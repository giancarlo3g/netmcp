"""Nokia SR OS NOS backend."""

from mcp.server.fastmcp import FastMCP

from netmcp.inventory import NodeInfo
from netmcp.nos.sros.backend import SROSBackend
from netmcp.nos.sros.contexts import bgp, evpn, interfaces, system

NOS_TYPE = "sros"
BACKEND = SROSBackend()


def register_vendor_tools(mcp: FastMCP, nodes: dict[str, NodeInfo]) -> None:
    """Register all sros_* vendor tools on the MCP instance."""
    system.register(mcp, nodes)
    interfaces.register(mcp, nodes)
    bgp.register(mcp, nodes)
    evpn.register(mcp, nodes)
