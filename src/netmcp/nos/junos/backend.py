"""Juniper Junos (Evolved) implementation of the NOSBackend Protocol.

Implements BGP reads and EVPN reads/writes via gNMI, YANG only (no CLI origin):
  - State: OpenConfig over Subscribe ONCE, since Junos Get serves config only.
    The default instance and the BGP protocol are both keyed "DEFAULT".
  - Config: native Junos YANG (junos-conf-*) over Get/Set, under the `juniper` origin.
    The OpenConfig config tree is empty unless the node was configured through it.

An EVPN instance is a vlan-based `mac-vrf` routing-instance (EVPN-VXLAN) plus the
access unit it bridges, e.g. et-0/0/2.10 (encapsulation vlan-bridge). Both are
written or deleted in one SetRequest, which Junos applies as a single commit.

All other domains fall through to NotImplementedBackend.
"""

import re

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from netmcp.inventory import NodeInfo
from netmcp.nos.junos.client import gnmi_get_config, gnmi_set_batch, gnmi_subscribe_once
from netmcp.registry import NotImplementedBackend
from netmcp.utils.formatters import format_dry_run, format_node_results
from netmcp.utils.openconfig import entries, leaf, peer_summary

BGP_PATH = (
    "/network-instances/network-instance[name=DEFAULT]"
    "/protocols/protocol[identifier=BGP][name=DEFAULT]/bgp"
)
BGP_CONFIG_PATH = "juniper:/configuration/protocols/bgp"
ROUTING_OPTIONS_PATH = "juniper:/configuration/routing-options"


def _bgp_neighbors(node: NodeInfo) -> list[dict]:
    return entries(gnmi_subscribe_once(node, f"{BGP_PATH}/neighbors"), "neighbor")


# ----------------------------------------------------------------------
# EVPN (mac-vrf, vlan-based, VXLAN)
# ----------------------------------------------------------------------

ROUTING_INSTANCES_PATH = "juniper:/configuration/routing-instances"
INTERFACES_PATH = "juniper:/configuration/interfaces"
VTEP_SOURCE_INTERFACE = "lo0.0"
# Parent-port encapsulations that accept vlan-bridge units
_BRIDGE_ENCAPSULATIONS = ("flexible-ethernet-services", "extended-vlan-bridge")


def _ri_path(name: str) -> str:
    return f"{ROUTING_INSTANCES_PATH}/instance[name={name}]"


def _unit_path(interface: str, unit: int | str) -> str:
    return f"{INTERFACES_PATH}/interface[name={interface}]/unit[name={unit}]"


def _ni_state_path(name: str) -> str:
    return f"/network-instances/network-instance[name={name}]"


def _config(node: NodeInfo, path: str):
    """Get one native config path; None when the node has nothing there."""
    return gnmi_get_config(node, [path]).get(path.removeprefix("juniper:/"))


def _items(data: dict | None, key: str) -> list:
    """Return a YANG list from a Junos reply as a Python list (a single entry may come unwrapped)."""
    val = (data or {}).get(key) or []
    return val if isinstance(val, list) else [val]


def _routing_instances(node: NodeInfo) -> list[dict]:
    return _items(_config(node, ROUTING_INSTANCES_PATH), "instance")


def _mac_vrfs(node: NodeInfo) -> list[dict]:
    return [i for i in _routing_instances(node) if i.get("instance-type") == "mac-vrf"]


def _vlans(inst: dict) -> list[dict]:
    return _items(inst.get("vlans"), "vlan")


def _first_vlan(inst: dict) -> dict:
    vlans = _vlans(inst)
    return vlans[0] if vlans else {}


def _vni(vlan: dict):
    return (vlan.get("vxlan") or {}).get("vni")


def _find_instance(node: NodeInfo, instance_name: str) -> dict | None:
    """Match instance_name against the routing-instance name, then its VLAN names and ids."""
    instances = _mac_vrfs(node)
    for inst in instances:
        if inst.get("name") == instance_name:
            return inst
    for inst in instances:
        for vlan in _vlans(inst):
            if instance_name in (str(vlan.get("name")), str(vlan.get("vlan-id"))):
                return inst
    return None


def _split_ifl(ifl: str) -> tuple[str, str]:
    """Split "et-0/0/2.10" into ("et-0/0/2", "10"); a bare port gets unit ""."""
    port, _, unit = ifl.partition(".")
    return port, unit


def _instance_ifls(inst: dict) -> list[str]:
    """Logical interfaces attached to the instance or any of its VLANs, in order."""
    names = [i.get("name") for i in _items(inst, "interface")]
    for vlan in _vlans(inst):
        names += [i.get("name") for i in _items(vlan, "interface")]
    return list(dict.fromkeys(n for n in names if n))


def _find_interface(interfaces: list[dict], name: str) -> dict | None:
    return next((i for i in interfaces if i.get("name") == name), None)


def _find_unit(interfaces: list[dict], ifl: str) -> dict | None:
    port, unit = _split_ifl(ifl)
    iface = _find_interface(interfaces, port)
    return next((u for u in _items(iface, "unit") if str(u.get("name")) == unit), None)


def _normalise_rt(rt: str) -> str:
    """Junos communities are "target:65000:10"; accept the bare "65000:10" too."""
    return rt if rt.startswith("target:") else f"target:{rt}"


def _strip_rt(rt: str) -> str:
    """OpenConfig state reports "route-target:65000:10 " -> "target:65000:10"."""
    return "target:" + rt.strip().removeprefix("route-target:")


class _MacVrfIntent(BaseModel):
    service_name: str = Field(..., pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
    vni: int = Field(..., ge=1, le=16_777_215)
    interface_name: str
    vlan_id: int = Field(..., ge=1, le=4094)
    route_distinguisher: str = Field(..., pattern=r"^[\w.]+:\d+$")  # e.g. "10:3"
    export_rt: str  # e.g. "target:65000:10" or "65000:10"
    import_rt: str

    @field_validator("interface_name")
    @classmethod
    def validate_interface_name(cls, v: str) -> str:
        if not re.match(r"^(?:(?:et|xe|ge|mge)-\d+/\d+/\d+(?::\d+)?|ae\d+)(?:\.\d+)?$", v):
            raise ValueError(
                f"{v!r} is not a valid access port. "
                "Use a physical or aggregated port, optionally with a unit (e.g. et-0/0/2 or et-0/0/2.20)."
            )
        return v

    @field_validator("export_rt", "import_rt")
    @classmethod
    def validate_rt(cls, v: str) -> str:
        if not re.match(r"^target:[\d.]+:\d+$", _normalise_rt(v)):
            raise ValueError(f"{v!r} is not a valid route-target (e.g. target:65000:10).")
        return v

    @model_validator(mode="after")
    def same_rt(self) -> "_MacVrfIntent":
        if _normalise_rt(self.export_rt) != _normalise_rt(self.import_rt):
            raise ValueError(
                "Junos mac-vrf uses one vrf-target community for import and export; "
                "export_rt and import_rt must be equal."
            )
        return self


class JunOSBackend(NotImplementedBackend):
    """Juniper Junos backend. Dispatched to by unified tools."""

    def __init__(self) -> None:
        super().__init__(nos_type="junos", transport="gnmi")

    # ------------------------------------------------------------------
    # BGP
    # ------------------------------------------------------------------

    def get_bgp_summary(self, node: NodeInfo) -> str:
        """Return BGP global state plus a one-line-per-peer summary for the default instance."""
        glob = gnmi_subscribe_once(node, f"{BGP_PATH}/global")
        if not glob:
            return f"Error: could not retrieve BGP summary from {node.name} ({node.fqdn})"
        state = glob.get("state") or {}
        peers = [peer_summary(n) for n in _bgp_neighbors(node)]
        return format_node_results({node.name: {
            "as": leaf(glob, "as"),
            "router-id": leaf(glob, "router-id"),
            "total-paths": state.get("total-paths"),
            "total-prefixes": state.get("total-prefixes"),
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
        peers = [peer_summary(n) for n in _bgp_neighbors(node)]
        if not peers:
            return f"No BGP neighbors found on {node.name} ({node.fqdn})"
        return format_node_results({node.name: peers})

    def get_bgp_neighbor(self, node: NodeInfo, peer_ip: str) -> str:
        """Return the full OpenConfig state tree for one BGP neighbor."""
        data = gnmi_subscribe_once(node, f"{BGP_PATH}/neighbors/neighbor[neighbor-address={peer_ip}]")
        if not data:
            return f"Error: could not retrieve BGP neighbor {peer_ip!r} from {node.name} ({node.fqdn})"
        return format_node_results({node.name: data})

    def get_bgp_config(self, node: NodeInfo) -> str:
        """Return the native Junos BGP config plus routing-options (AS, router-id)."""
        replies = gnmi_get_config(node, [BGP_CONFIG_PATH, ROUTING_OPTIONS_PATH])
        bgp = replies.get("configuration/protocols/bgp")
        if bgp is None:
            return f"Error: could not retrieve BGP config from {node.name} ({node.fqdn})"
        return format_node_results({node.name: {
            "routing-options": replies.get("configuration/routing-options"),
            "protocols": {"bgp": bgp},
        }})

    # ------------------------------------------------------------------
    # EVPN — read
    # ------------------------------------------------------------------

    def get_evpn_instances(self, node: NodeInfo) -> str:
        """Return all mac-vrf instances normalised to {name, type, vni, evi}.

        Vlan-based mac-vrf has no separate EVI; the VLAN id is reported as the EVI.
        """
        instances = [{
            "name": inst.get("name"),
            "type": "mac-vrf",
            "vni": _vni(_first_vlan(inst)),
            "evi": _first_vlan(inst).get("vlan-id"),
        } for inst in _mac_vrfs(node)]
        if not instances:
            return f"No EVPN instances found on {node.name} ({node.fqdn})"
        return format_node_results({node.name: instances})

    def get_evpn_instance(self, node: NodeInfo, instance_name: str) -> str:
        """Return the mac-vrf routing-instance config plus the config of its access units."""
        inst = _find_instance(node, instance_name)
        if inst is None:
            return f"Service {instance_name!r} not found on {node.name} ({node.fqdn})"
        interfaces = _items(_config(node, INTERFACES_PATH), "interface")
        vlan = _first_vlan(inst)
        return format_node_results({node.name: {
            "name": inst.get("name"),
            "vlan-id": vlan.get("vlan-id"),
            "vni": _vni(vlan),
            "routing-instance": inst,
            "interface-units": [
                {"name": ifl, "config": _find_unit(interfaces, ifl)} for ifl in _instance_ifls(inst)
            ],
        }})

    def get_evpn_instance_state(self, node: NodeInfo, instance_name: str) -> str:
        """Return the operational state (VLAN status, VTEPs, EVPN peers, access interfaces) of one instance."""
        inst = _find_instance(node, instance_name)
        if inst is None:
            return f"Service {instance_name!r} state not found on {node.name} ({node.fqdn})"
        # One Subscribe on the instance root: Junos serves several subscriptions in a
        # request one after another, so this is about half the time of asking for
        # state, vlan, jnx-evpn, ... separately.
        ni = gnmi_subscribe_once(node, _ni_state_path(inst["name"]))
        if not ni or ni.get("state") is None:
            return f"Service {instance_name!r} state not found on {node.name} ({node.fqdn})"
        state, evpn = ni["state"], ni.get("jnx-evpn") or {}
        vlans = _items(ni, "vlan")
        rts = ((ni.get("inter-instance-policies") or {}).get("import-export-policy") or {}).get("state") or {}
        return format_node_results({node.name: {
            "name": inst["name"],
            "route-distinguisher": state.get("route-distinguisher"),
            "route-targets": {
                "import": [_strip_rt(rt) for rt in rts.get("import-route-target") or []],
                "export": [_strip_rt(rt) for rt in rts.get("export-route-target") or []],
            },
            "vlans": [{
                "vlan-id": v.get("vlan-id"),
                "vni": v.get("vni"),
                "status": v.get("status"),
                "local-macs": v.get("num-local-mac-entries"),
            } for v in vlans],
            "local-macs": (ni.get("mac-table-info") or {}).get("num-local-entries"),
            "remote-macs": evpn.get("num-remote-macs"),
            "vteps": [{
                "remote": t.get("remote-ip-address"),
                "source": t.get("source-ip-address"),
                "status": t.get("status"),
                "vnis": [n.get("vnid") for n in _items(t, "vnids")],
            } for t in _items(evpn, "vxlan-tunnel-end-point")],
            "evpn-peers": _items(evpn, "peer"),
            "interfaces": [
                {k: i.get(k) for k in ("name", "status", "mode", "esi")}
                for i in _items(evpn, "interfaces") if not str(i.get("name", "")).startswith(".local")
            ],
        }})

    # ------------------------------------------------------------------
    # EVPN — write
    # ------------------------------------------------------------------

    def provision_evpn_instance(
        self,
        node: NodeInfo,
        service_name: str,
        service_id: int,  # not used by Junos; present for Protocol compatibility
        vni: int,
        evi: int,         # not used by Junos; vlan-based mac-vrf is keyed by vlan_id
        route_distinguisher: str,
        export_rt: str,
        import_rt: str,
        dry_run: bool,
        interface_name: str = "",
        vlan_id: int = 0,
    ) -> str:
        """Create a vlan-based EVPN-VXLAN mac-vrf on a Junos node in one gNMI SET (one commit).

        Requires interface_name (e.g. "et-0/0/2", unit = vlan_id, or "et-0/0/2.20")
        and vlan_id. The parent port must already use flexible-vlan-tagging with
        encapsulation flexible-ethernet-services; it is not modified. Objects written:
          1. Access unit: encapsulation vlan-bridge, vlan-id = vlan_id
          2. routing-instance service_name: instance-type mac-vrf, service-type
             vlan-based, VTEP source lo0.0, RD, vrf-target, evpn encapsulation vxlan
             with the VNI in extended-vni-list, and vlan<N> mapped to the VNI
        """
        if not interface_name or vlan_id == 0:
            return (
                "Error: Junos EVPN provisioning requires 'interface_name' "
                "(e.g. 'et-0/0/2') and 'vlan_id' (1-4094)."
            )

        try:
            _MacVrfIntent(
                service_name=service_name,
                vni=vni,
                interface_name=interface_name,
                vlan_id=vlan_id,
                route_distinguisher=route_distinguisher,
                export_rt=export_rt,
                import_rt=import_rt,
            )
        except ValidationError as e:
            messages = [f"  - {err['loc'][0] if err['loc'] else 'rt'}: {err['msg']}" for err in e.errors()]
            return "Validation error(s):\n" + "\n".join(messages)

        port, unit = _split_ifl(interface_name)
        unit = unit or str(vlan_id)
        ifl = f"{port}.{unit}"

        instances = _routing_instances(node)
        if any(i.get("name") == service_name for i in instances):
            return f"Error: routing-instance {service_name!r} already exists on {node.name}."
        for inst in instances:
            if inst.get("instance-type") == "mac-vrf" and any(_vni(v) == vni for v in _vlans(inst)):
                return f"Error: VNI {vni} is already used by {inst.get('name')!r} on {node.name}."

        interfaces = _items(_config(node, INTERFACES_PATH), "interface")
        parent = _find_interface(interfaces, port)
        if parent is None:
            return f"Error: interface {port} is not configured on {node.name}."
        encapsulation = parent.get("encapsulation")
        if "flexible-vlan-tagging" not in parent or encapsulation not in _BRIDGE_ENCAPSULATIONS:
            return (
                f"Error: {port} on {node.name} cannot carry bridged units "
                f"(encapsulation={encapsulation}). It needs flexible-vlan-tagging and "
                "encapsulation flexible-ethernet-services; uplinks and routed ports cannot be used."
            )
        if _find_unit(interfaces, ifl) is not None:
            return f"Error: unit {ifl} already exists on {node.name}."

        rt = _normalise_rt(export_rt)
        vlan_name = f"vlan{vlan_id}"
        unit_value = {"name": int(unit), "encapsulation": "vlan-bridge", "vlan-id": vlan_id}
        ri_value = {
            "name": service_name,
            "instance-type": "mac-vrf",
            "service-type": "vlan-based",
            "vtep-source-interface": {"interface-name": VTEP_SOURCE_INTERFACE},
            "route-distinguisher": {"rd-type": route_distinguisher},
            "vrf-target": {"community": rt},
            "protocols": {"evpn": {"encapsulation": "vxlan", "extended-vni-list": [str(vni)]}},
            "interface": [{"name": ifl}],
            "vlans": {"vlan": [{
                "name": vlan_name,
                "vlan-id": vlan_id,
                "interface": [{"name": ifl}],
                "vxlan": {"vni": vni, "ingress-node-replication": [None]},
            }]},
        }
        updates = [(_unit_path(port, unit), unit_value), (_ri_path(service_name), ri_value)]

        if dry_run:
            return "\n\n".join(format_dry_run(node.name, p, v) for p, v in updates)

        try:
            gnmi_set_batch(node, updates=updates)
        except RuntimeError as e:
            return f"Error: provisioning failed on {node.name}; no changes were applied. {e}"

        return (
            f"OK: EVPN instance {service_name!r} (mac-vrf, VLAN={vlan_id}, VNI={vni}, "
            f"iface={ifl}, RD={route_distinguisher}, vrf-target={rt}) provisioned on {node.name}."
        )

    def delete_evpn_instance(self, node: NodeInfo, instance_name: str, dry_run: bool) -> str:
        """Delete a mac-vrf routing-instance and its access units in one gNMI SET (one commit).

        Parent ports (tagging, encapsulation) are left untouched.
        """
        inst = _find_instance(node, instance_name)
        if inst is None:
            return f"Service {instance_name!r} not found on {node.name} ({node.fqdn})"

        interfaces = _items(_config(node, INTERFACES_PATH), "interface")
        units = [ifl for ifl in _instance_ifls(inst) if _find_unit(interfaces, ifl) is not None]
        deletes = [_ri_path(inst["name"])] + [_unit_path(*_split_ifl(ifl)) for ifl in units]

        if dry_run:
            return "\n\n".join(
                f"[DRY RUN] Node: {node.name}\n  Operation: DELETE\n  Path:      {p}" for p in deletes
            )

        try:
            gnmi_set_batch(node, deletes=deletes)
        except RuntimeError as e:
            return f"Error: deletion failed on {node.name}; no changes were applied. {e}"
        removed = f" and unit(s) {', '.join(units)}" if units else ""
        return f"OK: EVPN instance {inst['name']!r}{removed} deleted from {node.name}."
