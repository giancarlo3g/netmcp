"""netmcp — FastMCP orchestrator."""

import os

from mcp.server.fastmcp import FastMCP

from netmcp.dispatch import register_unified_tools
from netmcp.inventory import NODES
from netmcp.nos import eos, iosxr, junos, srl, sros

mcp = FastMCP(
    "netmcp",
    host=os.environ.get("MCP_HOST", "127.0.0.1"),
    port=int(os.environ.get("MCP_PORT", "8000")),
)

# Registry maps nos_type → backend singleton.
# Each backend is either a full implementation or a NotImplementedBackend stub.
REGISTRY = {
    "sros":  sros.BACKEND,
    "srl":   srl.BACKEND,
    "eos":   eos.BACKEND,
    "junos": junos.BACKEND,
    "iosxr": iosxr.BACKEND,
}

# Unified tools — work across all NOS, dispatch via REGISTRY.
register_unified_tools(mcp, NODES, REGISTRY)


def run():
    import os
    transport = "streamable-http" if os.environ.get("MCP_PORT") else "stdio"
    mcp.run(transport=transport)


if __name__ == "__main__":
    run()
