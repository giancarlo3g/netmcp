# Arista EOS Backend

**Transport:** gNMI (port 6030, no TLS) with OpenConfig and Arista experimental YANG models, no CLI origin.

## Implemented

EVPN (VLAN-based) through the unified tools: `get_evpn_instances`, `get_evpn_instance`,
`get_evpn_instance_state`, `provision_evpn_instance`, `delete_evpn_instance`.
All other domains return a "not implemented" error.

An EOS EVPN instance is spread over four YANG objects:

| Piece | CLI | YANG path |
|---|---|---|
| VLAN | `vlan 10` / `name mac-vrf-10` | `/network-instances/network-instance[name=default]/vlans/vlan[vlan-id=10]` |
| VLAN→VNI | `interface Vxlan1` / `vxlan vlan 10 vni 1010` | `/interfaces/interface[name=Vxlan1]/arista-vxlan/vlan-to-vnis/vlan-to-vni[vlan=10]` |
| BGP EVPN | `router bgp` / `vlan 10` / `rd`, `route-target`, `redistribute learned` | `/arista/eos/evpn/evpn-instances/evpn-instance[name=10]` |
| Access trunk | `switchport trunk allowed vlan add 10` | `/interfaces/interface[name=Ethernet1]/ethernet/switched-vlan/config/trunk-vlans` |

Notes:

- Instances can be looked up by VLAN name (`mac-vrf-10`) or VLAN id (`10`). The evpn-instance key is the VLAN id.
- `evi` and `service_id` are ignored. VLAN-based EVPN is keyed by `vlan_id`, which is reported as the EVI.
- Route-targets are accepted as `target:65000:10` or `65000:10`.
- `interface_name` must be a trunk switchport. If its trunk list is empty (all VLANs allowed), it is left untouched.
- Provision and delete are sent as one atomic SetRequest, so no rollback is needed.

## Containerlab kind

`arista_ceos` → auto-discovered as `nos_type = "eos"`, with `gnmi_port = 6030`.

## References

- YANG models: https://github.com/aristanetworks/yang (`EOS-4.34.2F/experimental/eos/models/{evpn,vxlan}`)
