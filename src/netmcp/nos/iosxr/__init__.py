"""Cisco IOS-XR NOS backend — placeholder."""

from netmcp.registry import NotImplementedBackend

NOS_TYPE = "iosxr"
BACKEND = NotImplementedBackend("iosxr", transport="netconf")
