"""SR OS implementation of the NOSBackend Protocol.

Implements the domains that have hand-written gNMI context modules:
  system, interfaces, BGP, EVPN (read/write).

All other domains (IGP, MPLS/SR, VRF, Logging) fall through to
NotImplementedBackend and will be filled in during Phase 4.
"""

from pydantic import BaseModel, Field, ValidationError

from netmcp.inventory import NodeInfo
from netmcp.nos.sros.client import gnmi_get, gnmi_set
from netmcp.registry import NotImplementedBackend
from netmcp.utils.formatters import format_dry_run, format_node_results


class _VplsIntent(BaseModel):
    service_name: str = Field(..., min_length=1)
    service_id: int = Field(..., ge=1, le=2_147_483_647)
    vni: int = Field(..., ge=1, le=16_777_215)
    evi: int = Field(..., ge=1, le=65_535)
    route_distinguisher: str  # e.g. "1:31"
    export_rt: str            # e.g. "target:65011:1"
    import_rt: str


class SROSBackend(NotImplementedBackend):
    """Nokia SR OS backend. Dispatched to by unified tools."""

    def __init__(self) -> None:
        super().__init__(nos_type="sros", transport="gnmi")

    # ------------------------------------------------------------------
    # System
    # ------------------------------------------------------------------

    def get_system_info(self, node: NodeInfo) -> str:
        """Return platform, software version, and uptime from nokia-state:state/system."""
        data = gnmi_get(node, "nokia-state:state/system")
        if data is None:
            return f"Error: could not retrieve system info from {node.name} ({node.fqdn})"
        return format_node_results({node.name: data})

    def get_system_alarms(self, node: NodeInfo) -> str:
        """Return all active system alarms from nokia-state:state/system/alarm."""
        data = gnmi_get(node, "nokia-state:state/system/alarm")
        if data is None:
            return f"No alarms found or could not retrieve alarms from {node.name} ({node.fqdn})"
        return format_node_results({node.name: data})

    # ------------------------------------------------------------------
    # Interfaces
    # ------------------------------------------------------------------

    def get_ports(self, node: NodeInfo) -> str:
        """Return a summary of all physical ports (port-id, admin/oper state, description).

        Reads nokia-state:state/port and normalises each entry to a flat dict
        so the response stays concise regardless of how many counters SR OS exposes.
        """
        data = gnmi_get(node, "nokia-state:state/port")
        if data is None:
            return f"Error: could not retrieve ports from {node.name} ({node.fqdn})"
        ports = data if isinstance(data, list) else [data]
        summary = [
            {
                "port-id": p.get("nokia-state:port-id"),
                "admin-state": p.get("nokia-state:admin-state"),
                "oper-state": p.get("nokia-state:oper-state"),
                "description": p.get("nokia-state:description", ""),
            }
            for p in ports
        ]
        return format_node_results({node.name: summary})

    def get_interfaces(self, node: NodeInfo) -> str:
        """Return all logical interfaces in the Base routing instance."""
        data = gnmi_get(node, "nokia-state:state/router[router-name=Base]/interface")
        if data is None:
            return f"Error: could not retrieve interfaces from {node.name} ({node.fqdn})"
        return format_node_results({node.name: data})

    def get_interface_state(self, node: NodeInfo, interface_name: str) -> str:
        """Return detailed operational state for a single interface in the Base routing instance."""
        path = f"nokia-state:state/router[router-name=Base]/interface[interface-name={interface_name}]"
        data = gnmi_get(node, path)
        if data is None:
            return f"Error: could not retrieve interface {interface_name!r} from {node.name} ({node.fqdn})"
        return format_node_results({node.name: data})

    def set_interface_description(
        self, node: NodeInfo, interface_name: str, description: str, dry_run: bool
    ) -> str:
        """Set the description field on a Base-instance interface via gNMI update.

        Targets nokia-conf:configure/router[router-name=Base]/interface[...].
        When dry_run is True the formatted payload is returned without sending it.
        """
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
        """Return aggregate BGP statistics (total prefixes, peer counts) for the Base instance."""
        data = gnmi_get(node, "nokia-state:state/router[router-name=Base]/bgp/statistics")
        if data is None:
            return f"Error: could not retrieve BGP summary from {node.name} ({node.fqdn})"
        return format_node_results({node.name: data})

    def get_bgp_neighbors(self, node: NodeInfo) -> str:
        """Return state for all BGP neighbors in the Base routing instance."""
        data = gnmi_get(node, "nokia-state:state/router[router-name=Base]/bgp/neighbor")
        if data is None:
            return f"Error: could not retrieve BGP neighbors from {node.name} ({node.fqdn})"
        return format_node_results({node.name: data})

    def get_bgp_neighbor(self, node: NodeInfo, peer_ip: str) -> str:
        """Return detailed state for a single BGP neighbor identified by its IP address."""
        path = f"nokia-state:state/router[router-name=Base]/bgp/neighbor[ip-address={peer_ip}]"
        data = gnmi_get(node, path)
        if data is None:
            return f"Error: could not retrieve BGP neighbor {peer_ip!r} from {node.name} ({node.fqdn})"
        return format_node_results({node.name: data})

    def get_bgp_config(self, node: NodeInfo) -> str:
        """Return the BGP configuration block for the Base routing instance."""
        data = gnmi_get(node, "nokia-conf:configure/router[router-name=Base]/bgp")
        if data is None:
            return f"Error: could not retrieve BGP config from {node.name} ({node.fqdn})"
        return format_node_results({node.name: data})

    # ------------------------------------------------------------------
    # EVPN (unified read — normalises VPLS config to a common schema)
    # ------------------------------------------------------------------

    def get_evpn_instances(self, node: NodeInfo) -> str:
        """Return all EVPN instances normalised to {name, type, vni, evi}.

        Reads the full VPLS service list from nokia-conf and extracts only the
        fields that are meaningful across NOS types, hiding SR OS-specific structure.
        """
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

    def get_evpn_instance(self, node: NodeInfo, instance_name: str) -> str:
        """Return the full configuration for a single VPLS service by name."""
        path = f"nokia-conf:configure/service/vpls[service-name={instance_name}]"
        data = gnmi_get(node, path)
        if data is None:
            return f"Service {instance_name!r} not found on {node.name} ({node.fqdn})"
        return format_node_results({node.name: data})

    def get_evpn_instance_state(self, node: NodeInfo, instance_name: str) -> str:
        """Return the operational state for a single VPLS service by name."""
        path = f"nokia-state:state/service/vpls[service-name={instance_name}]"
        data = gnmi_get(node, path)
        if data is None:
            return f"Service {instance_name!r} state not found on {node.name} ({node.fqdn})"
        return format_node_results({node.name: data})

    def provision_evpn_instance(
        self,
        node: NodeInfo,
        service_name: str,
        service_id: int,
        vni: int,
        evi: int,
        route_distinguisher: str,
        export_rt: str,
        import_rt: str,
        dry_run: bool,
    ) -> str:
        """Create a VPLS service with BGP-EVPN and VXLAN via gNMI update.

        Inputs are validated against _VplsIntent before any network call.
        The payload enables the service, binds a VXLAN instance to the VNI,
        sets BGP RD/RT, and activates BGP-EVPN with ECMP=8.
        When dry_run is True the formatted payload is returned without sending it.
        """
        try:
            _VplsIntent(
                service_name=service_name,
                service_id=service_id,
                vni=vni,
                evi=evi,
                route_distinguisher=route_distinguisher,
                export_rt=export_rt,
                import_rt=import_rt,
            )
        except ValidationError as e:
            messages = [f"  - {err['loc'][0] if err['loc'] else 'unknown'}: {err['msg']}" for err in e.errors()]
            return "Validation error(s):\n" + "\n".join(messages)

        path = f"nokia-conf:configure/service/vpls[service-name={service_name}]"
        value = {
            "service-name": service_name,
            "service-id": service_id,
            "admin-state": "enable",
            "customer": "1",
            "vxlan": {"instance": [{"id": 1, "vni": vni}]},
            "bgp": [{"bgp-instance": 1, "route-distinguisher": route_distinguisher, "route-target": {"export": export_rt, "import": import_rt}}],
            "bgp-evpn": {"evi": evi, "vxlan": [{"bgp-instance": 1, "admin-state": "enable", "vxlan-instance": 1, "ecmp": 8}]},
        }
        if dry_run:
            return format_dry_run(node.name, path, value)
        result = gnmi_set(node, path, value, operation="update")
        if result is None:
            return f"Error: failed to provision VPLS {service_name!r} on {node.name} ({node.fqdn})"
        return (
            f"OK: VPLS {service_name!r} (service-id={service_id}, VNI={vni}, EVI={evi}, "
            f"RD={route_distinguisher}, export-RT={export_rt}, import-RT={import_rt}) "
            f"provisioned on {node.name}."
        )

    def delete_evpn_instance(self, node: NodeInfo, instance_name: str, dry_run: bool) -> str:
        """Delete a VPLS service by name via gNMI delete.

        When dry_run is True the target path is printed without sending the delete.
        """
        path = f"nokia-conf:configure/service/vpls[service-name={instance_name}]"
        if dry_run:
            return (
                f"[DRY RUN] Node: {node.name}\n"
                f"  Operation: DELETE\n"
                f"  Path:      {path}"
            )
        result = gnmi_set(node, path, None, operation="delete")
        if result is None:
            return f"Error: failed to delete VPLS {instance_name!r} on {node.name} ({node.fqdn})"
        return f"OK: VPLS {instance_name!r} deleted from {node.name}."
