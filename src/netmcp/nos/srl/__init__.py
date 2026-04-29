"""Nokia SR Linux NOS backend."""

from netmcp.nos.srl.backend import SRLBackend

NOS_TYPE = "srl"
BACKEND = SRLBackend()


def register_vendor_tools(mcp, nodes) -> None:
    """SR Linux vendor tools — none yet; EVPN goes through unified dispatch."""
    pass
