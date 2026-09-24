"""Juniper Junos (Evolved) implementation of the NOSBackend Protocol.

Implements BGP reads via gNMI, YANG only (no CLI origin):
  - State: OpenConfig over Subscribe ONCE, since Junos Get serves config only.
    The default instance and the BGP protocol are both keyed "DEFAULT".
  - Config: native Junos YANG (junos-conf-*) over Get, under the `juniper` origin.
    The OpenConfig config tree is empty unless the node was configured through it.

All other domains fall through to NotImplementedBackend.
"""

from netmcp.inventory import NodeInfo
from netmcp.nos.junos.client import gnmi_get_config, gnmi_subscribe_once
from netmcp.registry import NotImplementedBackend
from netmcp.utils.formatters import format_node_results
from netmcp.utils.openconfig import entries, leaf, peer_summary

BGP_PATH = (
    "/network-instances/network-instance[name=DEFAULT]"
    "/protocols/protocol[identifier=BGP][name=DEFAULT]/bgp"
)
BGP_CONFIG_PATH = "juniper:/configuration/protocols/bgp"
ROUTING_OPTIONS_PATH = "juniper:/configuration/routing-options"


def _bgp_neighbors(node: NodeInfo) -> list[dict]:
    return entries(gnmi_subscribe_once(node, f"{BGP_PATH}/neighbors"), "neighbor")


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
