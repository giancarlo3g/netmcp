"""NOSBackend Protocol and NotImplementedBackend base class.

Every NOS directory exposes a BACKEND singleton that satisfies NOSBackend.
Placeholder directories use NotImplementedBackend directly.
"""

from typing import Protocol, runtime_checkable

from netmcp.inventory import NodeInfo


@runtime_checkable
class NOSBackend(Protocol):
    """Structural protocol that every NOS backend must satisfy."""

    nos_type: str
    transport: str

    # system
    def get_system_info(self, node: NodeInfo) -> str: ...
    def get_system_alarms(self, node: NodeInfo) -> str: ...

    # interfaces
    def get_interfaces(self, node: NodeInfo) -> str: ...
    def get_interface_state(self, node: NodeInfo, interface_name: str) -> str: ...

    # bgp
    def get_bgp_summary(self, node: NodeInfo) -> str: ...
    def get_bgp_neighbors(self, node: NodeInfo) -> str: ...
    def get_bgp_neighbor(self, node: NodeInfo, peer_ip: str) -> str: ...
    def get_bgp_config(self, node: NodeInfo) -> str: ...

    # igp
    def get_isis_adjacencies(self, node: NodeInfo) -> str: ...
    def get_ospf_neighbors(self, node: NodeInfo) -> str: ...

    # mpls / segment routing
    def get_mpls_lsps(self, node: NodeInfo) -> str: ...
    def get_sr_sid_table(self, node: NodeInfo) -> str: ...

    # vrf / l3vpn
    def get_vrfs(self, node: NodeInfo) -> str: ...
    def get_vrf_routes(self, node: NodeInfo, vrf_name: str) -> str: ...

    # evpn (unified read)
    def get_evpn_instances(self, node: NodeInfo) -> str: ...

    # logging
    def get_log_events(self, node: NodeInfo, count: int, severity: str) -> str: ...


class NotImplementedBackend:
    """Default backend for placeholder NOS directories.

    All methods return a clean error string so an unimplemented NOS node
    in the inventory never crashes the server.
    """

    def __init__(self, nos_type: str, transport: str = "none") -> None:
        self.nos_type = nos_type
        self.transport = transport

    def _not_impl(self, method: str) -> str:
        return f"Error: {method} is not implemented for NOS '{self.nos_type}'."

    def get_system_info(self, node: NodeInfo) -> str:
        return self._not_impl("get_system_info")

    def get_system_alarms(self, node: NodeInfo) -> str:
        return self._not_impl("get_system_alarms")

    def get_interfaces(self, node: NodeInfo) -> str:
        return self._not_impl("get_interfaces")

    def get_interface_state(self, node: NodeInfo, interface_name: str) -> str:
        return self._not_impl("get_interface_state")

    def get_bgp_summary(self, node: NodeInfo) -> str:
        return self._not_impl("get_bgp_summary")

    def get_bgp_neighbors(self, node: NodeInfo) -> str:
        return self._not_impl("get_bgp_neighbors")

    def get_bgp_neighbor(self, node: NodeInfo, peer_ip: str) -> str:
        return self._not_impl("get_bgp_neighbor")

    def get_bgp_config(self, node: NodeInfo) -> str:
        return self._not_impl("get_bgp_config")

    def get_isis_adjacencies(self, node: NodeInfo) -> str:
        return self._not_impl("get_isis_adjacencies")

    def get_ospf_neighbors(self, node: NodeInfo) -> str:
        return self._not_impl("get_ospf_neighbors")

    def get_mpls_lsps(self, node: NodeInfo) -> str:
        return self._not_impl("get_mpls_lsps")

    def get_sr_sid_table(self, node: NodeInfo) -> str:
        return self._not_impl("get_sr_sid_table")

    def get_vrfs(self, node: NodeInfo) -> str:
        return self._not_impl("get_vrfs")

    def get_vrf_routes(self, node: NodeInfo, vrf_name: str) -> str:
        return self._not_impl("get_vrf_routes")

    def get_evpn_instances(self, node: NodeInfo) -> str:
        return self._not_impl("get_evpn_instances")

    def get_log_events(self, node: NodeInfo, count: int, severity: str) -> str:
        return self._not_impl("get_log_events")
