"""Juniper JunOS NOS backend — placeholder."""

from netmcp.registry import NotImplementedBackend

NOS_TYPE = "junos"
BACKEND = NotImplementedBackend("junos", transport="netconf")


def register_vendor_tools(mcp, nodes) -> None:
    pass
