"""netmcp — FastMCP orchestrator."""

import logging
import os

from mcp.server.fastmcp import FastMCP

from netmcp.dispatch import register_unified_tools
from netmcp.inventory import load_nodes
from netmcp.nos import eos, iosxr, junos, srl, sros

# pygnmi attaches a StreamHandler(sys.stdout) at import, which corrupts the MCP
# stream in stdio mode. Drop it; its records still reach the root (stderr) handler.
for _h in list(logging.getLogger("pygnmi.client").handlers):
    logging.getLogger("pygnmi.client").removeHandler(_h)

mcp = FastMCP(
    "netmcp",
    host=os.environ.get("MCP_HOST", "127.0.0.1"),
    port=int(os.environ.get("MCP_PORT") or "8000"),
)

# Inventory, resolved once at import time.
NODES = load_nodes()

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
    # MCP_PORT set → streamable HTTP on MCP_HOST:MCP_PORT/mcp; unset or empty → stdio.
    transport = "streamable-http" if os.environ.get("MCP_PORT") else "stdio"
    mcp.run(transport=transport)


if __name__ == "__main__":
    run()
