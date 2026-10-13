# NVIDIA Cumulus Linux

**Status:**
- **BGP reads** (`get_bgp_summary`, `get_bgp_neighbors`, `get_bgp_neighbor`, `get_bgp_config`): verified live on Cumulus VX 5.16.1 (`mv-nvidia`) on 2026-10-13. Design: [`docs/prd-cumulus-bgp.md`](../../../../docs/prd-cumulus-bgp.md).
- **EVPN** (`get_evpn_instances`, `get_evpn_instance`, `get_evpn_instance_state`, `provision_evpn_instance`, `delete_evpn_instance`), VLAN-based in bridge `br_default`: verified live on `mv-nvidia` on 2026-10-13. VLAN 20 / VNI 1020 was provisioned and then deleted on swp1 through the MCP tools, with the safe-write protocol. Design: [`docs/prd-cumulus-evpn.md`](../../../../docs/prd-cumulus-evpn.md).

Every other method returns a "not implemented" error.

## Transport: NVUE REST API

NVUE is the Cumulus Linux 5.x object model. The `nv` CLI and the REST API are two front ends to it. The REST API is defined by NVIDIA's **OpenAPI 3** specification (see the [NVUE API docs](https://docs.nvidia.com/networking-ethernet-software/cumulus-linux/System-Configuration/NVIDIA-User-Experience-NVUE/NVUE-API/)), and it serves the model tree as URLs. For example, `nv show vrf default router bgp neighbor` corresponds to `GET /nvue_v1/vrf/default/router/bgp/neighbor`.

Cumulus has no usable YANG API, so this NOS uses the OpenAPI allowance in rule 7: gNMI (port 9339) has no Get, and Subscribe returned no data on 5.16. The client:
- reads with `nvue_get` (GET) and writes only through `nvue_apply` (one NVUE revision per change);
- never runs `nv` commands, SSH or `vtysh`.

| Setting | Value |
|---|---|
| Port | 8765, HTTPS, self-signed cert (not verified) |
| Auth | HTTP basic. User `cumulus` (inventory default); password from `NETMCP_<NODE>_PASSWORD` or `NETMCP_DEFAULT_PASSWORD`. There is no built-in default. |
| Revisions | `rev=applied` (running config), `rev=operational` (live state) |
| Proxy | Ignored (`trust_env=False`); through a corporate proxy the request returns nothing |

## Endpoints

| Method | NVUE paths |
|---|---|
| `get_bgp_summary` | `router/bgp` (applied) + `vrf/default/router/bgp/neighbor` (operational) |
| `get_bgp_neighbors` | `vrf/default/router/bgp/neighbor` (operational) |
| `get_bgp_neighbor` | `vrf/default/router/bgp/neighbor/<ip>` (operational) |
| `get_bgp_config` | `router/bgp` + `vrf/default/router/bgp` (applied) |

## EVPN

One instance is three NVUE objects:

| Object | NVUE path |
|---|---|
| VLAN → VNI | `bridge/domain/br_default/vlan/<N>/vni/<VNI>` |
| EVPN VNI | `evpn/vni/<VNI>` (`rd`, `route-target/{both\|import\|export}/<rt>`) |
| Access port | `interface/<swpN>/bridge/domain/br_default/vlan/<N>` (tagged) |

- Named `vlanN`; look it up by `vlanN`, VLAN id or VNI. The VLAN id is reported as the EVI. A requested `service_name` (e.g. `mac-vrf-20`) is ignored, because NVUE VLANs have no name; the instance becomes `vlan20`.
- Provision needs `interface_name` (already a `br_default` member) and `vlan_id` (2-3724). It never creates the bridge, NVE or EVPN settings, or bridge membership.
- Delete leaves the VLAN on a port where it's the only VLAN and reports it.
- State: VNI counters and remote VTEPs from `evpn/vni/<VNI>`, MACs from `evpn/vni/<VNI>/mac`. There is no up/down state for a VNI.

### Writes: NVUE revisions

`nvue_apply` performs:
1. `POST revision`
2. one `PATCH /nvue_v1/?rev=<id>` with the JSON tree (`null` unsets)
3. `PATCH revision/<id>` with `{"state": "apply", "auto-prompt": {"ays": "ays_yes"}}`
4. polling until `applied` / `applied_and_saved` (about 9 s on Cumulus VX)

On a 400 from the PATCH, an `invalid` / `ays_fail` / `apply_fail` state, or a 60 s timeout, it deletes the revision and the tool reports `no changes were applied`.

With `system config auto-save` enabled (the default), every applied revision is also saved to startup config.

## Device prerequisites

The REST API is enabled by default on Cumulus 5.x. If it listens on localhost only, expose it on the management address:

```
nv set system api listening-address <mgmt-ip>
nv config apply
```

Check it by hand:

```bash
curl -k --noproxy '*' -u "cumulus:$NETMCP_NVIDIA_PASSWORD" \
  'https://<node>:8765/nvue_v1/router/bgp?rev=applied'
```

## Troubleshooting

| Symptom | Cause |
|---|---|
| `curl` prints nothing | The request went through the corporate proxy. Add `--noproxy '*'`. (The client itself ignores proxy variables.) |
| HTTP 401 with an nginx HTML page | Wrong password. Check `NETMCP_<NODE>_PASSWORD`; in the `mv` lab it is `Clab123!`. |
| HTTP 404 with an `ItemDoesNotExist` JSON body | The object doesn't exist, e.g. an unknown neighbor. Tools report it as an `Error:` string. |
| Connection refused | NVUE is not listening on the management address. See "Device prerequisites". |
| A write fails with `NVUE apply invalid: …` | NVUE rejected the change semantically (e.g. `duplicate_vni`). Nothing was applied and the revision was deleted. |
| A pending revision is left on the switch | Not netmcp's: it deletes its own on failure. Inspect it with `nv config diff <id>` and remove it with `nv config detach` (on the switch) or `DELETE /nvue_v1/revision/<id>`. |
| Tools fail right after a code change | The Docker image wasn't rebuilt. Behind a proxy, pass it as build args (see `ARCHITECTURE.md`), then reconnect the MCP server. |

## Example (mv lab)

`get_bgp_summary(node="nvidia")`:

```json
{
  "nvidia": {
    "as": 65000,
    "router-id": "192.1.1.7",
    "peers": {"total": 2, "established": 2},
    "neighbors": [
      {"peer": "192.1.2.1", "peer-as": 65000, "state": "ESTABLISHED",
       "afi-safis": {"L2VPN_EVPN": {"received": 8, "sent": 1}}},
      {"peer": "192.1.2.2", "peer-as": 65000, "state": "ESTABLISHED",
       "afi-safis": {"L2VPN_EVPN": {"received": 0, "sent": 1}}}
    ]
  }
}
```

`get_evpn_instance_state(node="nvidia", instance_name="vlan10")` (trimmed to 2 VTEPs):

```json
{
  "nvidia": {
    "name": "vlan10", "vlan-id": 10, "vni": 1010, "access-interfaces": ["swp1"],
    "vni-state": {"bridge-domain": "br_default", "vxlan-interface": "vxlan48", "local-vtep": "192.1.1.7",
                  "tenant-vrf": "default", "mac-count": 3, "host-count": 1, "remote-vtep-count": 6},
    "vteps": [{"peer": "192.1.1.1", "flood": "HER"}, {"peer": "192.1.1.2", "flood": "HER"}],
    "macs": [{"mac": "00:c1:ab:00:00:03", "learned": "remote", "vtep": "192.1.1.3"}]
  }
}
```

Provision and delete through the MCP tools (live run):

```
provision_evpn_instance(node="nvidia", service_name="mac-vrf-20", service_id=0, vni=1020, evi=0,
                        route_distinguisher="20:7", export_rt="65000:20", import_rt="65000:20",
                        interface_name="swp1", vlan_id=20)
→ OK: EVPN instance 'vlan20' (VLAN=20, VNI=1020, iface=swp1, RD=20:7, export-RT=65000:20, import-RT=65000:20) provisioned on nvidia.

delete_evpn_instance(node="nvidia", instance_name="vlan20")
→ OK: EVPN instance 'vlan20' (VLAN=20, VNI=1020) deleted from nvidia.
```

A new VNI has no remote VTEPs or MACs until another leaf has the same VNI and RT. The EVPN routes nvidia sends to each route reflector go up by one (the VNI's type-3 route) while it exists.

## Reply notes (5.16.1)

- The operational neighbor list is keyed by peer IP and carries `session-state`, `remote-as`, `uptime`, `last-reset-reason`, `message-stats`, and `address-family.<afi>.{rx-prefix,tx-prefix}` for the active families only.
- An unknown neighbor returns HTTP 404 with an RFC 7807 JSON body. A wrong password returns HTTP 401 with an nginx HTML body.
- The VRF-level config reports `autonomous-system` and `router-id` as `auto`; the real values are under `router/bgp`.
- Config replies include neighbor password hashes (`$nvsec$…`). `get_bgp_config` redacts them.

## Known gaps

- The BGP summary has no `total-paths` / `total-prefixes`, and per-AFI counts have no `installed`.
- Only the `default` VRF and bridge `br_default`.
- EVPN: L2 VNIs only (no L3 VNI / symmetric IRB, ARP/ND suppression settings or multihoming).
