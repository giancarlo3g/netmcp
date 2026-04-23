"""EVPN context — VPLS service lifecycle."""

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, ValidationError

from router_mcp.client import gnmi_get, gnmi_set
from router_mcp.utils.formatters import format_node_results, format_dry_run


class _VplsIntent(BaseModel):
    service_name: str = Field(..., min_length=1)
    service_id: int = Field(..., ge=1, le=2_147_483_647)
    vni: int = Field(..., ge=1, le=16_777_215)
    evi: int = Field(..., ge=1, le=65_535)
    route_distinguisher: str  # e.g. "1:31"
    export_rt: str            # e.g. "target:65011:1"
    import_rt: str


def register(mcp: FastMCP, nodes: dict[str, str]) -> None:

    @mcp.tool()
    def sros_evpn_list_services(node: str) -> str:
        """List all VPLS services configured on an SR OS node.

        Args:
            node: Node short name (e.g. "dcgw1").
        """
        hostname = nodes.get(node)
        if not hostname:
            return f"Error: unknown node {node!r}. Available: {list(nodes)}"
        data = gnmi_get(hostname, "nokia-conf:configure/service/vpls")
        if data is None:
            return f"No VPLS services found or error retrieving from {node} ({hostname})"
        return format_node_results({node: data})

    @mcp.tool()
    def sros_evpn_get_service(node: str, service_name: str) -> str:
        """Get the configuration for a specific VPLS service on an SR OS node.

        Args:
            node: Node short name (e.g. "dcgw1").
            service_name: VPLS service name (e.g. "1").
        """
        hostname = nodes.get(node)
        if not hostname:
            return f"Error: unknown node {node!r}. Available: {list(nodes)}"
        path = f"nokia-conf:configure/service/vpls[service-name={service_name}]"
        data = gnmi_get(hostname, path)
        if data is None:
            return f"Service {service_name!r} not found on {node} ({hostname})"
        return format_node_results({node: data})

    @mcp.tool()
    def sros_evpn_get_service_state(node: str, service_name: str) -> str:
        """Get the operational state for a specific VPLS service on an SR OS node.

        Args:
            node: Node short name (e.g. "dcgw1").
            service_name: VPLS service name (e.g. "1").
        """
        hostname = nodes.get(node)
        if not hostname:
            return f"Error: unknown node {node!r}. Available: {list(nodes)}"
        path = f"nokia-state:state/service/vpls[service-name={service_name}]"
        data = gnmi_get(hostname, path)
        if data is None:
            return f"Service {service_name!r} state not found on {node} ({hostname})"
        return format_node_results({node: data})

    @mcp.tool()
    def sros_evpn_provision_vpls(
        node: str,
        service_name: str,
        service_id: int,
        vni: int,
        evi: int,
        route_distinguisher: str,
        export_rt: str,
        import_rt: str,
        dry_run: bool = False,
    ) -> str:
        """Provision an EVPN VPLS service with BGP-EVPN and VXLAN on an SR OS node.

        Creates a VPLS service with:
          - VXLAN instance bound to the given VNI
          - BGP route-distinguisher and route-targets
          - BGP-EVPN with the given EVI, enabled via VXLAN

        Always call sros_evpn_get_service first to confirm the service does not
        already exist before provisioning.

        Args:
            node: Node short name (e.g. "dcgw1").
            service_name: Name for the VPLS service (e.g. "2").
            service_id: Numeric service ID (1-2147483647).
            vni: VXLAN Network Identifier (1-16777215).
            evi: EVPN Instance number (1-65535).
            route_distinguisher: BGP route-distinguisher (e.g. "1:31").
            export_rt: BGP export route-target (e.g. "target:65011:1").
            import_rt: BGP import route-target (e.g. "target:65011:1").
            dry_run: If True, show what would be sent without making changes.
        """
        hostname = nodes.get(node)
        if not hostname:
            return f"Error: unknown node {node!r}. Available: {list(nodes)}"

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
            messages = []
            for err in e.errors():
                field = err["loc"][0] if err["loc"] else "unknown"
                messages.append(f"  - {field}: {err['msg']}")
            return "Validation error(s):\n" + "\n".join(messages)

        path = f"nokia-conf:configure/service/vpls[service-name={service_name}]"
        value = {
            "service-name": service_name,
            "service-id": service_id,
            "admin-state": "enable",
            "customer": "1",
            "vxlan": {
                "instance": [{"id": 1, "vni": vni}]
            },
            "bgp": [{
                "bgp-instance": 1,
                "route-distinguisher": route_distinguisher,
                "route-target": {
                    "export": export_rt,
                    "import": import_rt,
                },
            }],
            "bgp-evpn": {
                "evi": evi,
                "vxlan": [{
                    "bgp-instance": 1,
                    "admin-state": "enable",
                    "vxlan-instance": 1,
                    "ecmp": 8,
                }],
            },
        }

        if dry_run:
            return format_dry_run(node, path, value)

        result = gnmi_set(hostname, path, value, operation="update")
        if result is None:
            return f"Error: failed to provision VPLS {service_name!r} on {node} ({hostname})"
        return (
            f"OK: VPLS {service_name!r} (service-id={service_id}, VNI={vni}, EVI={evi}, "
            f"RD={route_distinguisher}, export-RT={export_rt}, import-RT={import_rt}) "
            f"provisioned on {node}."
        )

    @mcp.tool()
    def sros_evpn_delete_vpls(
        node: str,
        service_name: str,
        dry_run: bool = False,
    ) -> str:
        """Delete a VPLS service from an SR OS node.

        Always call sros_evpn_get_service first to confirm the service exists
        before deleting.

        Args:
            node: Node short name (e.g. "dcgw1").
            service_name: VPLS service name to delete (e.g. "1").
            dry_run: If True, show what would be deleted without making changes.
        """
        hostname = nodes.get(node)
        if not hostname:
            return f"Error: unknown node {node!r}. Available: {list(nodes)}"

        path = f"nokia-conf:configure/service/vpls[service-name={service_name}]"

        if dry_run:
            return (
                f"[DRY RUN] Node: {node}\n"
                f"  Operation: DELETE\n"
                f"  Path:      {path}"
            )

        result = gnmi_set(hostname, path, None, operation="delete")
        if result is None:
            return f"Error: failed to delete VPLS {service_name!r} on {node} ({hostname})"
        return f"OK: VPLS {service_name!r} deleted from {node}."
