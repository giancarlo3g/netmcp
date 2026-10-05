"""Cisco NX-OS implementation of the NOSBackend Protocol.

Implements BGP (read) over NETCONF using the native Cisco-NX-OS-device YANG model,
default VRF (dom-items/Dom-list[name=default]):

  /System/bgp-items/inst-items                   asn
    dom-items/Dom-list[name=default]             rtrId, numPeers, numEstPeers
      peer-items/Peer-list[addr=X]               peerImp (template), config
        ent-items/PeerEntry-list                 operSt, operAsn, connEst, lastFlapTs
          af-items/PeerAfEntry-list[type=T]      tblSt, acceptedPaths, pfxSent
      peercont-items/PeerCont-list[name=T]       peer templates (asn, srcIf, AFs)

Implements EVPN (VLAN-based, one VLAN = one EVI) read and write over the same model.
One instance is spread over four objects, joined on the VNI:

  /System/bd-items/bd-items/BD-list[fabEncap=vlan-N]          name, accEncap=vxlan-VNI (vn-segment)
  /System/eps-items/epId-items/Ep-list[epId=1]                 nve1 (sourceInterface, hostReach)
    nws-items/vni-items/Nw-list[vni=VNI]                       member vni, IngRepl-items/proto
  /System/evpn-items/bdevi-items/BDEvi-list[encap=vxlan-VNI]   evpn vni l2: rd, rttp-items RTs
  /System/intf-items/phys-items/PhysIf-list[id=ethX/Y]         access trunk: mode, trunkVlans

State adds Ep-list operState, nws-items/opervni-items/OperNw-list, peers-items/dy_peer-items
(VTEPs), BD-list operSt/member-items, and mac-items/table-items (MAC table).
Writes are a single <edit-config> with rollback-on-error, so no rollback helper is needed.

All other domains fall through to NotImplementedBackend.
"""

import copy
import re
import textwrap
from xml.etree import ElementTree as ET

from pydantic import BaseModel, Field, ValidationError, field_validator

from netmcp.inventory import NodeInfo
from netmcp.nos.nxos.client import netconf_edit_config, netconf_get, netconf_get_config
from netmcp.registry import NotImplementedBackend
from netmcp.utils.formatters import format_node_results
from netmcp.utils.yang import ns_get

NXOS_NS = "http://cisco.com/ns/yang/cisco-nx-os-device"
NC_NS = "urn:ietf:params:xml:ns:netconf:base:1.0"
ALL_VLANS = "1-4094"

# NX-OS AF type -> OpenConfig AFI-SAFI spelling
_AFI_SAFI_NAMES = {
    "ipv4-ucast": "IPV4_UNICAST",
    "ipv6-ucast": "IPV6_UNICAST",
    "l2vpn-evpn": "L2VPN_EVPN",
    "vpnv4-ucast": "L3VPN_IPV4_UNICAST",
    "vpnv6-ucast": "L3VPN_IPV6_UNICAST",
}


def _bgp_filter(peer_ip: str | None = None, vrf: str | None = "default") -> str:
    """Subtree filter for the BGP instance (vrf=None), one VRF, or one peer in it."""
    if vrf is None:
        return f'<System xmlns="{NXOS_NS}"><bgp-items/></System>'
    peer = f"<peer-items><Peer-list><addr>{peer_ip}</addr></Peer-list></peer-items>" if peer_ip else ""
    return (
        f'<System xmlns="{NXOS_NS}"><bgp-items><inst-items><asn/><dom-items>'
        f"<Dom-list><name>{vrf}</name>{peer}</Dom-list>"
        "</dom-items></inst-items></bgp-items></System>"
    )


def _entries(data, key: str) -> list:
    found = ns_get(data or {}, key)
    if found is None:
        return []
    return found if isinstance(found, list) else [found]


def _inst(data) -> dict:
    return ns_get(ns_get(ns_get(data or {}, "System") or {}, "bgp-items") or {}, "inst-items") or {}


def _dom(data) -> dict:
    """Return the (single) Dom-list entry from a reply filtered on one VRF."""
    doms = _entries(ns_get(_inst(data), "dom-items"), "Dom-list")
    return doms[0] if doms else {}


def _peers(dom: dict) -> list[dict]:
    return _entries(ns_get(dom, "peer-items"), "Peer-list")


def _int(value):
    """NETCONF leaves are text; return numeric ones as int."""
    return int(value) if isinstance(value, str) and value.isdigit() else value


def _afi_safi_name(name: str) -> str:
    return _AFI_SAFI_NAMES.get(name, name.upper().replace("-", "_"))


def _active_afi_safis(entry: dict) -> dict[str, dict]:
    """Map active (tblSt up) AFI-SAFI name -> {received, sent}.

    NX-OS has no per-peer installed-path counter in the device model.
    """
    return {
        _afi_safi_name(af.get("type", "")): {
            "received": _int(af.get("acceptedPaths")),
            "sent": _int(af.get("pfxSent")),
        }
        for af in _entries(ns_get(entry, "af-items"), "PeerAfEntry-list")
        if af.get("tblSt") == "up"
    }


def _peer_summary(peer: dict, templates: dict[str, dict]) -> dict:
    """Compact view of one NX-OS BGP peer, shaped like utils.openconfig.peer_summary."""
    ents = _entries(ns_get(peer, "ent-items"), "PeerEntry-list")
    entry = ents[0] if ents else {}
    template = peer.get("peerImp") or None
    peer_as = entry.get("operAsn") or peer.get("asn") or (templates.get(template) or {}).get("asn")
    state = entry.get("operSt")
    established = state == "established"
    return {
        "peer": peer.get("addr"),
        "peer-as": _int(peer_as),
        "peer-group": template,
        "state": state.upper().replace("-", "_") if isinstance(state, str) else state,
        "established-transitions": _int(entry.get("connEst")),
        "last-established": entry.get("lastFlapTs") if established else None,
        "afi-safis": _active_afi_safis(entry),
    }


def _templates(dom: dict) -> dict[str, dict]:
    return {t.get("name"): t for t in _entries(ns_get(dom, "peercont-items"), "PeerCont-list")}


# ----------------------------------------------------------------------
# EVPN
# ----------------------------------------------------------------------

_PORTS_FILTER = (
    "<intf-items><phys-items><PhysIf-list><id/><layer/><mode/><trunkVlans/></PhysIf-list>"
    "</phys-items></intf-items>"
)


def _evpn_filter(ports: bool = False) -> str:
    """Subtree filter for VLANs, NVE and EVPN VNIs (plus switchport leaves of physical ports)."""
    return (
        f'<System xmlns="{NXOS_NS}"><bd-items><bd-items/></bd-items>'
        "<eps-items><epId-items/></eps-items><evpn-items><bdevi-items/></evpn-items>"
        f"{_PORTS_FILTER if ports else ''}</System>"
    )


def _evpn_state_filter(vlan_id: int) -> str:
    """Subtree filter for one VLAN's state, NVE state/peers and the MAC table.

    The MAC table can't be filtered by VLAN alone (both list keys are required).
    """
    return (
        f'<System xmlns="{NXOS_NS}"><bd-items><bd-items><BD-list><fabEncap>vlan-{vlan_id}</fabEncap>'
        "</BD-list></bd-items></bd-items><eps-items><epId-items/></eps-items>"
        "<mac-items><table-items/></mac-items></System>"
    )


def _path(data, *keys) -> dict:
    for key in keys:
        data = ns_get(data or {}, key) or {}
    return data


def _encap_id(value, kind: str) -> int | None:
    """'vlan-10' / 'vxlan-1010' -> 10 / 1010."""
    if isinstance(value, str) and value.startswith(f"{kind}-") and value[len(kind) + 1:].isdigit():
        return int(value[len(kind) + 1:])
    return None


def _nve(system: dict) -> dict:
    """The NVE interface (Ep-list), nve1 if present."""
    eps = _entries(_path(system, "eps-items", "epId-items"), "Ep-list")
    return next((e for e in eps if e.get("epId") == "1"), eps[0] if eps else {})


def _collect(data) -> list[dict]:
    """Join VLANs, NVE member VNIs and EVPN VNIs into one record per VLAN mapped to a VNI."""
    system = _path(data, "System")
    nve = _nve(system)
    nws = {_int(n.get("vni")): n for n in _entries(_path(nve, "nws-items", "vni-items"), "Nw-list")}
    evis = {
        _encap_id(e.get("encap"), "vxlan"): e
        for e in _entries(_path(system, "evpn-items", "bdevi-items"), "BDEvi-list")
    }
    records = []
    for bd in _entries(_path(system, "bd-items", "bd-items"), "BD-list"):
        vni = _encap_id(bd.get("accEncap"), "vxlan")
        if vni is None:
            continue
        records.append({
            "vlan_id": _encap_id(bd.get("fabEncap"), "vlan"),
            "name": bd.get("name") or None,
            "vni": vni,
            "bd": bd,
            "nve_vni": nws.get(vni),
            "evpn": evis.get(vni),
        })
    return sorted(records, key=lambda r: r["vlan_id"] or 0)


def _find_instance(records: list[dict], instance_name: str) -> dict | None:
    """Match instance_name against the VLAN name or VLAN id."""
    return next(
        (r for r in records if instance_name in {str(r["name"]), str(r["vlan_id"])}), None
    )


def _instance_name(rec: dict) -> str:
    return rec["name"] or str(rec["vlan_id"])


def _to_nxos_id(value: str, prefix: str) -> str:
    """'65000:10' / 'target:65000:10' / '1.1.1.1:10' -> 'route-target:as2-nn2:65000:10' (or 'rd:...').

    An RD of 'auto' maps to 'rd:unknown:0:0'.
    """
    v = value.strip()
    if v.lower().startswith("target:"):
        v = v[len("target:"):]
    if prefix == "rd" and v.lower() == "auto":
        return "rd:unknown:0:0"
    m = re.fullmatch(r"(\d+\.\d+\.\d+\.\d+|\d+):(\d+)", v)
    if not m:
        raise ValueError(f"{value!r} is not a valid {prefix} (e.g. 65000:10).")
    admin, nn = m.group(1), int(m.group(2))
    if "." in admin:
        kind, nn_max = "ipv4-nn2", 0xFFFF
    elif int(admin) <= 0xFFFF:
        kind, nn_max = ("as2-nn2", 0xFFFF) if nn <= 0xFFFF else ("as2-nn4", 0xFFFFFFFF)
    elif int(admin) <= 0xFFFFFFFF:
        kind, nn_max = "as4-nn2", 0xFFFF
    else:
        raise ValueError(f"{value!r}: AS number out of range.")
    if nn > nn_max:
        raise ValueError(f"{value!r}: assigned number out of range for {kind}.")
    return f"{prefix}:{kind}:{admin}:{nn}"


def _from_nxos_id(value) -> str | None:
    """'route-target:as2-nn2:65000:10' / 'rd:as2-nn2:10:6' -> '65000:10' / '10:6'; 'rd:unknown:0:0' -> 'auto'."""
    if not isinstance(value, str):
        return None
    parts = value.split(":")
    if len(parts) >= 4 and parts[0] in ("rd", "route-target"):
        return "auto" if parts[1] == "unknown" else ":".join(parts[2:])
    return value


def _route_targets(evpn: dict | None, direction: str) -> list[str]:
    for rttp in _entries(_path(evpn, "rttp-items"), "RttP-list"):
        if rttp.get("type") == direction:
            return [_from_nxos_id(e.get("rtt")) for e in _entries(_path(rttp, "ent-items"), "RttEntry-list")]
    return []


def _vlan_set(spec) -> set[int]:
    """'1-5,10' -> {1,2,3,4,5,10}; '' or 'none' -> empty."""
    vlans: set[int] = set()
    for part in (spec or "").split(","):
        part = part.strip()
        if re.fullmatch(r"\d+", part):
            vlans.add(int(part))
        elif m := re.fullmatch(r"(\d+)-(\d+)", part):
            vlans.update(range(int(m.group(1)), int(m.group(2)) + 1))
    return vlans


def _vlan_spec(vlans: set[int]) -> str:
    """{1,2,3,10} -> '1-3,10'."""
    ranges: list[list[int]] = []
    for v in sorted(vlans):
        if ranges and v == ranges[-1][1] + 1:
            ranges[-1][1] = v
        else:
            ranges.append([v, v])
    return ",".join(str(a) if a == b else f"{a}-{b}" for a, b in ranges)


def _port_id(interface_name: str) -> str | None:
    """'Ethernet1/1' / 'eth1/1' -> 'eth1/1'; None if not an Ethernet port."""
    m = re.fullmatch(r"(?:ethernet|eth)(\d+/\d+(?:/\d+)?)", interface_name.strip(), re.I)
    return f"eth{m.group(1)}" if m else None


def _ports(system: dict) -> dict[str, dict]:
    return {p.get("id"): p for p in _entries(_path(system, "intf-items", "phys-items"), "PhysIf-list")}


def _trunk_interfaces(system: dict, vlan_id: int) -> list[dict]:
    """[{name, trunk-vlans}] of trunk ports that carry vlan_id."""
    return [
        {"name": pid, "trunk-vlans": p.get("trunkVlans")}
        for pid, p in _ports(system).items()
        if p.get("mode") == "trunk" and vlan_id in _vlan_set(p.get("trunkVlans"))
    ]


class _VlanEvpnIntent(BaseModel):
    service_name: str = Field(..., min_length=1, max_length=32)
    vni: int = Field(..., ge=1, le=16_777_214)
    interface_name: str
    vlan_id: int = Field(..., ge=2, le=3967)  # 3968-4094 are reserved by default
    route_distinguisher: str  # e.g. "10:6" or "auto"
    export_rt: str             # e.g. "65000:10" or "target:65000:10"
    import_rt: str

    @field_validator("interface_name")
    @classmethod
    def validate_interface_name(cls, v: str) -> str:
        if _port_id(v) is None:
            raise ValueError(
                f"{v!r} is not a valid access port. "
                "Only Ethernet interfaces (e.g. Ethernet1/1) are permitted."
            )
        return v

    @field_validator("route_distinguisher")
    @classmethod
    def validate_rd(cls, v: str) -> str:
        _to_nxos_id(v, "rd")
        return v

    @field_validator("export_rt", "import_rt")
    @classmethod
    def validate_rt(cls, v: str) -> str:
        if v.strip().lower() == "auto":
            raise ValueError("route-target must be explicit (e.g. 65000:10).")
        _to_nxos_id(v, "route-target")
        return v


def _sub(parent: ET.Element, *path: str, text: str | None = None) -> ET.Element:
    """Append a chain of child elements; set text on the last one."""
    for tag in path:
        parent = ET.SubElement(parent, tag)
    if text is not None:
        parent.text = text
    return parent


def _system_root() -> ET.Element:
    return ET.Element("System", {"xmlns": NXOS_NS, "xmlns:nc": NC_NS})


def _bd_list(root: ET.Element, vlan_id: int) -> ET.Element:
    bd = _sub(root, "bd-items", "bd-items", "BD-list")
    _sub(bd, "fabEncap", text=f"vlan-{vlan_id}")
    return bd


def _nw_list(root: ET.Element, nve_id: str, vni: int) -> ET.Element:
    ep = _sub(root, "eps-items", "epId-items", "Ep-list")
    _sub(ep, "epId", text=nve_id)
    nw = _sub(ep, "nws-items", "vni-items", "Nw-list")
    _sub(nw, "vni", text=str(vni))
    return nw


def _bdevi_list(root: ET.Element, vni: int) -> ET.Element:
    evi = _sub(root, "evpn-items", "bdevi-items", "BDEvi-list")
    _sub(evi, "encap", text=f"vxlan-{vni}")
    return evi


def _set_trunk(root: ET.Element, port_id: str, spec: str) -> None:
    port = _sub(root, "intf-items", "phys-items", "PhysIf-list")
    _sub(port, "id", text=port_id)
    _sub(port, "trunkVlans", text=spec)


def _dry_run(node_name: str, root: ET.Element) -> str:
    pretty = copy.deepcopy(root)
    ET.indent(pretty)
    return (
        f"[DRY RUN] Node: {node_name}\n"
        "  RPC:    edit-config (running, merge, rollback-on-error)\n"
        f"  Config:\n{textwrap.indent(ET.tostring(pretty, encoding='unicode'), '    ')}"
    )


class NXOSBackend(NotImplementedBackend):
    """Cisco NX-OS backend. Dispatched to by unified tools."""

    def __init__(self) -> None:
        super().__init__(nos_type="nxos", transport="netconf")

    # ------------------------------------------------------------------
    # BGP
    # ------------------------------------------------------------------

    def get_bgp_summary(self, node: NodeInfo) -> str:
        """Return BGP global state plus a one-line-per-peer summary for the default VRF."""
        data = netconf_get(node, _bgp_filter())
        dom = _dom(data)
        if not dom:
            return f"Error: could not retrieve BGP summary from {node.name} ({node.fqdn})"
        templates = _templates(dom)
        peers = [_peer_summary(p, templates) for p in _peers(dom)]
        return format_node_results({node.name: {
            "as": _int(_inst(data).get("asn")),
            "router-id": dom.get("rtrId") or dom.get("operRtrId"),
            "peers": {
                "total": len(peers),
                "established": sum(p["state"] == "ESTABLISHED" for p in peers),
            },
            "neighbors": [
                {k: p[k] for k in ("peer", "peer-as", "state", "afi-safis")} for p in peers
            ],
        }})

    def get_bgp_neighbors(self, node: NodeInfo) -> str:
        """Return a compact view (state, AS, active AFI-SAFI path counts) of every BGP neighbor."""
        dom = _dom(netconf_get(node, _bgp_filter()))
        templates = _templates(dom)
        peers = [_peer_summary(p, templates) for p in _peers(dom)]
        if not peers:
            return f"No BGP neighbors found on {node.name} ({node.fqdn})"
        return format_node_results({node.name: peers})

    def get_bgp_neighbor(self, node: NodeInfo, peer_ip: str) -> str:
        """Return the full native tree (config + state) for one BGP neighbor."""
        peers = _peers(_dom(netconf_get(node, _bgp_filter(peer_ip))))
        if not peers:
            return f"Error: could not retrieve BGP neighbor {peer_ip!r} from {node.name} ({node.fqdn})"
        return format_node_results({node.name: peers[0]})

    def get_bgp_config(self, node: NodeInfo) -> str:
        """Return the BGP configuration (instance, VRFs, peers, peer templates) from running config."""
        inst = _inst(netconf_get_config(node, _bgp_filter(vrf=None)))
        if not inst:
            return f"Error: could not retrieve BGP config from {node.name} ({node.fqdn})"
        return format_node_results({node.name: inst})


    # ------------------------------------------------------------------
    # EVPN — read
    # ------------------------------------------------------------------

    def get_evpn_instances(self, node: NodeInfo) -> str:
        """Return all VLAN-based EVPN instances normalised to {name, type, vni, evi}.

        NX-OS VLAN-based EVPN has no separate EVI; the VLAN id is reported as the EVI.
        """
        records = _collect(netconf_get_config(node, _evpn_filter()))
        if not records:
            return f"No EVPN instances found on {node.name} ({node.fqdn})"
        return format_node_results({node.name: [
            {"name": _instance_name(r), "type": "vlan", "vni": r["vni"], "evi": r["vlan_id"]}
            for r in records
        ]})

    def get_evpn_instance(self, node: NodeInfo, instance_name: str) -> str:
        """Return the merged configuration (VLAN, NVE VNI, RD/RT, trunk ports) of one EVPN instance."""
        data = netconf_get_config(node, _evpn_filter(ports=True))
        rec = _find_instance(_collect(data), instance_name)
        if rec is None:
            return f"Service {instance_name!r} not found on {node.name} ({node.fqdn})"
        nve = _nve(_path(data, "System"))
        nw = rec["nve_vni"] or {}
        return format_node_results({node.name: {
            "name": _instance_name(rec),
            "vlan-id": rec["vlan_id"],
            "vni": rec["vni"],
            "route-distinguisher": _from_nxos_id((rec["evpn"] or {}).get("rd")),
            "import-rt": _route_targets(rec["evpn"], "import"),
            "export-rt": _route_targets(rec["evpn"], "export"),
            "nve-interface": f"nve{nve['epId']}" if rec["nve_vni"] and nve.get("epId") else None,
            "replication": _path(nw, "IngRepl-items").get("proto") or nw.get("mcastGroup"),
            "vlan": rec["bd"],
            "nve-vni": rec["nve_vni"],
            "evpn": rec["evpn"],
            "trunk-interfaces": _trunk_interfaces(_path(data, "System"), rec["vlan_id"]),
        }})

    def get_evpn_instance_state(self, node: NodeInfo, instance_name: str) -> str:
        """Return the operational state (VLAN, VNI, NVE, VTEP peers, MAC table) of one EVPN instance."""
        rec = _find_instance(_collect(netconf_get_config(node, _evpn_filter())), instance_name)
        if rec is None:
            return f"Service {instance_name!r} state not found on {node.name} ({node.fqdn})"
        vlan_id, vni = rec["vlan_id"], rec["vni"]
        system = _path(netconf_get(node, _evpn_state_filter(vlan_id)), "System")
        bds = _entries(_path(system, "bd-items", "bd-items"), "BD-list")
        bd = bds[0] if bds else {}
        nve = _nve(system)
        oper = next(
            (o for o in _entries(_path(nve, "nws-items", "opervni-items"), "OperNw-list")
             if _int(o.get("vni")) == vni),
            {},
        )
        macs = [
            m for m in _entries(_path(system, "mac-items", "table-items", "vlan-items"), "MacAddressEntry-list")
            if m.get("vlan") == f"vlan-{vlan_id}"
        ]
        return format_node_results({node.name: {
            "name": _instance_name(rec),
            "vlan-id": vlan_id,
            "vni": vni,
            "vlan-state": {"admin": bd.get("adminSt"), "oper": bd.get("operSt")},
            "vlan-members": [m.get("id") for m in _entries(_path(bd, "member-items"), "VlanMemberIf-list")],
            "vni-state": {k: oper.get(k) for k in ("state", "type", "mode", "vlanBD")} if oper else None,
            "nve": {
                "interface": f"nve{nve['epId']}" if nve.get("epId") else None,
                "state": nve.get("operState"),
                "source-interface": nve.get("sourceInterface"),
                "source-ip": nve.get("primaryIp"),
                "host-reachability": nve.get("hostReach"),
            },
            "vteps": [
                {"peer": p.get("ip"), "state": p.get("state"), "up-since": p.get("upStateTransitionTs")}
                for p in _entries(_path(nve, "peers-items", "dy_peer-items"), "DyPeer-list")
            ],
            "macs": [
                {
                    "mac": m.get("macAddress"),
                    "interface": m.get("port"),
                    "learned": "remote" if m.get("macInfo") == "nve" else "local",
                    "static": m.get("static") == "true",
                }
                for m in macs
            ],
        }})

    # ------------------------------------------------------------------
    # EVPN — write
    # ------------------------------------------------------------------

    def provision_evpn_instance(
        self,
        node: NodeInfo,
        service_name: str,
        service_id: int,  # not used by NX-OS; present for Protocol compatibility
        vni: int,
        evi: int,         # not used by NX-OS; VLAN-based EVPN is keyed by vlan_id
        route_distinguisher: str,
        export_rt: str,
        import_rt: str,
        dry_run: bool,
        interface_name: str = "",
        vlan_id: int = 0,
    ) -> str:
        """Create a VLAN-based EVPN instance on an NX-OS node in one edit-config.

        Requires interface_name (e.g. "Ethernet1/1", must be a layer-2 trunk) and
        vlan_id. The NVE interface (nve1, host-reachability bgp) must already exist.
        Objects written:
          1. VLAN with name = service_name and vn-segment = vni
          2. nve1 member vni with ingress-replication protocol bgp
          3. evpn / vni N l2 with RD and import/export RTs
          4. VLAN added to the port's trunk allowed list, unless already carried
        """
        if not interface_name or vlan_id == 0:
            return (
                "Error: NX-OS EVPN provisioning requires 'interface_name' "
                "(e.g. 'Ethernet1/1') and 'vlan_id' (2-3967)."
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

        data = netconf_get_config(node, _evpn_filter(ports=True))
        system = _path(data, "System")
        bds = _entries(_path(system, "bd-items", "bd-items"), "BD-list")
        if any(_encap_id(bd.get("fabEncap"), "vlan") == vlan_id for bd in bds):
            return f"Error: VLAN {vlan_id} already exists on {node.name}."
        if any(r["vni"] == vni for r in _collect(data)):
            return f"Error: VNI {vni} is already mapped to a VLAN on {node.name}."
        nve = _nve(system)
        if not nve.get("epId"):
            return f"Error: no NVE interface configured on {node.name}; configure nve1 (host-reachability bgp) first."

        port_id = _port_id(interface_name)
        port = _ports(system).get(port_id)
        if port is None:
            return f"Error: interface {interface_name} not found on {node.name}."
        if port.get("layer") != "Layer2" or port.get("mode") != "trunk":
            return (
                f"Error: {interface_name} on {node.name} is not a layer-2 trunk "
                f"(layer={port.get('layer')}, mode={port.get('mode')}). Uplinks and routed ports cannot be used."
            )

        root = _system_root()
        bd = _bd_list(root, vlan_id)
        _sub(bd, "name", text=service_name)
        _sub(bd, "accEncap", text=f"vxlan-{vni}")
        nw = _nw_list(root, nve["epId"], vni)
        _sub(nw, "IngRepl-items", "proto", text="bgp")
        evi_el = _bdevi_list(root, vni)
        _sub(evi_el, "rd", text=_to_nxos_id(route_distinguisher, "rd"))
        rttp = _sub(evi_el, "rttp-items")
        for direction, rt in (("import", import_rt), ("export", export_rt)):
            entry = _sub(rttp, "RttP-list")
            _sub(entry, "type", text=direction)
            _sub(entry, "ent-items", "RttEntry-list", "rtt", text=_to_nxos_id(rt, "route-target"))
        trunk_vlans = _vlan_set(port.get("trunkVlans"))
        if vlan_id not in trunk_vlans:
            _set_trunk(root, port_id, _vlan_spec(trunk_vlans | {vlan_id}))

        if dry_run:
            return _dry_run(node.name, root)

        try:
            netconf_edit_config(node, ET.tostring(root, encoding="unicode"))
        except RuntimeError as e:
            return f"Error: provisioning failed on {node.name}; no changes were applied. {e}"

        return (
            f"OK: EVPN instance {service_name!r} (VLAN={vlan_id}, VNI={vni}, "
            f"iface={interface_name}, RD={_from_nxos_id(_to_nxos_id(route_distinguisher, 'rd'))}, "
            f"export-RT={_from_nxos_id(_to_nxos_id(export_rt, 'route-target'))}, "
            f"import-RT={_from_nxos_id(_to_nxos_id(import_rt, 'route-target'))}) "
            f"provisioned on {node.name}."
        )

    def delete_evpn_instance(self, node: NodeInfo, instance_name: str, dry_run: bool) -> str:
        """Delete an EVPN instance (evpn vni, nve member vni, VLAN) in one edit-config.

        Also removes the VLAN from trunk allowed lists that carry it, except the
        default all-VLAN list and lists where it is the only VLAN.
        """
        data = netconf_get_config(node, _evpn_filter(ports=True))
        rec = _find_instance(_collect(data), instance_name)
        if rec is None:
            return f"Service {instance_name!r} not found on {node.name} ({node.fqdn})"
        system = _path(data, "System")
        vlan_id, vni = rec["vlan_id"], rec["vni"]

        root = _system_root()
        delete = {"nc:operation": "delete"}
        if rec["evpn"] is not None:
            _bdevi_list(root, vni).attrib.update(delete)
        if rec["nve_vni"] is not None:
            _nw_list(root, _nve(system)["epId"], vni).attrib.update(delete)
        _bd_list(root, vlan_id).attrib.update(delete)

        skipped = []
        for trunk in _trunk_interfaces(system, vlan_id):
            if trunk["trunk-vlans"] == ALL_VLANS:
                continue
            remaining = _vlan_set(trunk["trunk-vlans"]) - {vlan_id}
            if not remaining:
                skipped.append(trunk["name"])
                continue
            _set_trunk(root, trunk["name"], _vlan_spec(remaining))

        note = (
            f" VLAN {vlan_id} left in trunk allowed list of {', '.join(skipped)} (only VLAN listed)."
            if skipped else ""
        )

        if dry_run:
            return _dry_run(node.name, root) + (f"\n\nNote:{note}" if note else "")

        try:
            netconf_edit_config(node, ET.tostring(root, encoding="unicode"))
        except RuntimeError as e:
            return f"Error: deletion failed on {node.name}; no changes were applied. {e}"
        return f"OK: EVPN instance {instance_name!r} (VLAN={vlan_id}, VNI={vni}) deleted from {node.name}.{note}"
