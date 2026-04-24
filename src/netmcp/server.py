"""netmcp — FastMCP orchestrator."""

from mcp.server.fastmcp import FastMCP

from netmcp.dispatch import register_unified_tools
from netmcp.inventory import NODES
from netmcp.nos import eos, iosxr, junos, srl, sros

mcp = FastMCP("netmcp")

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

# Vendor-specific tools — registered per NOS with their own prefix (sros_*, srl_*, ...).
sros_nodes = {name: info for name, info in NODES.items() if info.nos_type == "sros"}
sros.register_vendor_tools(mcp, sros_nodes)


def run():
    mcp.run()


if __name__ == "__main__":
    run()
