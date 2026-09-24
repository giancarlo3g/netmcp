# Juniper Junos Backend

`JunOSBackend` implements the 4 BGP methods (`get_bgp_summary`, `get_bgp_neighbors`,
`get_bgp_neighbor`, `get_bgp_config`) and the 5 EVPN methods (`get_evpn_instances`,
`get_evpn_instance`, `get_evpn_instance_state`, `provision_evpn_instance`,
`delete_evpn_instance`) over gNMI, using YANG only (no `cli:` origin).
Every other method falls through to `NotImplementedBackend`.

Tested against cJunos Evolved 25.4R1 (`juniper_cjunosevolved`).

**Transport:** gNMI via pygnmi, port 32767, no TLS.

## Enabling gNMI on the node

```
set system services extension-service request-response grpc clear-text port 32767
```

## How Junos serves gNMI

| Need | RPC | Path | Encoding |
|---|---|---|---|
| Config | Get (`type=CONFIG` only) | `juniper:/configuration/...` (native `junos-conf-*` YANG) | JSON_IETF |
| Config writes | Set | `juniper:/configuration/...`, values shaped like the Get reply | JSON_IETF |
| State | Subscribe, mode ONCE | OpenConfig, e.g. `/network-instances/network-instance[name=DEFAULT]/...` | PROTO |

- Get rejects `STATE`/`ALL`, so operational state is only available over Subscribe.
- Get reads the `openconfig` origin by default. That tree is empty unless the node was
  configured through OpenConfig, so native config needs the `juniper` origin.
- The OpenConfig default instance and BGP protocol are both keyed `DEFAULT`:
  `network-instance[name=DEFAULT]/protocols/protocol[identifier=BGP][name=DEFAULT]`.
- Subscribe needs `no_qos_marking=True` (Junos answers "Qos not supported" otherwise),
  and only PROTO/JSON encodings. `client.gnmi_subscribe_once()` reads the raw protobuf
  stream because pygnmi's `subscribe2()` parser cannot decode `leaflist_val`. It merges
  the per-leaf updates into a nested dict rooted at the requested path, so the backend
  can reuse `utils/openconfig.py` like EOS does.
- An unknown key (e.g. a neighbor that does not exist) returns only a `sync_response`.
- One SetRequest is one commit: if any update or delete is rejected, nothing is applied,
  so writes need no rollback. `client.gnmi_set_batch()` sends updates and deletes together.
- Several subscriptions in one request are served one after another (5 paths took ~11 s
  vs ~5 s for one), so prefer one Subscribe on a common parent.

## EVPN

An EVPN instance is a vlan-based EVPN-VXLAN `mac-vrf` routing-instance plus the access
unit it bridges:

```
routing-instances EVPN-VXLAN10 {
    instance-type mac-vrf;  service-type vlan-based;
    protocols evpn { encapsulation vxlan; extended-vni-list 1010; }
    vtep-source-interface lo0.0;
    interface et-0/0/2.10;
    route-distinguisher 10:3;  vrf-target target:65000:10;
    vlans vlan10 { vlan-id 10; interface et-0/0/2.10; vxlan { vni 1010; ingress-node-replication; } }
}
interfaces et-0/0/2 { flexible-vlan-tagging; encapsulation flexible-ethernet-services;
                      unit 10 { encapsulation vlan-bridge; vlan-id 10; } }
```

- Config: `juniper:/configuration/routing-instances/instance[name=X]` and
  `juniper:/configuration/interfaces/interface[name=et-0/0/2]/unit[name=10]`.
  Only `instance-type mac-vrf` instances are listed. Lookup is by instance name, VLAN
  name or VLAN id. The VLAN id is reported as the EVI.
- State: one Subscribe on `/network-instances/network-instance[name=X]` (OpenConfig
  type `evpn`). `get_evpn_instance_state` picks `state` (RD), `vlan` (status, VNI,
  local MACs), `jnx-evpn` (VXLAN tunnel end-points, EVPN peers, interface status),
  `mac-table-info` and `inter-instance-policies` (RTs). `.../jnx-evpn` can be subscribed
  on its own, but `.../evpn` is rejected. The root reply also carries the instance's
  BGP loc-rib and fdb, so it grows with the MAC count.
- `provision_evpn_instance` writes the unit and the routing-instance in one Set.
  `interface_name` is `et-0/0/2` (unit = `vlan_id`) or `et-0/0/2.20`. The parent port
  must already have `flexible-vlan-tagging` and `encapsulation flexible-ethernet-services`,
  and is never modified. `export_rt` must equal `import_rt` (one `vrf-target community`).
  The VTEP source is always `lo0.0`. `evi`/`service_id` are ignored. It refuses an existing
  instance name, a VNI already used by a mac-vrf, or an existing unit.
- `delete_evpn_instance` deletes the routing-instance and its units in one Set, leaving
  the parent port untouched.

## Containerlab kinds

`juniper_cjunosevolved`, `juniper_vjunosevolved`, `juniper_vjunosrouter` → `nos_type = "junos"`.
