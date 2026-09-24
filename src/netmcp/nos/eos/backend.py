"""Arista EOS implementation of the NOSBackend Protocol.

Implements EVPN (VLAN-based) read and write operations via gNMI using
OpenConfig and Arista experimental YANG models — no CLI origin.

An EOS EVPN instance is spread over four YANG objects:
  1. VLAN              /network-instances/network-instance[name=default]/vlans/vlan[vlan-id=N]
  2. VLAN -> VNI       /interfaces/interface[name=Vxlan1]/arista-vxlan/vlan-to-vnis/vlan-to-vni[vlan=N]
  3. BGP EVPN (RD/RT)  /arista/eos/evpn/evpn-instances/evpn-instance[name=...]   (router bgp / vlan N)
  4. Access trunk      /interfaces/interface[name=EthernetX]/ethernet/switched-vlan/config/trunk-vlans

All other domains fall through to NotImplementedBackend.
"""

import re

from pydantic import BaseModel, Field, ValidationError, field_validator

from netmcp.inventory import NodeInfo
from netmcp.nos.eos.client import gnmi_get, gnmi_set_batch
from netmcp.registry import NotImplementedBackend
from netmcp.utils.formatters import format_dry_run, format_node_results
from netmcp.utils.yang import ns_get, strip_prefix

VXLAN_IFACE = "Vxlan1"
VLANS_PATH = "/network-instances/network-instance[name=default]/vlans"
VXLAN_PATH = f"/interfaces/interface[name={VXLAN_IFACE}]/arista-vxlan"
EVPN_PATH = "/arista/eos/evpn/evpn-instances"


def _switched_vlan_path(interface_name: str) -> str:
    return f"/interfaces/interface[name={interface_name}]/ethernet/switched-vlan/config"


def _normalise_rt(rt: str) -> str:
    """EOS route-targets are "65000:10"; accept the SR-style "target:65000:10" too."""
    return rt.removeprefix("target:")


class _VlanEvpnIntent(BaseModel):
    service_name: str = Field(..., min_length=1)
    vni: int = Field(..., ge=1, le=16_777_215)
    interface_name: str
    vlan_id: int = Field(..., ge=2, le=4094)
    route_distinguisher: str  # e.g. "10:2"
    export_rt: str             # e.g. "65000:10" or "target:65000:10"
    import_rt: str

    @field_validator("interface_name")
    @classmethod
    def validate_interface_name(cls, v: str) -> str:
        if not re.match(r"^Ethernet\d+(/\d+)*$", v):
            raise ValueError(
                f"{v!r} is not a valid access port. "
                "Only Ethernet interfaces (e.g. Ethernet1) are permitted."
            )
        return v

    @field_validator("export_rt", "import_rt")
    @classmethod
    def validate_rt(cls, v: str) -> str:
        if not re.match(r"^[\d.]+:\d+$", _normalise_rt(v)):
            raise ValueError(f"{v!r} is not a valid route-target (e.g. 65000:10).")
        return v


# ----------------------------------------------------------------------
# Reply parsing
# ----------------------------------------------------------------------

def _entries(data, key: str) -> list:
    """Return the list entries named `key` from a GET reply.

    EOS may root the reply at the requested container or one level above it,
    so wrapping containers are descended until `key` is found.
    """
    if data is None:
        return []
    if isinstance(data, list):
        out = []
        for item in data:
            out.extend(_entries(item, key) or [item])
        return out
    found = ns_get(data, key)
    if found is not None:
        return found if isinstance(found, list) else [found]
    for v in data.values():
        if isinstance(v, dict):
            nested = _entries(v, key)
            if nested:
                return nested
    return []


def _leaf(entry: dict, name: str):
    """Read a leaf from an entry, looking at the entry itself then its config/state."""
    val = ns_get(entry, name)
    if val is None:
        val = ns_get(ns_get(entry, "config", {}) or {}, name)
    if val is None:
        val = ns_get(ns_get(entry, "state", {}) or {}, name)
    return val


def _vlans(node: NodeInfo) -> dict[int, dict]:
    """Map VLAN id -> OpenConfig VLAN entry."""
    return {int(_leaf(v, "vlan-id")): v for v in _entries(gnmi_get(node, VLANS_PATH), "vlan") if _leaf(v, "vlan-id") is not None}


def _vlan_vnis(node: NodeInfo) -> dict[int, int]:
    """Map VLAN id -> VNI from Vxlan1."""
    vnis = {}
    for m in _entries(gnmi_get(node, VXLAN_PATH), "vlan-to-vni"):
        vlan, vni = _leaf(m, "vlan"), _leaf(m, "vni")
        if vlan is not None and vni is not None:
            vnis[int(vlan)] = int(vni)
    return vnis


def _evpn_instances(node: NodeInfo) -> list[dict]:
    """Return VLAN / VLAN-aware-bundle evpn-instance entries."""
    instances = []
    for inst in _entries(gnmi_get(node, EVPN_PATH), "evpn-instance"):
        if strip_prefix(_leaf(inst, "instance-type") or "VLAN") == "VPWS":
            continue
        instances.append(inst)
    return instances


def _instance_vlan_ids(inst: dict) -> list[int]:
    vlans = ns_get(inst, "vlans", {}) or {}
    return [int(_leaf(v, "vlan-id")) for v in _entries(vlans, "vlan") if _leaf(v, "vlan-id") is not None]


def _vlan_name(vlan_entry: dict | None) -> str | None:
    return _leaf(vlan_entry, "name") if vlan_entry else None


def _collect(node: NodeInfo) -> list[dict]:
    """Join VLANs, VNIs and evpn-instances into one record per EVPN instance."""
    instances = _evpn_instances(node)
    if not instances:
        return []
    vlans = _vlans(node)
    vnis = _vlan_vnis(node)
    records = []
    for inst in instances:
        vlan_ids = _instance_vlan_ids(inst)
        vlan_id = vlan_ids[0] if vlan_ids else None
        records.append({
            "evpn_name": _leaf(inst, "name"),
            "vlan_id": vlan_id,
            "vlan_ids": vlan_ids,
            "vlan": vlans.get(vlan_id),
            "vni": vnis.get(vlan_id),
            "evpn": inst,
        })
    return records


def _find_instance(node: NodeInfo, instance_name: str) -> dict | None:
    """Match instance_name against the evpn-instance name, VLAN name or VLAN id."""
    for rec in _collect(node):
        candidates = {str(rec["evpn_name"]), str(rec["vlan_id"]), str(_vlan_name(rec["vlan"]))}
        if instance_name in candidates:
            return rec
    return None


def _trunk_config(node: NodeInfo, interface_name: str) -> dict | None:
    """Return the switched-vlan config container of an interface, or None if routed."""
    data = gnmi_get(node, _switched_vlan_path(interface_name))
    if data is None:
        return None
    if isinstance(data, dict) and ns_get(data, "config") is not None:
        data = ns_get(data, "config")
    return data


def _vlan_in_trunk_list(vlan_id: int, trunk_vlans: list) -> bool:
    """True if vlan_id is carried by an explicit trunk-vlans list (ints or "a..b" ranges)."""
    for item in trunk_vlans:
        if isinstance(item, int) and item == vlan_id:
            return True
        if isinstance(item, str):
            if item.isdigit() and int(item) == vlan_id:
                return True
            m = re.match(r"^(\d+)\.\.(\d+)$", item)
            if m and int(m.group(1)) <= vlan_id <= int(m.group(2)):
                return True
    return False


def _trunk_interfaces(node: NodeInfo, vlan_id: int) -> list[dict]:
    """Return [{name, trunk-vlans}] for interfaces whose trunk list names vlan_id explicitly."""
    result = []
    for iface in _entries(gnmi_get(node, "/interfaces"), "interface"):
        eth = ns_get(iface, "ethernet") or {}
        cfg = ns_get(ns_get(eth, "switched-vlan") or {}, "config") or {}
        trunk = ns_get(cfg, "trunk-vlans") or []
        if vlan_id in trunk or str(vlan_id) in trunk:
            result.append({"name": ns_get(iface, "name"), "trunk-vlans": trunk})
    return result


# ----------------------------------------------------------------------
# BGP
# ----------------------------------------------------------------------

BGP_PATH = (
    "/network-instances/network-instance[name=default]"
    "/protocols/protocol[identifier=BGP][name=BGP]/bgp"
)


def _active_afi_safis(nbr: dict) -> dict[str, dict]:
    """Map active AFI-SAFI name (e.g. "L2VPN_EVPN") -> {received, sent, installed}."""
    afis = {}
    for afi in _entries(ns_get(nbr, "afi-safis") or {}, "afi-safi"):
        state = ns_get(afi, "state") or {}
        if not ns_get(state, "active"):
            continue
        prefixes = ns_get(state, "prefixes") or {}
        afis[strip_prefix(_leaf(afi, "afi-safi-name"))] = {
            "received": ns_get(prefixes, "received"),
            "sent": ns_get(prefixes, "sent"),
            "installed": ns_get(prefixes, "installed"),
        }
    return afis


def _peer_summary(nbr: dict) -> dict:
    """Compact, vendor-neutral view of one OpenConfig BGP neighbor."""
    state = ns_get(nbr, "state") or {}
    return {
        "peer": _leaf(nbr, "neighbor-address"),
        "peer-as": _leaf(nbr, "peer-as"),
        "peer-group": _leaf(nbr, "peer-group"),
        "state": strip_prefix(ns_get(state, "session-state")),
        "established-transitions": ns_get(state, "established-transitions"),
        "last-established": ns_get(state, "last-established"),
        "afi-safis": _active_afi_safis(nbr),
    }


def _bgp_neighbors(node: NodeInfo) -> list[dict]:
    return _entries(gnmi_get(node, f"{BGP_PATH}/neighbors"), "neighbor")


class EOSBackend(NotImplementedBackend):
    """Arista EOS backend. Dispatched to by unified tools."""

    def __init__(self) -> None:
        super().__init__(nos_type="eos", transport="gnmi")

    # ------------------------------------------------------------------
    # BGP
    # ------------------------------------------------------------------

    def get_bgp_summary(self, node: NodeInfo) -> str:
        """Return BGP global state plus a one-line-per-peer summary for the default VRF."""
        glob = gnmi_get(node, f"{BGP_PATH}/global")
        if glob is None:
            return f"Error: could not retrieve BGP summary from {node.name} ({node.fqdn})"
        glob = ns_get(glob, "global", glob)
        state = ns_get(glob, "state") or {}
        peers = [_peer_summary(n) for n in _bgp_neighbors(node)]
        # EOS may omit total-paths/total-prefixes; only report what the device does
        totals = {
            k: ns_get(state, k) for k in ("total-paths", "total-prefixes")
            if ns_get(state, k) is not None
        }
        return format_node_results({node.name: {
            "as": _leaf(glob, "as"),
            "router-id": _leaf(glob, "router-id"),
            **totals,
            "peers": {
                "total": len(peers),
                "established": sum(p["state"] == "ESTABLISHED" for p in peers),
            },
            "neighbors": [
                {k: p[k] for k in ("peer", "peer-as", "state", "afi-safis")} for p in peers
            ],
        }})

    def get_bgp_neighbors(self, node: NodeInfo) -> str:
        """Return a compact view (state, AS, active AFI-SAFI prefix counts) of every BGP neighbor."""
        peers = [_peer_summary(n) for n in _bgp_neighbors(node)]
        if not peers:
            return f"No BGP neighbors found on {node.name} ({node.fqdn})"
        return format_node_results({node.name: peers})

    def get_bgp_neighbor(self, node: NodeInfo, peer_ip: str) -> str:
        """Return the full OpenConfig tree (config + state) for one BGP neighbor."""
        data = gnmi_get(node, f"{BGP_PATH}/neighbors/neighbor[neighbor-address={peer_ip}]")
        if data is None:
            return f"Error: could not retrieve BGP neighbor {peer_ip!r} from {node.name} ({node.fqdn})"
        return format_node_results({node.name: data})

    def get_bgp_config(self, node: NodeInfo) -> str:
        """Return the BGP configuration (global, neighbors, peer-groups) of the default VRF."""
        data = gnmi_get(node, BGP_PATH, datatype="config")
        if data is None:
            return f"Error: could not retrieve BGP config from {node.name} ({node.fqdn})"
        return format_node_results({node.name: data})

    # ------------------------------------------------------------------
    # EVPN — read
    # ------------------------------------------------------------------

    def get_evpn_instances(self, node: NodeInfo) -> str:
        """Return all VLAN-based EVPN instances normalised to {name, type, vni, evi}.

        EOS VLAN-based EVPN has no separate EVI; the VLAN id is reported as the EVI.
        """
        records = _collect(node)
        if not records:
            return f"No EVPN instances found on {node.name} ({node.fqdn})"
        instances = [{
            "name": _vlan_name(rec["vlan"]) or rec["evpn_name"],
            "type": strip_prefix(_leaf(rec["evpn"], "instance-type") or "VLAN").lower(),
            "vni": rec["vni"],
            "evi": rec["vlan_id"],
        } for rec in records]
        return format_node_results({node.name: instances})

    def get_evpn_instance(self, node: NodeInfo, instance_name: str) -> str:
        """Return the merged configuration (VLAN, VNI, RD/RT, trunk ports) of one EVPN instance."""
        rec = _find_instance(node, instance_name)
        if rec is None:
            return f"Service {instance_name!r} not found on {node.name} ({node.fqdn})"
        vlan_id = rec["vlan_id"]
        return format_node_results({node.name: {
            "name": _vlan_name(rec["vlan"]) or rec["evpn_name"],
            "vlan-id": vlan_id,
            "vni": rec["vni"],
            "evpn-instance": rec["evpn"],
            "vlan": rec["vlan"],
            "trunk-interfaces": _trunk_interfaces(node, vlan_id) if vlan_id is not None else [],
        }})

    def get_evpn_instance_state(self, node: NodeInfo, instance_name: str) -> str:
        """Return the operational state (VLAN status/members, Vxlan1 state) of one EVPN instance."""
        rec = _find_instance(node, instance_name)
        if rec is None:
            return f"Service {instance_name!r} state not found on {node.name} ({node.fqdn})"
        vlan = rec["vlan"] or {}
        vxlan = gnmi_get(node, VXLAN_PATH) or {}
        vxlan = ns_get(vxlan, "arista-vxlan", vxlan) if isinstance(vxlan, dict) else {}
        return format_node_results({node.name: {
            "name": _vlan_name(vlan) or rec["evpn_name"],
            "vlan-id": rec["vlan_id"],
            "vni": rec["vni"],
            "vlan-state": ns_get(vlan, "state"),
            "vlan-members": ns_get(vlan, "members"),
            "vxlan-state": ns_get(vxlan, "state"),
        }})

    # ------------------------------------------------------------------
    # EVPN — write
    # ------------------------------------------------------------------

    def provision_evpn_instance(
        self,
        node: NodeInfo,
        service_name: str,
        service_id: int,  # not used by EOS; present for Protocol compatibility
        vni: int,
        evi: int,         # not used by EOS; VLAN-based EVPN is keyed by vlan_id
        route_distinguisher: str,
        export_rt: str,
        import_rt: str,
        dry_run: bool,
        interface_name: str = "",
        vlan_id: int = 0,
    ) -> str:
        """Create a VLAN-based EVPN instance on an EOS node in one atomic gNMI SET.

        Requires interface_name (e.g. "Ethernet1", must be a trunk switchport) and
        vlan_id. Objects written:
          1. VLAN with name = service_name
          2. Vxlan1 VLAN-to-VNI mapping
          3. evpn-instance (router bgp / vlan N) with RD, RTs, redistribute learned
          4. VLAN added to the access port's trunk list, unless the trunk already
             carries it (an empty list means all VLANs are allowed)
        """
        if not interface_name or vlan_id == 0:
            return (
                "Error: EOS EVPN provisioning requires 'interface_name' "
                "(e.g. 'Ethernet1') and 'vlan_id' (2-4094)."
            )

        try:
            _VlanEvpnIntent(
                service_name=service_name,
                vni=vni,
                interface_name=interface_name,
                vlan_id=vlan_id,
                route_distinguisher=route_distinguisher,
                export_rt=export_rt,
                import_rt=import_rt,
            )
        except ValidationError as e:
            messages = [f"  - {err['loc'][0] if err['loc'] else 'unknown'}: {err['msg']}" for err in e.errors()]
            return "Validation error(s):\n" + "\n".join(messages)

        trunk_cfg = _trunk_config(node, interface_name)
        mode = strip_prefix(ns_get(trunk_cfg, "interface-mode")) if trunk_cfg else None
        if mode != "TRUNK":
            return (
                f"Error: {interface_name} on {node.name} is not a trunk switchport "
                f"(interface-mode={mode}). Uplinks and routed ports cannot be used."
            )
        if vlan_id in _vlans(node):
            return f"Error: VLAN {vlan_id} already exists on {node.name}."

        evpn_name = str(vlan_id)
        vlan_path = f"{VLANS_PATH}/vlan[vlan-id={vlan_id}]"
        vlan_value = {"vlan-id": vlan_id, "config": {"vlan-id": vlan_id, "name": service_name}}

        vni_path = f"{VXLAN_PATH}/vlan-to-vnis/vlan-to-vni[vlan={vlan_id}]"
        vni_value = {"vlan": vlan_id, "config": {"vlan": vlan_id, "vni": vni}}

        evpn_path = f"{EVPN_PATH}/evpn-instance[name={evpn_name}]"
        evpn_value = {
            "name": evpn_name,
            "config": {
                "name": evpn_name,
                "instance-type": "VLAN",
                "route-distinguisher": route_distinguisher,
                "redistribute": ["LEARNED"],
            },
            "route-target": {"config": {
                "import": [_normalise_rt(import_rt)],
                "export": [_normalise_rt(export_rt)],
            }},
            "vlans": {"vlan": [{"vlan-id": vlan_id, "config": {"vlan-id": vlan_id}}]},
        }

        updates = [(vlan_path, vlan_value), (vni_path, vni_value), (evpn_path, evpn_value)]

        trunk_vlans = ns_get(trunk_cfg, "trunk-vlans") or []
        if trunk_vlans and not _vlan_in_trunk_list(vlan_id, trunk_vlans):
            trunk_path = _switched_vlan_path(interface_name)
            updates.append((trunk_path, {"trunk-vlans": [*trunk_vlans, vlan_id]}))

        if dry_run:
            return "\n\n".join(format_dry_run(node.name, p, v) for p, v in updates)

        try:
            gnmi_set_batch(node, updates=updates)
        except RuntimeError as e:
            return f"Error: provisioning failed on {node.name}; no changes were applied. {e}"

        return (
            f"OK: EVPN instance {service_name!r} (VLAN={vlan_id}, VNI={vni}, "
            f"iface={interface_name}, RD={route_distinguisher}, "
            f"export-RT={_normalise_rt(export_rt)}, import-RT={_normalise_rt(import_rt)}) "
            f"provisioned on {node.name}."
        )

    def delete_evpn_instance(self, node: NodeInfo, instance_name: str, dry_run: bool) -> str:
        """Delete an EVPN instance, its VNI mapping and VLAN in one atomic gNMI SET.

        Also removes the VLAN from any trunk list that names it explicitly. Trunks
        where it is the only listed VLAN are left alone, since an empty list
        would allow all VLANs.
        """
        rec = _find_instance(node, instance_name)
        if rec is None:
            return f"Service {instance_name!r} not found on {node.name} ({node.fqdn})"

        vlan_id = rec["vlan_id"]
        deletes = [f"{EVPN_PATH}/evpn-instance[name={rec['evpn_name']}]"]
        updates = []
        skipped = []
        if vlan_id is not None:
            if rec["vni"] is not None:
                deletes.append(f"{VXLAN_PATH}/vlan-to-vnis/vlan-to-vni[vlan={vlan_id}]")
            deletes.append(f"{VLANS_PATH}/vlan[vlan-id={vlan_id}]")
            for trunk in _trunk_interfaces(node, vlan_id):
                remaining = [v for v in trunk["trunk-vlans"] if v not in (vlan_id, str(vlan_id))]
                if not remaining:
                    skipped.append(trunk["name"])
                    continue
                cfg_path = _switched_vlan_path(trunk["name"])
                deletes.append(f"{cfg_path}/trunk-vlans")
                updates.append((cfg_path, {"trunk-vlans": remaining}))

        note = (
            f" VLAN {vlan_id} left in trunk list of {', '.join(skipped)} (only VLAN listed)."
            if skipped else ""
        )

        if dry_run:
            blocks = [
                f"[DRY RUN] Node: {node.name}\n  Operation: DELETE\n  Path:      {p}" for p in deletes
            ] + [format_dry_run(node.name, p, v) for p, v in updates]
            return "\n\n".join(blocks) + (f"\n\nNote:{note}" if note else "")

        try:
            gnmi_set_batch(node, updates=updates, deletes=deletes)
        except RuntimeError as e:
            return f"Error: deletion failed on {node.name}; no changes were applied. {e}"
        return f"OK: EVPN instance {instance_name!r} (VLAN={vlan_id}) deleted from {node.name}.{note}"
