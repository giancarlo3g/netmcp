"""SR OS implementation of the NOSBackend Protocol.

Implements the domains that have hand-written gNMI context modules:
  system, interfaces, BGP, EVPN (read).

All other domains (IGP, MPLS/SR, VRF, Logging) fall through to
NotImplementedBackend and will be filled in during Phase 4.
"""

from netmcp.inventory import NodeInfo
from netmcp.nos.sros.client import gnmi_get, gnmi_set
from netmcp.registry import NotImplementedBackend
from netmcp.utils.formatters import format_dry_run, format_node_results


class SROSBackend(NotImplementedBackend):
    """Nokia SR OS backend. Dispatched to by unified tools."""

    def __init__(self) -> None:
        super().__init__(nos_type="sros", transport="gnmi")

    # ------------------------------------------------------------------
    # System
    # ------------------------------------------------------------------

    def get_system_info(self, node: NodeInfo) -> str:
        data = gnmi_get(node, "nokia-state:state/system")
        if data is None:
            return f"Error: could not retrieve system info from {node.name} ({node.fqdn})"
        return format_node_results({node.name: data})

    def get_system_alarms(self, node: NodeInfo) -> str:
        data = gnmi_get(node, "nokia-state:state/system/alarm")
        if data is None:
            return f"No alarms found or could not retrieve alarms from {node.name} ({node.fqdn})"
        return format_node_results({node.name: data})

    # ------------------------------------------------------------------
    # Interfaces
    # ------------------------------------------------------------------

    def get_interfaces(self, node: NodeInfo) -> str:
        data = gnmi_get(node, "nokia-state:state/router[router-name=Base]/interface")
        if data is None:
            return f"Error: could not retrieve interfaces from {node.name} ({node.fqdn})"
        return format_node_results({node.name: data})

    def get_interface_state(self, node: NodeInfo, interface_name: str) -> str:
        path = f"nokia-state:state/router[router-name=Base]/interface[interface-name={interface_name}]"
        data = gnmi_get(node, path)
        if data is None:
            return f"Error: could not retrieve interface {interface_name!r} from {node.name} ({node.fqdn})"
        return format_node_results({node.name: data})

    def set_interface_description(
        self, node: NodeInfo, interface_name: str, description: str, dry_run: bool
    ) -> str:
        path = f"nokia-conf:configure/router[router-name=Base]/interface[interface-name={interface_name}]"
        value = {"description": description}
        if dry_run:
            return format_dry_run(node.name, path, value)
        result = gnmi_set(node, path, value, operation="update")
        if result is None:
            return f"Error: failed to set description on {interface_name!r} of {node.name}"
        return f"OK: description on {interface_name!r} of {node.name} set to {description!r}"

    # ------------------------------------------------------------------
    # BGP
    # ------------------------------------------------------------------

    def get_bgp_summary(self, node: NodeInfo) -> str:
        data = gnmi_get(node, "nokia-state:state/router[router-name=Base]/bgp/statistics")
        if data is None:
            return f"Error: could not retrieve BGP summary from {node.name} ({node.fqdn})"
        return format_node_results({node.name: data})

    def get_bgp_neighbors(self, node: NodeInfo) -> str:
        data = gnmi_get(node, "nokia-state:state/router[router-name=Base]/bgp/neighbor")
        if data is None:
            return f"Error: could not retrieve BGP neighbors from {node.name} ({node.fqdn})"
        return format_node_results({node.name: data})

    def get_bgp_neighbor(self, node: NodeInfo, peer_ip: str) -> str:
        path = f"nokia-state:state/router[router-name=Base]/bgp/neighbor[ip-address={peer_ip}]"
        data = gnmi_get(node, path)
        if data is None:
            return f"Error: could not retrieve BGP neighbor {peer_ip!r} from {node.name} ({node.fqdn})"
        return format_node_results({node.name: data})

    def get_bgp_config(self, node: NodeInfo) -> str:
        data = gnmi_get(node, "nokia-conf:configure/router[router-name=Base]/bgp")
        if data is None:
            return f"Error: could not retrieve BGP config from {node.name} ({node.fqdn})"
        return format_node_results({node.name: data})

    # ------------------------------------------------------------------
    # EVPN (unified read — normalises VPLS config to a common schema)
    # ------------------------------------------------------------------

    def get_evpn_instances(self, node: NodeInfo) -> str:
        data = gnmi_get(node, "nokia-conf:configure/service/vpls")
        if data is None:
            return f"No EVPN instances found on {node.name} ({node.fqdn})"
        services = data if isinstance(data, list) else [data]
        instances = []
        for svc in services:
            vni = None
            evi = None
            vxlan_instances = svc.get("vxlan", {}).get("instance", [])
            if vxlan_instances:
                vni = vxlan_instances[0].get("vni")
            evi = svc.get("bgp-evpn", {}).get("evi")
            instances.append({
                "name": svc.get("service-name"),
                "type": "vpls",
                "vni": vni,
                "evi": evi,
            })
        return format_node_results({node.name: instances})
