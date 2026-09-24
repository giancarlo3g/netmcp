"""Juniper JunOS NOS backend — placeholder."""

from netmcp.registry import NotImplementedBackend

NOS_TYPE = "junos"
BACKEND = NotImplementedBackend("junos", transport="netconf")
