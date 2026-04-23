"""netmcp — FastMCP orchestrator."""

from mcp.server.fastmcp import FastMCP

from netmcp.inventory import NODES
from netmcp.nos import sros

mcp = FastMCP("netmcp")

# Route nodes to their NOS backend
sros_nodes = {name: info for name, info in NODES.items() if info.nos_type == "sros"}

sros.register_vendor_tools(mcp, sros_nodes)


def run():
    mcp.run()


if __name__ == "__main__":
    run()
