"""SR Linux implementation of the NOSBackend Protocol.

Implements EVPN (MAC-VRF) read and write operations via gNMI.

SRL EVPN uses a resource-centric model — three gNMI objects per service:
  1. Bridged subinterface on an Ethernet port (VLAN-tagged access)
  2. VXLAN tunnel interface (vxlan0.{vni})
  3. MAC-VRF network-instance with BGP-EVPN + BGP-VPN

All other domains fall through to NotImplementedBackend.
"""

import re

from pydantic import BaseModel, Field, ValidationError, field_validator

from netmcp.inventory import NodeInfo
from netmcp.nos.srl.client import gnmi_get, gnmi_set
from netmcp.registry import NotImplementedBackend
from netmcp.utils.formatters import format_dry_run, format_node_results

_FORBIDDEN_INTERFACES = {"ethernet-1/51", "ethernet-1/52", "system0", "mgmt0"}


class _MacVrfIntent(BaseModel):
    service_name: str = Field(..., min_length=1, strip_whitespace=True)
    vni: int = Field(..., ge=1, le=16_777_215)
    evi: int = Field(..., ge=1, le=65_535)
    interface_name: str
    vlan_id: int = Field(..., ge=2, le=4094)
    route_distinguisher: str  # e.g. "10:11"
    export_rt: str             # e.g. "target:65000:100"
    import_rt: str

    @field_validator("interface_name")
    @classmethod
    def validate_interface_name(cls, v: str) -> str:
        if v in _FORBIDDEN_INTERFACES:
            raise ValueError(
                f"{v!r} is a forbidden interface. "
                "Do not use uplinks (ethernet-1/51, ethernet-1/52), system0, or mgmt0."
            )
        if not re.match(r"^ethernet-1/([1-9]|[1-4][0-9]|5[0-8])$", v):
            raise ValueError(
                f"{v!r} is not a valid access port. "
                "Only ethernet-1/1 through ethernet-1/58 are permitted."
            )
        return v


def _rollback(node: NodeInfo, service_name: str, vni: int, interface_name: str, vlan_id: int) -> None:
    """Best-effort rollback of a partially provisioned MAC-VRF."""
    gnmi_set(node, f"/network-instance[name={service_name}]", None, operation="delete")
    gnmi_set(node, f"/tunnel-interface[name=vxlan0]/vxlan-interface[index={vni}]", None, operation="delete")
    gnmi_set(node, f"/interface[name={interface_name}]/subinterface[index={vlan_id}]", None, operation="delete")


class SRLBackend(NotImplementedBackend):
    """Nokia SR Linux backend. Dispatched to by unified tools."""

    def __init__(self) -> None:
        super().__init__(nos_type="srl", transport="gnmi")

    # ------------------------------------------------------------------
    # EVPN — read
    # ------------------------------------------------------------------

    def get_evpn_instances(self, node: NodeInfo) -> str:
        """Return all MAC-VRF network-instances normalised to {name, type, vni, evi}."""
        data = gnmi_get(node, "/network-instance")
        if data is None:
            return f"No EVPN instances found on {node.name} ({node.fqdn})"
        instances_raw = data if isinstance(data, list) else [data]
        instances = []
        for ni in instances_raw:
            if ni.get("type") not in ("mac-vrf", "srl_nokia-network-instance:mac-vrf"):
                continue
            vni = None
            evi = None
            vxlan_ifaces = ni.get("vxlan-interface", [])
            if vxlan_ifaces:
                # vxlan-interface name is like "vxlan0.100" — suffix is the VNI
                vxlan_name = vxlan_ifaces[0].get("name", "")
                parts = vxlan_name.rsplit(".", 1)
                if len(parts) == 2 and parts[1].isdigit():
                    vni = int(parts[1])
            bgp_evpn = ni.get("protocols", {}).get("bgp-evpn", {})
            bgp_instances = bgp_evpn.get("bgp-instance", [])
            if bgp_instances:
                evi = bgp_instances[0].get("evi")
            instances.append({
                "name": ni.get("name"),
                "type": "mac-vrf",
                "vni": vni,
                "evi": evi,
            })
        if not instances:
            return f"No MAC-VRF instances found on {node.name} ({node.fqdn})"
        return format_node_results({node.name: instances})

    def get_evpn_instance(self, node: NodeInfo, instance_name: str) -> str:
        """Return the full configuration for a single MAC-VRF network-instance."""
        data = gnmi_get(node, f"/network-instance[name={instance_name}]")
        if data is None:
            return f"Service {instance_name!r} not found on {node.name} ({node.fqdn})"
        return format_node_results({node.name: data})

    def get_evpn_instance_state(self, node: NodeInfo, instance_name: str) -> str:
        """Return the operational state for a single MAC-VRF network-instance.

        SR Linux exposes config and state in the same YANG tree via gNMI,
        so this returns the same path as get_evpn_instance but is kept
        separate for consistency with the unified tool interface.
        """
        data = gnmi_get(node, f"/network-instance[name={instance_name}]")
        if data is None:
            return f"Service {instance_name!r} state not found on {node.name} ({node.fqdn})"
        return format_node_results({node.name: data})

    # ------------------------------------------------------------------
    # EVPN — write
    # ------------------------------------------------------------------

    def provision_evpn_instance(
        self,
        node: NodeInfo,
        service_name: str,
        service_id: int,  # not used by SRL; present for Protocol compatibility
        vni: int,
        evi: int,
        route_distinguisher: str,
        export_rt: str,
        import_rt: str,
        dry_run: bool,
        interface_name: str = "",
        vlan_id: int = 0,
    ) -> str:
        """Create a MAC-VRF service on an SR Linux node via three gNMI SET operations.

        Requires interface_name (e.g. "ethernet-1/3") and vlan_id (e.g. 100) which
        are SRL-specific and not needed by SROS. Returns an error if they are missing.

        Steps:
          1. Configure bridged subinterface with VLAN encapsulation
          2. Create VXLAN tunnel interface
          3. Create MAC-VRF network-instance with BGP-EVPN + BGP-VPN
        Rolls back all three on any failure.
        """
        if not interface_name or vlan_id == 0:
            return (
                "Error: SR Linux MAC-VRF provisioning requires 'interface_name' "
                "(e.g. 'ethernet-1/3') and 'vlan_id' (2-4094)."
            )

        try:
            _MacVrfIntent(
                service_name=service_name,
                vni=vni,
                evi=evi,
                interface_name=interface_name,
                vlan_id=vlan_id,
                route_distinguisher=route_distinguisher,
                export_rt=export_rt,
                import_rt=import_rt,
            )
        except ValidationError as e:
            messages = [f"  - {err['loc'][0] if err['loc'] else 'unknown'}: {err['msg']}" for err in e.errors()]
            return "Validation error(s):\n" + "\n".join(messages)

        subif_name = f"{interface_name}.{vlan_id}"
        vxlan_iface_name = f"vxlan0.{vni}"

        iface_path = f"/interface[name={interface_name}]"
        iface_value = {
            "admin-state": "enable",
            "vlan-tagging": True,
            "subinterface": [{
                "index": vlan_id,
                "type": "bridged",
                "admin-state": "enable",
                "vlan": {"encap": {"single-tagged": {"vlan-id": vlan_id}}},
            }],
        }

        vxlan_path = f"/tunnel-interface[name=vxlan0]/vxlan-interface[index={vni}]"
        vxlan_value = {
            "index": vni,
            "type": "bridged",
            "ingress": {"vni": vni},
        }

        ni_path = f"/network-instance[name={service_name}]"
        ni_value = {
            "name": service_name,
            "type": "mac-vrf",
            "interface": [{"name": subif_name}],
            "vxlan-interface": [{"name": vxlan_iface_name}],
            "protocols": {
                "bgp-evpn": {
                    "bgp-instance": [{
                        "id": 1,
                        "admin-state": "enable",
                        "evi": evi,
                        "vxlan-interface": vxlan_iface_name,
                        "ecmp": 2,
                    }]
                },
                "bgp-vpn": {
                    "bgp-instance": [{
                        "id": 1,
                        "route-distinguisher": {"rd": route_distinguisher},
                        "route-target": {
                            "export-rt": export_rt,
                            "import-rt": import_rt,
                        },
                    }]
                },
            },
        }

        if dry_run:
            return "\n\n".join([
                format_dry_run(node.name, iface_path, iface_value),
                format_dry_run(node.name, vxlan_path, vxlan_value),
                format_dry_run(node.name, ni_path, ni_value),
            ])

        r1 = gnmi_set(node, iface_path, iface_value, operation="update")
        r2 = gnmi_set(node, vxlan_path, vxlan_value, operation="update")
        r3 = gnmi_set(node, ni_path, ni_value, operation="update")

        if not (r1 and r2 and r3):
            _rollback(node, service_name, vni, interface_name, vlan_id)
            steps = (
                f"subinterface={'OK' if r1 else 'FAIL'}, "
                f"vxlan-interface={'OK' if r2 else 'FAIL'}, "
                f"network-instance={'OK' if r3 else 'FAIL'}"
            )
            return (
                f"Error: provisioning failed on {node.name} ({steps}). "
                "All changes have been rolled back."
            )

        return (
            f"OK: MAC-VRF {service_name!r} (VNI={vni}, EVI={evi}, "
            f"iface={subif_name}, RD={route_distinguisher}, "
            f"export-RT={export_rt}, import-RT={import_rt}) "
            f"provisioned on {node.name}."
        )

    def delete_evpn_instance(self, node: NodeInfo, instance_name: str, dry_run: bool) -> str:
        """Delete a MAC-VRF service and its associated VXLAN interface and subinterface.

        Reads the running config first to discover the VXLAN interface name and
        access subinterface, then deletes in order:
          1. network-instance (releases references)
          2. vxlan-interface
          3. access subinterface
        """
        data = gnmi_get(node, f"/network-instance[name={instance_name}]")
        if data is None:
            return f"Service {instance_name!r} not found on {node.name} ({node.fqdn})"

        # Extract vxlan-interface name (e.g. "vxlan0.100") and parse VNI from suffix
        vxlan_ifaces = data.get("vxlan-interface", [])
        vxlan_iface_name = vxlan_ifaces[0].get("name", "") if vxlan_ifaces else ""
        vni = None
        if vxlan_iface_name:
            parts = vxlan_iface_name.rsplit(".", 1)
            if len(parts) == 2 and parts[1].isdigit():
                vni = int(parts[1])

        # Extract subinterface name (e.g. "ethernet-1/3.100") and split into iface + index
        iface_entries = data.get("interface", [])
        subif_name = iface_entries[0].get("name", "") if iface_entries else ""
        interface_name = None
        vlan_id = None
        if subif_name and "." in subif_name:
            parts = subif_name.rsplit(".", 1)
            if len(parts) == 2 and parts[1].isdigit():
                interface_name = parts[0]
                vlan_id = int(parts[1])

        ni_path = f"/network-instance[name={instance_name}]"
        vxlan_path = f"/tunnel-interface[name=vxlan0]/vxlan-interface[index={vni}]" if vni is not None else None
        subif_path = f"/interface[name={interface_name}]/subinterface[index={vlan_id}]" if interface_name and vlan_id is not None else None

        if dry_run:
            lines = [
                f"[DRY RUN] Node: {node.name}",
                f"  Operation: DELETE",
                f"  Path:      {ni_path}",
            ]
            if vxlan_path:
                lines += ["", f"[DRY RUN] Node: {node.name}", "  Operation: DELETE", f"  Path:      {vxlan_path}"]
            if subif_path:
                lines += ["", f"[DRY RUN] Node: {node.name}", "  Operation: DELETE", f"  Path:      {subif_path}"]
            return "\n".join(lines)

        r1 = gnmi_set(node, ni_path, None, operation="delete")
        r2 = gnmi_set(node, vxlan_path, None, operation="delete") if vxlan_path else None
        r3 = gnmi_set(node, subif_path, None, operation="delete") if subif_path else None

        if r1 and (r2 is not False) and (r3 is not False):
            return f"OK: MAC-VRF {instance_name!r} deleted from {node.name}."
        steps = (
            f"network-instance={'OK' if r1 else 'FAIL'}"
            + (f", vxlan-interface={'OK' if r2 else 'FAIL'}" if vxlan_path else "")
            + (f", subinterface={'OK' if r3 else 'FAIL'}" if subif_path else "")
        )
        return f"Error: partial deletion on {node.name} ({steps})."
