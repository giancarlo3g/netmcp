# Nokia SR OS Backend

**Transport:** gNMI (port 57400, no TLS) with the native Nokia YANG models (`nokia-conf`, `nokia-state`).

## Implemented

Through the unified tools:

- System: `get_system_info`, `get_system_alarms`
- Ports and interfaces: `get_ports`, `get_interfaces`, `get_interface_state`, `set_interface_description`
- BGP (Base router): `get_bgp_summary`, `get_bgp_neighbors`, `get_bgp_neighbor`, `get_bgp_config`
- EVPN (VPLS): `get_evpn_instances`, `get_evpn_instance`, `get_evpn_instance_state`, `provision_evpn_instance`, `delete_evpn_instance`

IGP, MPLS/SR, VRF and logging are not implemented yet and return a "not implemented" error.

## Path conventions

- Config: `nokia-conf:configure/...`
- State: `nokia-state:state/...`
- List keys use unquoted values: `router[router-name=Base]`, `service/vpls[service-name=1]`

| Tool | Path |
|---|---|
| `get_system_info` | `nokia-state:state/system` |
| `get_system_alarms` | `nokia-state:state/system/alarm` |
| `get_ports` | `nokia-state:state/port` (flattened to port-id, admin/oper state, description) |
| `get_interfaces` | `nokia-state:state/router[router-name=Base]/interface` |
| `get_interface_state` | `nokia-state:state/router[router-name=Base]/interface[interface-name=X]` |
| `set_interface_description` | `nokia-conf:configure/router[router-name=Base]/interface[interface-name=X]` (update `description`) |
| `get_bgp_summary` | `nokia-state:state/router[router-name=Base]/bgp/statistics` |
| `get_bgp_neighbors` | `nokia-state:state/router[router-name=Base]/bgp/neighbor` |
| `get_bgp_neighbor` | `nokia-state:state/router[router-name=Base]/bgp/neighbor[ip-address=X]` |
| `get_bgp_config` | `nokia-conf:configure/router[router-name=Base]/bgp` |
| `get_evpn_instances`, `get_evpn_instance` | `nokia-conf:configure/service/vpls[service-name=X]` |
| `get_evpn_instance_state` | `nokia-state:state/service/vpls[service-name=X]` |
| `provision_evpn_instance`, `delete_evpn_instance` | `nokia-conf:configure/service/vpls[service-name=X]` |

## BGP

The BGP tools return the raw `nokia-state`/`nokia-conf` trees. They don't use the compact per-peer view that EOS, SR Linux, Junos and NX-OS share, so the summary is the Base router's `statistics` container (peer counts, `bgp-paths`, routes per family).

## EVPN

One EVPN instance is one VPLS service with BGP-EVPN over VXLAN:

```
service vpls "<name>" service-id <id> customer 1
    vxlan instance 1 vni <vni>
    bgp 1 route-distinguisher <rd> route-target export <rt> import <rt>
    bgp-evpn evi <evi> vxlan bgp-instance 1 vxlan-instance 1 ecmp 8
```

Notes:

- Provision needs `service_id` and `evi`. `interface_name` and `vlan_id` are ignored, and no SAP is created.
- Inputs are validated with `_VplsIntent` before any network call.
- Provision is one gNMI update, and delete is one gNMI delete of the VPLS.
- `get_evpn_instances` lists every VPLS as `{name, type: vpls, vni, evi}`.

## Containerlab kind

`nokia_srsim` → auto-discovered as `nos_type = "sros"`, with `gnmi_port = 57400`. Default password `NokiaSros1!`; `SROS_PASSWORD` is accepted as a legacy override.
