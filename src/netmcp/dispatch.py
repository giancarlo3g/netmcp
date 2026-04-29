"""Unified tool dispatcher.

Registers vendor-agnostic tools that route to the correct NOS backend based
on each node's nos_type. These tools work across all supported NOS — the
caller never needs to know the vendor.

Vendor-specific tools (sros_*, srl_*, etc.) are registered separately by
each NOS module and coexist with these unified tools.
"""

from mcp.server.fastmcp import FastMCP

from netmcp.inventory import NodeInfo
from netmcp.registry import NOSBackend


def _resolve(nodes: dict[str, NodeInfo], registry: dict[str, NOSBackend], node: str):
    """Return (NodeInfo, NOSBackend) or (None, error_string)."""
    info = nodes.get(node)
    if not info:
        return None, f"Error: unknown node {node!r}. Available: {list(nodes)}"
    backend = registry.get(info.nos_type)
    if not backend:
        return None, f"Error: no backend registered for NOS '{info.nos_type}' (node {node!r})."
    return info, backend


def register_unified_tools(
    mcp: FastMCP,
    nodes: dict[str, NodeInfo],
    registry: dict[str, NOSBackend],
) -> None:
    """Register all unified tools on the MCP instance."""

    # ------------------------------------------------------------------
    # System
    # ------------------------------------------------------------------

    @mcp.tool()
    def list_nodes() -> str:
        """List all nodes in the inventory with their NOS type and hostname."""
        if not nodes:
            return "No nodes in inventory."
        lines = [
            f"  * {name}  nos={info.nos_type}  fqdn={info.fqdn}"
            + (f"  tags={info.tags}" if info.tags else "")
            for name, info in nodes.items()
        ]
        return "Nodes:\n" + "\n".join(lines)

    @mcp.tool()
    def get_system_info(node: str) -> str:
        """Get system information (platform, software version, uptime) from any node.

        Args:
            node: Node short name (e.g. "dcgw1").
        """
        info, backend = _resolve(nodes, registry, node)
        if info is None:
            return backend  # error string
        return backend.get_system_info(info)

    @mcp.tool()
    def get_system_alarms(node: str) -> str:
        """Get active system alarms from any node.

        Args:
            node: Node short name (e.g. "dcgw1").
        """
        info, backend = _resolve(nodes, registry, node)
        if info is None:
            return backend
        return backend.get_system_alarms(info)

    # ------------------------------------------------------------------
    # Interfaces
    # ------------------------------------------------------------------

    @mcp.tool()
    def get_ports(node: str) -> str:
        """Get all physical ports on any node.

        Args:
            node: Node short name (e.g. "dcgw1").
        """
        info, backend = _resolve(nodes, registry, node)
        if info is None:
            return backend
        return backend.get_ports(info)

    @mcp.tool()
    def get_interfaces(node: str) -> str:
        """Get all logical interfaces from any node.

        Args:
            node: Node short name (e.g. "dcgw1").
        """
        info, backend = _resolve(nodes, registry, node)
        if info is None:
            return backend
        return backend.get_interfaces(info)

    @mcp.tool()
    def get_interface_state(node: str, interface_name: str) -> str:
        """Get detailed operational state for a specific interface on any node.

        Args:
            node: Node short name (e.g. "dcgw1").
            interface_name: Interface name (e.g. "to_spine1", "system").
        """
        info, backend = _resolve(nodes, registry, node)
        if info is None:
            return backend
        return backend.get_interface_state(info, interface_name)

    @mcp.tool()
    def set_interface_description(
        node: str,
        interface_name: str,
        description: str,
        dry_run: bool = False,
    ) -> str:
        """Set the description on an interface on any node.

        Args:
            node: Node short name (e.g. "dcgw1").
            interface_name: Interface name (e.g. "to_spine1").
            description: New description string.
            dry_run: If True, show what would be sent without making changes.
        """
        info, backend = _resolve(nodes, registry, node)
        if info is None:
            return backend
        return backend.set_interface_description(info, interface_name, description, dry_run)

    # ------------------------------------------------------------------
    # BGP
    # ------------------------------------------------------------------

    @mcp.tool()
    def get_bgp_summary(node: str) -> str:
        """Get BGP summary statistics from any node.

        Args:
            node: Node short name (e.g. "dcgw1").
        """
        info, backend = _resolve(nodes, registry, node)
        if info is None:
            return backend
        return backend.get_bgp_summary(info)

    @mcp.tool()
    def get_bgp_neighbors(node: str) -> str:
        """Get all BGP neighbor states from any node.

        Args:
            node: Node short name (e.g. "dcgw1").
        """
        info, backend = _resolve(nodes, registry, node)
        if info is None:
            return backend
        return backend.get_bgp_neighbors(info)

    @mcp.tool()
    def get_bgp_neighbor(node: str, peer_ip: str) -> str:
        """Get detailed state for a specific BGP neighbor on any node.

        Args:
            node: Node short name (e.g. "dcgw1").
            peer_ip: Neighbor IP address (e.g. "10.0.0.21").
        """
        info, backend = _resolve(nodes, registry, node)
        if info is None:
            return backend
        return backend.get_bgp_neighbor(info, peer_ip)

    @mcp.tool()
    def get_bgp_config(node: str) -> str:
        """Get the BGP configuration from any node.

        Args:
            node: Node short name (e.g. "dcgw1").
        """
        info, backend = _resolve(nodes, registry, node)
        if info is None:
            return backend
        return backend.get_bgp_config(info)

    # ------------------------------------------------------------------
    # EVPN
    # ------------------------------------------------------------------

    @mcp.tool()
    def get_evpn_instances(node: str) -> str:
        """List EVPN instances on any node, normalised to {name, type, vni, evi}.

        Returns a common representation regardless of NOS-specific service model
        (VPLS on SR OS, mac-vrf on SR Linux, etc.).

        Args:
            node: Node short name (e.g. "dcgw1").
        """
        info, backend = _resolve(nodes, registry, node)
        if info is None:
            return backend
        return backend.get_evpn_instances(info)

    @mcp.tool()
    def get_evpn_instance(node: str, instance_name: str) -> str:
        """Get the configuration for a specific EVPN instance on any node.

        Args:
            node: Node short name (e.g. "dcgw1").
            instance_name: EVPN instance name (e.g. "1").
        """
        info, backend = _resolve(nodes, registry, node)
        if info is None:
            return backend
        return backend.get_evpn_instance(info, instance_name)

    @mcp.tool()
    def get_evpn_instance_state(node: str, instance_name: str) -> str:
        """Get the operational state for a specific EVPN instance on any node.

        Args:
            node: Node short name (e.g. "dcgw1").
            instance_name: EVPN instance name (e.g. "1").
        """
        info, backend = _resolve(nodes, registry, node)
        if info is None:
            return backend
        return backend.get_evpn_instance_state(info, instance_name)

    @mcp.tool()
    def provision_evpn_instance(
        node: str,
        service_name: str,
        service_id: int,
        vni: int,
        evi: int,
        route_distinguisher: str,
        export_rt: str,
        import_rt: str,
        dry_run: bool = False,
        interface_name: str = "",
        vlan_id: int = 0,
    ) -> str:
        """Provision an EVPN instance (VPLS + BGP-EVPN + VXLAN) on any node.

        Always call get_evpn_instance first to confirm the instance does not
        already exist before provisioning.

        Args:
            node: Node short name (e.g. "dcgw1").
            service_name: Name for the EVPN instance (e.g. "2").
            service_id: Numeric service ID (1-2147483647). Required for SR OS; ignored for SR Linux.
            vni: VXLAN Network Identifier (1-16777215).
            evi: EVPN Instance number (1-65535).
            route_distinguisher: BGP route-distinguisher (e.g. "1:31").
            export_rt: BGP export route-target (e.g. "target:65011:1").
            import_rt: BGP import route-target (e.g. "target:65011:1").
            dry_run: If True, show what would be sent without making changes.
            interface_name: Access port to attach (e.g. "ethernet-1/3"). Required for SR Linux.
            vlan_id: VLAN ID for the bridged subinterface (2-4094). Required for SR Linux.
        """
        info, backend = _resolve(nodes, registry, node)
        if info is None:
            return backend
        return backend.provision_evpn_instance(
            info, service_name, service_id, vni, evi,
            route_distinguisher, export_rt, import_rt, dry_run,
            interface_name, vlan_id,
        )

    @mcp.tool()
    def delete_evpn_instance(node: str, instance_name: str, dry_run: bool = False) -> str:
        """Delete an EVPN instance from any node.

        Always call get_evpn_instance first to confirm the instance exists
        before deleting.

        Args:
            node: Node short name (e.g. "dcgw1").
            instance_name: EVPN instance name to delete (e.g. "1").
            dry_run: If True, show what would be deleted without making changes.
        """
        info, backend = _resolve(nodes, registry, node)
        if info is None:
            return backend
        return backend.delete_evpn_instance(info, instance_name, dry_run)

    # ------------------------------------------------------------------
    # IGP
    # ------------------------------------------------------------------

    @mcp.tool()
    def get_isis_adjacencies(node: str) -> str:
        """Get all IS-IS adjacencies and their state from any node.

        Args:
            node: Node short name (e.g. "dcgw1").
        """
        info, backend = _resolve(nodes, registry, node)
        if info is None:
            return backend
        return backend.get_isis_adjacencies(info)

    @mcp.tool()
    def get_isis_database(node: str) -> str:
        """Get the IS-IS link-state database (LSDB) summary from any node.

        Args:
            node: Node short name (e.g. "dcgw1").
        """
        info, backend = _resolve(nodes, registry, node)
        if info is None:
            return backend
        return backend.get_isis_database(info)

    @mcp.tool()
    def get_isis_config(node: str) -> str:
        """Get the IS-IS instance configuration from any node.

        Args:
            node: Node short name (e.g. "dcgw1").
        """
        info, backend = _resolve(nodes, registry, node)
        if info is None:
            return backend
        return backend.get_isis_config(info)

    @mcp.tool()
    def get_ospf_neighbors(node: str) -> str:
        """Get all OSPF neighbor states from any node.

        Args:
            node: Node short name (e.g. "dcgw1").
        """
        info, backend = _resolve(nodes, registry, node)
        if info is None:
            return backend
        return backend.get_ospf_neighbors(info)

    @mcp.tool()
    def get_ospf_config(node: str) -> str:
        """Get the OSPF instance configuration from any node.

        Args:
            node: Node short name (e.g. "dcgw1").
        """
        info, backend = _resolve(nodes, registry, node)
        if info is None:
            return backend
        return backend.get_ospf_config(info)

    # ------------------------------------------------------------------
    # MPLS / Segment Routing
    # ------------------------------------------------------------------

    @mcp.tool()
    def get_mpls_lsps(node: str) -> str:
        """Get active MPLS LSPs from any node.

        Args:
            node: Node short name (e.g. "dcgw1").
        """
        info, backend = _resolve(nodes, registry, node)
        if info is None:
            return backend
        return backend.get_mpls_lsps(info)

    @mcp.tool()
    def get_sr_sid_table(node: str) -> str:
        """Get the Segment Routing SID binding table from any node.

        Args:
            node: Node short name (e.g. "dcgw1").
        """
        info, backend = _resolve(nodes, registry, node)
        if info is None:
            return backend
        return backend.get_sr_sid_table(info)

    @mcp.tool()
    def get_sr_config(node: str) -> str:
        """Get the Segment Routing configuration from any node.

        Args:
            node: Node short name (e.g. "dcgw1").
        """
        info, backend = _resolve(nodes, registry, node)
        if info is None:
            return backend
        return backend.get_sr_config(info)

    # ------------------------------------------------------------------
    # VRF / L3VPN
    # ------------------------------------------------------------------

    @mcp.tool()
    def get_vrfs(node: str) -> str:
        """Get all VRFs (or network-instances) from any node.

        Args:
            node: Node short name (e.g. "dcgw1").
        """
        info, backend = _resolve(nodes, registry, node)
        if info is None:
            return backend
        return backend.get_vrfs(info)

    @mcp.tool()
    def get_vrf_routes(node: str, vrf_name: str) -> str:
        """Get the route table for a specific VRF from any node.

        Args:
            node: Node short name (e.g. "dcgw1").
            vrf_name: VRF or network-instance name.
        """
        info, backend = _resolve(nodes, registry, node)
        if info is None:
            return backend
        return backend.get_vrf_routes(info, vrf_name)

    @mcp.tool()
    def get_vrf_interfaces(node: str, vrf_name: str) -> str:
        """Get the interfaces bound to a specific VRF from any node.

        Args:
            node: Node short name (e.g. "dcgw1").
            vrf_name: VRF or network-instance name.
        """
        info, backend = _resolve(nodes, registry, node)
        if info is None:
            return backend
        return backend.get_vrf_interfaces(info, vrf_name)

    # ------------------------------------------------------------------
    # Logging / Events
    # ------------------------------------------------------------------

    @mcp.tool()
    def get_log_events(node: str, count: int = 50, severity: str = "") -> str:
        """Get recent log events from any node.

        Args:
            node: Node short name (e.g. "dcgw1").
            count: Number of most-recent entries to return (default 50).
            severity: Filter by minimum severity (e.g. "major", "critical"). Empty = all.
        """
        info, backend = _resolve(nodes, registry, node)
        if info is None:
            return backend
        return backend.get_log_events(info, count, severity)

    @mcp.tool()
    def get_log_config(node: str) -> str:
        """Get the logging destinations configuration from any node.

        Args:
            node: Node short name (e.g. "dcgw1").
        """
        info, backend = _resolve(nodes, registry, node)
        if info is None:
            return backend
        return backend.get_log_config(info)
