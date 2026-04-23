"""Interfaces context — ports and router interfaces."""

from mcp.server.fastmcp import FastMCP

from router_mcp.client import gnmi_get, gnmi_set
from router_mcp.utils.formatters import format_node_results, format_dry_run


def register(mcp: FastMCP, nodes: dict[str, str]) -> None:

    @mcp.tool()
    def sros_get_ports(node: str) -> str:
        """Get all physical ports on an SR OS node.

        Args:
            node: Node short name (e.g. "dcgw1").
        """
        hostname = nodes.get(node)
        if not hostname:
            return f"Error: unknown node {node!r}. Available: {list(nodes)}"
        data = gnmi_get(hostname, "nokia-state:state/port")
        if data is None:
            return f"Error: could not retrieve ports from {node} ({hostname})"
        ports = data if isinstance(data, list) else [data]
        summary = [
            {
                "port-id": p.get("nokia-state:port-id"),
                "admin-state": p.get("nokia-state:admin-state"),
                "oper-state": p.get("nokia-state:oper-state"),
                "description": p.get("nokia-state:description", ""),
            }
            for p in ports
        ]
        return format_node_results({node: summary})

    @mcp.tool()
    def sros_get_interfaces(node: str) -> str:
        """Get all router interfaces in the Base routing instance on an SR OS node.

        Args:
            node: Node short name (e.g. "dcgw1").
        """
        hostname = nodes.get(node)
        if not hostname:
            return f"Error: unknown node {node!r}. Available: {list(nodes)}"
        data = gnmi_get(hostname, 'nokia-state:state/router[router-name=Base]/interface')
        if data is None:
            return f"Error: could not retrieve interfaces from {node} ({hostname})"
        return format_node_results({node: data})

    @mcp.tool()
    def sros_get_interface_state(node: str, interface_name: str) -> str:
        """Get detailed state for a specific router interface on an SR OS node.

        Args:
            node: Node short name (e.g. "dcgw1").
            interface_name: Interface name (e.g. "to_spine1", "system").
        """
        hostname = nodes.get(node)
        if not hostname:
            return f"Error: unknown node {node!r}. Available: {list(nodes)}"
        path = f"nokia-state:state/router[router-name=Base]/interface[interface-name={interface_name}]"
        data = gnmi_get(hostname, path)
        if data is None:
            return f"Error: could not retrieve interface {interface_name!r} from {node} ({hostname})"
        return format_node_results({node: data})

    @mcp.tool()
    def sros_set_interface_description(
        node: str,
        interface_name: str,
        description: str,
        dry_run: bool = False,
    ) -> str:
        """Set the description on a router interface in the Base routing instance.

        Args:
            node: Node short name (e.g. "dcgw1").
            interface_name: Interface name (e.g. "to_spine1").
            description: New description string.
            dry_run: If True, show what would be sent without making changes.
        """
        hostname = nodes.get(node)
        if not hostname:
            return f"Error: unknown node {node!r}. Available: {list(nodes)}"
        path = f"nokia-conf:configure/router[router-name=Base]/interface[interface-name={interface_name}]"
        value = {"description": description}
        if dry_run:
            return format_dry_run(node, path, value)
        result = gnmi_set(hostname, path, value, operation="update")
        if result is None:
            return f"Error: failed to set description on {interface_name!r} of {node}"
        return f"OK: description on {interface_name!r} of {node} set to {description!r}"
