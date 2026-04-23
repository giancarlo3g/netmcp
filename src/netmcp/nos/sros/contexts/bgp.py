"""BGP context — summary, neighbors, and config."""

from mcp.server.fastmcp import FastMCP

from netmcp.inventory import NodeInfo
from netmcp.nos.sros.client import gnmi_get
from netmcp.utils.formatters import format_node_results


def register(mcp: FastMCP, nodes: dict[str, NodeInfo]) -> None:

    @mcp.tool()
    def sros_bgp_summary(node: str) -> str:
        """Get BGP summary statistics from the Base routing instance on an SR OS node.

        Args:
            node: Node short name (e.g. "dcgw1").
        """
        node_info = nodes.get(node)
        if not node_info:
            return f"Error: unknown node {node!r}. Available: {list(nodes)}"
        data = gnmi_get(node_info, "nokia-state:state/router[router-name=Base]/bgp/statistics")
        if data is None:
            return f"Error: could not retrieve BGP summary from {node} ({node_info.fqdn})"
        return format_node_results({node: data})

    @mcp.tool()
    def sros_bgp_neighbors(node: str) -> str:
        """Get all BGP neighbor states from the Base routing instance on an SR OS node.

        Args:
            node: Node short name (e.g. "dcgw1").
        """
        node_info = nodes.get(node)
        if not node_info:
            return f"Error: unknown node {node!r}. Available: {list(nodes)}"
        data = gnmi_get(node_info, "nokia-state:state/router[router-name=Base]/bgp/neighbor")
        if data is None:
            return f"Error: could not retrieve BGP neighbors from {node} ({node_info.fqdn})"
        return format_node_results({node: data})

    @mcp.tool()
    def sros_bgp_neighbor(node: str, peer_ip: str) -> str:
        """Get detailed state for a specific BGP neighbor on an SR OS node.

        Args:
            node: Node short name (e.g. "dcgw1").
            peer_ip: Neighbor IP address (e.g. "10.0.0.21").
        """
        node_info = nodes.get(node)
        if not node_info:
            return f"Error: unknown node {node!r}. Available: {list(nodes)}"
        path = f"nokia-state:state/router[router-name=Base]/bgp/neighbor[ip-address={peer_ip}]"
        data = gnmi_get(node_info, path)
        if data is None:
            return f"Error: could not retrieve BGP neighbor {peer_ip!r} from {node} ({node_info.fqdn})"
        return format_node_results({node: data})

    @mcp.tool()
    def sros_bgp_config(node: str) -> str:
        """Get the BGP configuration from the Base routing instance on an SR OS node.

        Args:
            node: Node short name (e.g. "dcgw1").
        """
        node_info = nodes.get(node)
        if not node_info:
            return f"Error: unknown node {node!r}. Available: {list(nodes)}"
        data = gnmi_get(node_info, "nokia-conf:configure/router[router-name=Base]/bgp")
        if data is None:
            return f"Error: could not retrieve BGP config from {node} ({node_info.fqdn})"
        return format_node_results({node: data})
