# Nokia SR Linux Backend

**Transport:** gNMI (port 57400, TLS with certificate verification skipped) with native SR Linux YANG, no origin or prefix.

## Implemented

EVPN (MAC-VRF) through the unified tools: `get_evpn_instances`, `get_evpn_instance`,
`get_evpn_instance_state`, `provision_evpn_instance`, `delete_evpn_instance`.
BGP (default network-instance) through `get_bgp_summary`, `get_bgp_neighbors`, `get_bgp_neighbor` and `get_bgp_config`,
from `/network-instance[name=default]/protocols/bgp`.

All other domains return a "not implemented" error.

Replies are json_ietf with module-prefixed keys (e.g. `srl_nokia-network-instance:network-instance`),
so the backend reads them with `utils.yang.ns_get`. Config and state share one tree; `datatype="config"`
drops the state leaves.

## BGP

- `get_bgp_summary` / `get_bgp_neighbors` return the same compact per-peer view as EOS, Junos and NX-OS:
  - `session-state` is uppercased (`ESTABLISHED`).
  - Only AFI-SAFIs with `oper-state up` are listed, with names in OpenConfig spelling (`evpn` → `L2VPN_EVPN`, `ipv4-unicast` → `IPV4_UNICAST`).
  - `received-routes`/`sent-routes`/`active-routes` become `received`/`sent`/`installed`.
  - The summary also reports `total-paths`/`total-prefixes` from `statistics`.
- 64-bit counters (`total-paths`, `established-transitions`, …) arrive as strings and are converted to int.
- `get_bgp_neighbor` returns the raw native `neighbor[peer-address=X]` tree.
- `get_bgp_config` reads the BGP container with `datatype="config"`.

## EVPN

One MAC-VRF service is three gNMI objects:

| Piece | CLI | YANG path |
|---|---|---|
| Access subinterface | `interface ethernet-1/3 subinterface 10 type bridged vlan encap single-tagged vlan-id 10` | `/interface[name=ethernet-1/3]/subinterface[index=10]` |
| VXLAN tunnel interface | `tunnel-interface vxlan0 vxlan-interface 1010 type bridged ingress vni 1010` | `/tunnel-interface[name=vxlan0]/vxlan-interface[index=1010]` |
| MAC-VRF | `network-instance mac-vrf-10 type mac-vrf` + `protocols bgp-evpn` (EVI, VXLAN interface) + `protocols bgp-vpn` (RD, RT) | `/network-instance[name=mac-vrf-10]` |

Notes:

- `get_evpn_instances` lists only `mac-vrf` network-instances. The VNI is read from the tunnel interface's `ingress/vni`, not from the `vxlan0.N` index.
- `get_evpn_instance` and `get_evpn_instance_state` read the same path, because config and state share one tree.
- Provision needs `interface_name` (an access port, `ethernet-1/1` to `ethernet-1/58`) and `vlan_id` (2–4094). `service_id` is ignored.
  - The uplinks `ethernet-1/51` and `ethernet-1/52`, plus `system0` and `mgmt0`, are rejected.
  - Inputs are validated with `_MacVrfIntent` before any network call.
- Provision creates the bridged subinterface, then `vxlan0.<vni>` (index = VNI), then the MAC-VRF (`bgp-evpn` ECMP 2).
- Writes are three separate gNMI Sets, so they are not atomic. If any of them fails, `_rollback()` deletes all three objects on a best-effort basis.
- Delete reads the instance first to find its `vxlan0.N` and access subinterface. It then deletes the network-instance, the VXLAN interface and the subinterface, in that order.

## Containerlab kind

`nokia_srlinux` → auto-discovered as `nos_type = "srl"`, with `gnmi_port = 57400`. Default password `NokiaSrl1!`.
