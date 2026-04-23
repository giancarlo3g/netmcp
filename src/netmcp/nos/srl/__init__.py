"""Nokia SR Linux NOS backend — Phase 2."""

from netmcp.registry import NotImplementedBackend

NOS_TYPE = "srl"
BACKEND = NotImplementedBackend("srl", transport="gnmi")


def register_vendor_tools(mcp, nodes) -> None:
    """SR Linux vendor tools — implemented in Phase 2."""
    pass
