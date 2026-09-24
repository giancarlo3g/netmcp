# Juniper Junos Backend

`JunOSBackend` implements the 4 BGP methods (`get_bgp_summary`, `get_bgp_neighbors`,
`get_bgp_neighbor`, `get_bgp_config`) over gNMI, using YANG only (no `cli:` origin).
Every other method falls through to `NotImplementedBackend`.

Tested against cJunos Evolved 25.4R1 (`juniper_cjunosevolved`).

**Transport:** gNMI via pygnmi, port 57400, no TLS.

## Enabling gNMI on the node

```
set system services extension-service request-response grpc clear-text port 57400
```

## How Junos serves gNMI

| Need | RPC | Path | Encoding |
|---|---|---|---|
| Config | Get (`type=CONFIG` only) | `juniper:/configuration/...` (native `junos-conf-*` YANG) | JSON_IETF |
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

## Containerlab kinds

`juniper_cjunosevolved`, `juniper_vjunosevolved`, `juniper_vjunosrouter` → `nos_type = "junos"`.
