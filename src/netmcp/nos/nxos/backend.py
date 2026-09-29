"""Cisco NX-OS implementation of the NOSBackend Protocol.

Implements BGP (read) over NETCONF using the native Cisco-NX-OS-device YANG model,
default VRF (dom-items/Dom-list[name=default]):

  /System/bgp-items/inst-items                   asn
    dom-items/Dom-list[name=default]             rtrId, numPeers, numEstPeers
      peer-items/Peer-list[addr=X]               peerImp (template), config
        ent-items/PeerEntry-list                 operSt, operAsn, connEst, lastFlapTs
          af-items/PeerAfEntry-list[type=T]      tblSt, acceptedPaths, pfxSent
      peercont-items/PeerCont-list[name=T]       peer templates (asn, srcIf, AFs)

All other domains fall through to NotImplementedBackend.
"""

from netmcp.inventory import NodeInfo
from netmcp.nos.nxos.client import netconf_get, netconf_get_config
from netmcp.registry import NotImplementedBackend
from netmcp.utils.formatters import format_node_results
from netmcp.utils.yang import ns_get

NXOS_NS = "http://cisco.com/ns/yang/cisco-nx-os-device"

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
