"""Cisco IOS-XR NOS backend — placeholder."""

from netmcp.registry import NotImplementedBackend

NOS_TYPE = "iosxr"
BACKEND = NotImplementedBackend("iosxr", transport="netconf")


def register_vendor_tools(mcp, nodes) -> None:
    pass
