"""NVIDIA Cumulus Linux implementation of the NOSBackend Protocol.

Implements the BGP reads over the NVUE REST API (NVIDIA's OpenAPI 3 model):
  - State: `vrf/default/router/bgp/neighbor` with rev=operational.
  - Config: `router/bgp` (global AS, router-id) and `vrf/default/router/bgp`
    (neighbors, address families) with rev=applied.

Summary and neighbors use the same compact per-peer view as the other NOSes:
upper-cased session state and OpenConfig-style AFI-SAFI names (L2VPN_EVPN).
NVUE's operational neighbor lists only the active address families and reports
no installed-prefix count. Config replies carry peer password hashes, which are
redacted.

EVPN (VLAN-based, reads and writes): one instance is a VLAN in bridge br_default
mapped to a VNI (`bridge/domain/br_default/vlan/N/vni/VNI`), the EVPN VNI with its
RD and route-targets (`evpn/vni/VNI`), and tagged membership of access ports
(`interface/swpN/bridge/domain/br_default/vlan/N`). NVUE has no VLAN name, so an
instance is named `vlanN` and can be looked up by that name, the VLAN id or the
VNI; the VLAN id is reported as the EVI. A write is one NVUE revision, applied
all-or-nothing.

All other domains fall through to NotImplementedBackend.
"""

import ipaddress
import re

from pydantic import BaseModel, Field, ValidationError, field_validator

from netmcp.inventory import NodeInfo
from netmcp.nos.cumulus.client import nvue_apply, nvue_get
from netmcp.registry import NotImplementedBackend
from netmcp.utils.formatters import format_dry_run, format_node_results

VRF = "default"
GLOBAL_BGP_PATH = "router/bgp"
VRF_BGP_PATH = f"vrf/{VRF}/router/bgp"
NEIGHBOR_PATH = f"{VRF_BGP_PATH}/neighbor"

# NVUE address-family name -> OpenConfig AFI-SAFI identity
_AFI_NAMES = {
    "ipv4-unicast": "IPV4_UNICAST",
    "ipv6-unicast": "IPV6_UNICAST",
    "l2vpn-evpn": "L2VPN_EVPN",
}


def _upper(val):
    return val.upper() if isinstance(val, str) else val


def _afi_safis(nbr: dict) -> dict[str, dict]:
    """Map the neighbor's active address families to {received, sent}."""
    return {
        _AFI_NAMES.get(name, name.upper().replace("-", "_")): {
            "received": af.get("rx-prefix"),
            "sent": af.get("tx-prefix"),
        }
        for name, af in (nbr.get("address-family") or {}).items()
        if isinstance(af, dict)
    }


def _peer_summary(peer: str, nbr: dict) -> dict:
    """Compact, vendor-neutral view of one NVUE operational neighbor."""
    return {
        "peer": peer,
        "peer-as": nbr.get("remote-as"),
        "state": _upper(nbr.get("session-state")),
        "uptime": nbr.get("uptime"),
        "last-reset-reason": nbr.get("last-reset-reason"),
        "afi-safis": _afi_safis(nbr),
    }


def _redact(data):
    """Replace password leaves (NVUE returns them as $nvsec$ hashes) with "<redacted>"."""
    if isinstance(data, dict):
        return {k: "<redacted>" if k == "password" else _redact(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_redact(v) for v in data]
    return data


def _bgp_peers(node: NodeInfo) -> list[dict]:
    neighbors = nvue_get(node, NEIGHBOR_PATH) or {}
    return [_peer_summary(peer, nbr) for peer, nbr in neighbors.items() if isinstance(nbr, dict)]


def _valid_ip(peer_ip: str) -> bool:
    try:
        ipaddress.ip_address(peer_ip)
    except ValueError:
        return False
    return True


# ----------------------------------------------------------------------
# EVPN (VLAN-based, bridge br_default)
# ----------------------------------------------------------------------

BRIDGE = "br_default"
BRIDGE_PATH = f"bridge/domain/{BRIDGE}"
EVPN_PATH = "evpn"
NVE_PATH = "nve/vxlan"
INTERFACES_PATH = "interface"
# NVUE reserves 3725-3999 (internal) and 4000-4064 (L3-VNI VLANs) by default
VLAN_MIN, VLAN_MAX = 2, 3724
_INTERFACE_RE = re.compile(r"^swp\d+(s\d+)?$")
_ASN_OR_IP = r"(\d+|\d{1,3}(\.\d{1,3}){3})"
_RD_RE = re.compile(rf"^{_ASN_OR_IP}:\d+$")
_RT_RE = re.compile(rf"^{_ASN_OR_IP}:\d+$")


def _strip_target(rt: str) -> str:
    rt = rt.strip()
    return rt[len("target:"):] if rt.lower().startswith("target:") else rt


def _int(val):
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _vlan_vni(vlan: dict) -> int | None:
    """The VNI a bridge VLAN maps to, or None."""
    return next((_int(k) for k in (vlan or {}).get("vni") or {}), None)


def _port_vlans(interface: dict) -> set[int] | None:
    """Tagged VLANs of a br_default member port, or None if it isn't a member."""
    member = (((interface or {}).get("bridge") or {}).get("domain") or {}).get(BRIDGE)
    if member is None:
        return None
    return {v for v in (_int(k) for k in member.get("vlan") or {}) if v is not None}


def _route_targets(evpn_vni: dict, direction: str) -> list[str]:
    """Import or export RTs: entries under `both` plus those under `direction`."""
    rt = (evpn_vni or {}).get("route-target") or {}
    return [*(rt.get("both") or {}), *(rt.get(direction) or {})]


def _collect(node: NodeInfo, ports: bool = False) -> tuple[list[dict], dict]:
    """Return ([{vlan_id, vni, vlan, evpn, ports}], interfaces) for VLANs that map to a VNI."""
    bridge = nvue_get(node, BRIDGE_PATH, rev="applied") or {}
    evpn_vnis = (nvue_get(node, EVPN_PATH, rev="applied") or {}).get("vni") or {}
    interfaces = (nvue_get(node, INTERFACES_PATH, rev="applied") or {}) if ports else {}
    records = []
    for vid, vlan in (bridge.get("vlan") or {}).items():
        vlan_id, vni = _int(vid), _vlan_vni(vlan)
        if vlan_id is None or vni is None:
            continue
        records.append({
            "vlan_id": vlan_id,
            "vni": vni,
            "vlan": vlan,
            "evpn": evpn_vnis.get(str(vni)),
            "ports": sorted(n for n, i in interfaces.items() if vlan_id in (_port_vlans(i) or set())),
        })
    return sorted(records, key=lambda r: r["vlan_id"]), interfaces


def _instance_name(rec: dict) -> str:
    return f"vlan{rec['vlan_id']}"


def _find_instance(records: list[dict], name: str) -> dict | None:
    """Match `vlanN`, the VLAN id or the VNI."""
    key = name.strip().lower()
    for rec in records:
        if key in (_instance_name(rec), str(rec["vlan_id"]), str(rec["vni"])):
            return rec
    return None


class _EvpnIntent(BaseModel):
    vni: int = Field(..., ge=1, le=16_777_214)
    vlan_id: int = Field(..., ge=VLAN_MIN, le=VLAN_MAX)
    interface_name: str
    route_distinguisher: str  # "10:7", "192.1.1.7:10" or "auto"
    export_rt: str             # "65000:10" or "target:65000:10"
    import_rt: str

    @field_validator("interface_name")
    @classmethod
    def validate_interface_name(cls, v: str) -> str:
        if not _INTERFACE_RE.match(v):
            raise ValueError(f"{v!r} is not a valid access port. Only switch ports (e.g. swp1) are permitted.")
        return v

    @field_validator("route_distinguisher")
    @classmethod
    def validate_rd(cls, v: str) -> str:
        if v.strip().lower() != "auto" and not _RD_RE.match(v.strip()):
            raise ValueError(f"{v!r} is not a valid route-distinguisher (e.g. 10:7, 192.1.1.7:10 or auto).")
        return v.strip().lower() if v.strip().lower() == "auto" else v.strip()

    @field_validator("export_rt", "import_rt")
    @classmethod
    def validate_rt(cls, v: str) -> str:
        if v.strip().lower() == "auto":
            raise ValueError("route-target must be explicit (e.g. 65000:10).")
        if not _RT_RE.match(_strip_target(v)):
            raise ValueError(f"{v!r} is not a valid route-target (e.g. 65000:10 or target:65000:10).")
        return _strip_target(v)


def _provision_tree(intent: _EvpnIntent) -> dict:
    """NVUE root PATCH that creates the VLAN→VNI mapping, the EVPN VNI and the port membership."""
    if intent.import_rt == intent.export_rt:
        route_target = {"both": {intent.import_rt: {}}}
    else:
        route_target = {"import": {intent.import_rt: {}}, "export": {intent.export_rt: {}}}
    vlan = str(intent.vlan_id)
    return {
        "bridge": {"domain": {BRIDGE: {"vlan": {vlan: {"vni": {str(intent.vni): {}}}}}}},
        "evpn": {"vni": {str(intent.vni): {"rd": intent.route_distinguisher, "route-target": route_target}}},
        "interface": {intent.interface_name: {"bridge": {"domain": {BRIDGE: {"vlan": {vlan: {}}}}}}},
    }


def _delete_tree(rec: dict, ports: list[str]) -> dict:
    """NVUE root PATCH (nulls unset) that removes the VLAN, the EVPN VNI and the VLAN from `ports`."""
    vlan = str(rec["vlan_id"])
    tree = {
        "bridge": {"domain": {BRIDGE: {"vlan": {vlan: None}}}},
        "evpn": {"vni": {str(rec["vni"]): None}},
    }
    if ports:
        tree["interface"] = {p: {"bridge": {"domain": {BRIDGE: {"vlan": {vlan: None}}}}} for p in ports}
    return tree


def _prerequisite_errors(node: NodeInfo) -> list[str]:
    """Missing bridge / VXLAN / EVPN setup that netmcp never creates."""
    bridge = nvue_get(node, BRIDGE_PATH, rev="applied")
    nve = nvue_get(node, NVE_PATH, rev="applied") or {}
    evpn = nvue_get(node, EVPN_PATH, rev="applied") or {}
    errors = []
    if bridge is None:
        errors.append(f"bridge {BRIDGE} does not exist")
    elif bridge.get("type") != "vlan-aware":
        errors.append(f"bridge {BRIDGE} is not vlan-aware (type={bridge.get('type')})")
    if nve.get("state") != "enabled" or not (nve.get("source") or {}).get("address"):
        errors.append("NVE VXLAN is not enabled with a source address (nve vxlan state / source address)")
    if evpn.get("state") != "enabled":
        errors.append("EVPN is not enabled (evpn state)")
    return errors


def _dry_run(node: str, tree: dict) -> str:
    return format_dry_run(node, "PATCH /nvue_v1/?rev=<new revision>, then apply", tree)


class CumulusBackend(NotImplementedBackend):
    """NVIDIA Cumulus Linux backend: BGP reads over NVUE REST."""

    def __init__(self) -> None:
        super().__init__(nos_type="cumulus", transport="rest")

    # ------------------------------------------------------------------
    # BGP
    # ------------------------------------------------------------------

    def get_bgp_summary(self, node: NodeInfo) -> str:
        """Return the BGP AS and router-id plus a one-line-per-peer summary for the default VRF."""
        try:
            glob = nvue_get(node, GLOBAL_BGP_PATH, rev="applied")
            peers = _bgp_peers(node)
        except RuntimeError as e:
            return f"Error: could not retrieve BGP summary from {node.name} ({node.fqdn}): {e}"
        if not glob:
            return f"Error: could not retrieve BGP summary from {node.name} ({node.fqdn})"
        return format_node_results({node.name: {
            "as": glob.get("autonomous-system"),
            "router-id": glob.get("router-id"),
            "peers": {
                "total": len(peers),
                "established": sum(p["state"] == "ESTABLISHED" for p in peers),
            },
            "neighbors": [
                {k: p[k] for k in ("peer", "peer-as", "state", "afi-safis")} for p in peers
            ],
        }})

    def get_bgp_neighbors(self, node: NodeInfo) -> str:
        """Return a compact view (state, AS, enabled AFI-SAFI prefix counts) of every BGP neighbor."""
        try:
            peers = _bgp_peers(node)
        except RuntimeError as e:
            return f"Error: could not retrieve BGP neighbors from {node.name} ({node.fqdn}): {e}"
        if not peers:
            return f"No BGP neighbors found on {node.name} ({node.fqdn})"
        return format_node_results({node.name: peers})

    def get_bgp_neighbor(self, node: NodeInfo, peer_ip: str) -> str:
        """Return the full NVUE operational object for one BGP neighbor."""
        if not _valid_ip(peer_ip):
            return f"Error: {peer_ip!r} is not a valid IP address"
        try:
            data = nvue_get(node, f"{NEIGHBOR_PATH}/{peer_ip}")
        except RuntimeError as e:
            return f"Error: could not retrieve BGP neighbor {peer_ip!r} from {node.name} ({node.fqdn}): {e}"
        if not data:
            return f"Error: could not retrieve BGP neighbor {peer_ip!r} from {node.name} ({node.fqdn})"
        return format_node_results({node.name: data})

    def get_bgp_config(self, node: NodeInfo) -> str:
        """Return the applied BGP config: global (AS, router-id) and the default VRF (neighbors, AFIs).

        Peer passwords are redacted.
        """
        try:
            glob = nvue_get(node, GLOBAL_BGP_PATH, rev="applied")
            vrf = nvue_get(node, VRF_BGP_PATH, rev="applied")
        except RuntimeError as e:
            return f"Error: could not retrieve BGP config from {node.name} ({node.fqdn}): {e}"
        if glob is None and vrf is None:
            return f"Error: could not retrieve BGP config from {node.name} ({node.fqdn})"
        return format_node_results({node.name: _redact({
            "router": {"bgp": glob},
            "vrf": {VRF: {"router": {"bgp": vrf}}},
        })})

    # ------------------------------------------------------------------
    # EVPN — read
    # ------------------------------------------------------------------

    def get_evpn_instances(self, node: NodeInfo) -> str:
        """Return all VLAN-based EVPN instances normalised to {name, type, vni, evi}.

        Cumulus VLAN-based EVPN has no separate EVI; the VLAN id is reported as the EVI.
        """
        try:
            records, _ = _collect(node)
        except RuntimeError as e:
            return f"Error: could not retrieve EVPN instances from {node.name} ({node.fqdn}): {e}"
        if not records:
            return f"No EVPN instances found on {node.name} ({node.fqdn})"
        return format_node_results({node.name: [
            {"name": _instance_name(r), "type": "vlan", "vni": r["vni"], "evi": r["vlan_id"]}
            for r in records
        ]})

    def get_evpn_instance(self, node: NodeInfo, instance_name: str) -> str:
        """Return the configuration (VLAN→VNI, RD/RT, access ports) of one EVPN instance."""
        try:
            records, _ = _collect(node, ports=True)
        except RuntimeError as e:
            return f"Error: could not retrieve EVPN instance from {node.name} ({node.fqdn}): {e}"
        rec = _find_instance(records, instance_name)
        if rec is None:
            return f"Service {instance_name!r} not found on {node.name} ({node.fqdn})"
        evpn = rec["evpn"] or {}
        return format_node_results({node.name: {
            "name": _instance_name(rec),
            "vlan-id": rec["vlan_id"],
            "vni": rec["vni"],
            "route-distinguisher": evpn.get("rd"),
            "import-rt": _route_targets(evpn, "import"),
            "export-rt": _route_targets(evpn, "export"),
            "bridge": BRIDGE,
            "access-interfaces": rec["ports"],
            "vlan": rec["vlan"],
            "evpn": rec["evpn"],
        }})

    def get_evpn_instance_state(self, node: NodeInfo, instance_name: str) -> str:
        """Return the operational state (VNI, remote VTEPs, EVPN MACs) of one EVPN instance."""
        try:
            records, _ = _collect(node, ports=True)
            rec = _find_instance(records, instance_name)
            if rec is None:
                return f"Service {instance_name!r} state not found on {node.name} ({node.fqdn})"
            vni = rec["vni"]
            oper = nvue_get(node, f"{EVPN_PATH}/vni/{vni}") or {}
            macs = nvue_get(node, f"{EVPN_PATH}/vni/{vni}/mac") or {}
        except RuntimeError as e:
            return f"Error: could not retrieve EVPN state from {node.name} ({node.fqdn}): {e}"
        return format_node_results({node.name: {
            "name": _instance_name(rec),
            "vlan-id": rec["vlan_id"],
            "vni": vni,
            "access-interfaces": rec["ports"],
            "vni-state": {
                k: oper.get(k) for k in (
                    "bridge-domain", "vxlan-interface", "local-vtep", "tenant-vrf",
                    "mac-count", "host-count", "remote-vtep-count",
                )
            } if oper else None,
            "vteps": [
                {"peer": peer, "flood": (v or {}).get("flood")}
                for peer, v in (oper.get("remote-vtep") or {}).items()
            ],
            "macs": [
                {"mac": mac, "learned": (m or {}).get("type"), "vtep": (m or {}).get("remote-vtep")}
                for mac, m in macs.items()
            ],
        }})

    # ------------------------------------------------------------------
    # EVPN — write
    # ------------------------------------------------------------------

    def provision_evpn_instance(
        self,
        node: NodeInfo,
        service_name: str,  # not used: NVUE VLANs have no name; the instance is named vlanN
        service_id: int,    # not used by Cumulus; present for Protocol compatibility
        vni: int,
        evi: int,           # not used: VLAN-based EVPN is keyed by vlan_id
        route_distinguisher: str,
        export_rt: str,
        import_rt: str,
        dry_run: bool,
        interface_name: str = "",
        vlan_id: int = 0,
    ) -> str:
        """Create a VLAN-based EVPN instance in bridge br_default as one NVUE revision.

        Requires interface_name (a br_default member port, e.g. "swp1") and vlan_id.
        Objects written:
          1. VLAN vlan_id in br_default mapped to vni
          2. evpn vni with RD (or auto) and route-targets (`both` when import == export)
          3. vlan_id added to the port's tagged VLANs (untagged is never changed)
        """
        if not interface_name or vlan_id == 0:
            return (
                "Error: Cumulus EVPN provisioning requires 'interface_name' "
                f"(a {BRIDGE} member port, e.g. 'swp1') and 'vlan_id' ({VLAN_MIN}-{VLAN_MAX})."
            )
        try:
            intent = _EvpnIntent(
                vni=vni,
                vlan_id=vlan_id,
                interface_name=interface_name,
                route_distinguisher=route_distinguisher,
                export_rt=export_rt,
                import_rt=import_rt,
            )
        except ValidationError as e:
            messages = [f"  - {err['loc'][0] if err['loc'] else 'unknown'}: {err['msg']}" for err in e.errors()]
            return "Validation error(s):\n" + "\n".join(messages)

        try:
            missing = _prerequisite_errors(node)
            if missing:
                return f"Error: {node.name} is not ready for EVPN: " + "; ".join(missing) + "."
            records, interfaces = _collect(node, ports=True)
            evpn_vnis = (nvue_get(node, EVPN_PATH, rev="applied") or {}).get("vni") or {}
            bridge_vlans = (nvue_get(node, BRIDGE_PATH, rev="applied") or {}).get("vlan") or {}
        except RuntimeError as e:
            return f"Error: could not read the current config of {node.name}: {e}"
        if str(vlan_id) in bridge_vlans:
            return f"Error: VLAN {vlan_id} already exists in {BRIDGE} on {node.name}."
        if any(r["vni"] == vni for r in records) or str(vni) in evpn_vnis:
            return f"Error: VNI {vni} is already in use on {node.name}."
        if interface_name not in interfaces:
            return f"Error: interface {interface_name} not found on {node.name}."
        if _port_vlans(interfaces[interface_name]) is None:
            return (
                f"Error: {interface_name} on {node.name} is not a member of bridge {BRIDGE}. "
                "Uplinks and routed ports cannot be used."
            )

        tree = _provision_tree(intent)
        if dry_run:
            return _dry_run(node.name, tree)
        try:
            nvue_apply(node, tree)
        except RuntimeError as e:
            return f"Error: provisioning failed on {node.name}; no changes were applied. {e}"
        return (
            f"OK: EVPN instance 'vlan{vlan_id}' (VLAN={vlan_id}, VNI={vni}, iface={interface_name}, "
            f"RD={intent.route_distinguisher}, export-RT={intent.export_rt}, import-RT={intent.import_rt}) "
            f"provisioned on {node.name}."
        )

    def delete_evpn_instance(self, node: NodeInfo, instance_name: str, dry_run: bool) -> str:
        """Delete an EVPN instance (VLAN→VNI, evpn vni, port memberships) as one NVUE revision.

        The VLAN is removed from every br_default port that carries it, except ports
        where it is the only VLAN; those are left alone and reported.
        """
        try:
            records, interfaces = _collect(node, ports=True)
        except RuntimeError as e:
            return f"Error: could not read the current config of {node.name}: {e}"
        rec = _find_instance(records, instance_name)
        if rec is None:
            return f"Service {instance_name!r} not found on {node.name} ({node.fqdn})"
        skipped = [p for p in rec["ports"] if _port_vlans(interfaces[p]) == {rec["vlan_id"]}]
        ports = [p for p in rec["ports"] if p not in skipped]
        tree = _delete_tree(rec, ports)
        note = (
            f" VLAN {rec['vlan_id']} left on {', '.join(skipped)} (only VLAN on the port)."
            if skipped else ""
        )
        if dry_run:
            return _dry_run(node.name, tree) + (f"\n\nNote:{note}" if note else "")
        try:
            nvue_apply(node, tree)
        except RuntimeError as e:
            return f"Error: deletion failed on {node.name}; no changes were applied. {e}"
        return (
            f"OK: EVPN instance {_instance_name(rec)!r} (VLAN={rec['vlan_id']}, VNI={rec['vni']}) "
            f"deleted from {node.name}.{note}"
        )
