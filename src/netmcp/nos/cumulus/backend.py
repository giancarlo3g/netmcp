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

All other domains fall through to NotImplementedBackend.
"""

import ipaddress

from netmcp.inventory import NodeInfo
from netmcp.nos.cumulus.client import nvue_get
from netmcp.registry import NotImplementedBackend
from netmcp.utils.formatters import format_node_results

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
