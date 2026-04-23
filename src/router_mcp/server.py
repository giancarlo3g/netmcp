"""FastMCP orchestrator for the SR OS MCP server."""

from mcp.server.fastmcp import FastMCP

from router_mcp.config import NODES
from router_mcp.contexts import system, interfaces, bgp, evpn

mcp = FastMCP("sros-mcp")

system.register(mcp, NODES)
interfaces.register(mcp, NODES)
bgp.register(mcp, NODES)
evpn.register(mcp, NODES)


def run():
    mcp.run()


if __name__ == "__main__":
    run()
