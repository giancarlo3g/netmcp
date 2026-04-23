"""System context — node info and alarms."""

from mcp.server.fastmcp import FastMCP

from router_mcp.client import gnmi_get
from router_mcp.utils.formatters import format_node_results


def register(mcp: FastMCP, nodes: dict[str, str]) -> None:

    @mcp.tool()
    def sros_list_nodes() -> str:
        """List all SR OS nodes discovered from the topology YAML.

        Returns node short names and their containerlab FQDNs.
        """
        lines = [f"  * {name} ({fqdn})" for name, fqdn in nodes.items()]
        return "Available SR OS nodes:\n" + "\n".join(lines)

    @mcp.tool()
    def sros_system_info(node: str) -> str:
        """Get basic system information from an SR OS node.

        Args:
            node: Node short name (e.g. "dcgw1").
        """
        hostname = nodes.get(node)
        if not hostname:
            return f"Error: unknown node {node!r}. Available: {list(nodes)}"
        data = gnmi_get(hostname, "nokia-state:state/system")
        if data is None:
            return f"Error: could not retrieve system info from {node} ({hostname})"
        return format_node_results({node: data})

    @mcp.tool()
    def sros_system_alarms(node: str) -> str:
        """Get active system alarms from an SR OS node.

        Args:
            node: Node short name (e.g. "dcgw1").
        """
        hostname = nodes.get(node)
        if not hostname:
            return f"Error: unknown node {node!r}. Available: {list(nodes)}"
        data = gnmi_get(hostname, "nokia-state:state/system/alarm")
        if data is None:
            return f"No alarms found or could not retrieve alarms from {node} ({hostname})"
        return format_node_results({node: data})
