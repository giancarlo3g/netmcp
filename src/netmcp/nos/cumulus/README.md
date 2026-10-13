# NVIDIA Cumulus Linux

**Status:** BGP reads (`get_bgp_summary`, `get_bgp_neighbors`, `get_bgp_neighbor`, `get_bgp_config`), verified live on Cumulus VX 5.16.1 (`mv-nvidia`) through the MCP tools on 2026-10-13. Every other method returns a "not implemented" error. Design: [`docs/prd-cumulus-bgp.md`](../../../../docs/prd-cumulus-bgp.md).

## Transport: NVUE REST API

NVUE is the Cumulus Linux 5.x object model. The `nv` CLI and the REST API are two front ends to it. The REST API is defined by NVIDIA's **OpenAPI 3** specification (see the [NVUE API docs](https://docs.nvidia.com/networking-ethernet-software/cumulus-linux/System-Configuration/NVIDIA-User-Experience-NVUE/NVUE-API/)), and it serves the model tree as URLs. For example, `nv show vrf default router bgp neighbor` corresponds to `GET /nvue_v1/vrf/default/router/bgp/neighbor`.

Cumulus has no usable YANG API, so this NOS uses the OpenAPI allowance in rule 7: gNMI (port 9339) has no Get, and Subscribe returned no data on 5.16. The client:
- issues only `GET`;
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

## Reply notes (5.16.1)

- The operational neighbor list is keyed by peer IP and carries `session-state`, `remote-as`, `uptime`, `last-reset-reason`, `message-stats`, and `address-family.<afi>.{rx-prefix,tx-prefix}` for the active families only.
- An unknown neighbor returns HTTP 404 with an RFC 7807 JSON body. A wrong password returns HTTP 401 with an nginx HTML body.
- The VRF-level config reports `autonomous-system` and `router-id` as `auto`; the real values are under `router/bgp`.
- Config replies include neighbor password hashes (`$nvsec$…`). `get_bgp_config` redacts them.

## Known gaps

- The BGP summary has no `total-paths` / `total-prefixes`, and per-AFI counts have no `installed`.
- Only the `default` VRF.
- No writes.
