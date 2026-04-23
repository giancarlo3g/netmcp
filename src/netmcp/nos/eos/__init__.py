"""Arista EOS NOS backend — placeholder."""

from netmcp.registry import NotImplementedBackend

NOS_TYPE = "eos"
BACKEND = NotImplementedBackend("eos", transport="gnmi")


def register_vendor_tools(mcp, nodes) -> None:
    pass
