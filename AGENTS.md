# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync

# Run the MCP server
uv run netmcp

# Run unit tests (no lab required)
uv run pytest
```

## Architecture

This is an MCP (Model Context Protocol) server that exposes network routers from multiple vendors to LLM agents via gNMI (primary) or NETCONF (fallback). The entry point is `src/netmcp/server.py`, which instantiates a `FastMCP` object and registers all tools through a single unified dispatch path.

### Architecture rules (do not change)

This structure is fixed. Fit new work into it; do not restructure around it. `tests/unit/test_structure.py` enforces these rules. If it fails, fix the code, not the test.

1. **Tools live only in `dispatch.py`.** No `@mcp.tool`/`add_tool` anywhere else, no vendor-prefixed tools (`sros_*`, `eos_*`, …), no `register_vendor_tools`, no `contexts/` directories.
2. **One directory per NOS: `src/netmcp/nos/<nos>/`** with exactly:
   - `__init__.py`: only `NOS_TYPE = "<nos>"` and `BACKEND = <Class>()`; no functions.
   - `backend.py`: `<Class>(NotImplementedBackend)`. Public methods must be `NOSBackend` Protocol methods; everything else is a `_private` module-level helper.
   - `client.py`: transport only (gNMI/NETCONF get/set). No MCP imports, no formatting, no business logic.
   - `README.md`: optional.

   A placeholder NOS has only `__init__.py` (+ `README.md`) with `BACKEND = NotImplementedBackend("<nos>", ...)`. No other files or subdirectories.
3. **New capability = Protocol + dispatch.** Add the method to `NOSBackend` and `NotImplementedBackend` in `registry.py`, add one unified tool in `dispatch.py`, then implement it in the backends that support it. Vendor-specific arguments become optional unified parameters, as `interface_name`/`vlan_id` did for `provision_evpn_instance`.
4. **New NOS = new directory + one `REGISTRY` entry** in `server.py` (plus its containerlab kind in `inventory.CLAB_KIND_TO_NOS`). Nothing else registers anything.
5. **Shared helpers go in `utils/`** (`formatters.py`, `yang.py`), not copied between NOS directories and not imported across them (e.g. `nos/eos` must not import from `nos/srl`).
6. **Every implemented backend gets `tests/unit/test_<nos>_backend.py`**, with `gnmi_get`/set mocked using reply shapes captured from a live node.
7. **Device access is YANG-modeled APIs only: gNMI (gRPC), NETCONF, or RESTCONF.** Every read and write in `client.py` must target a YANG model (OpenConfig or the vendor's native YANG). Never use the CLI: no netmiko, paramiko/SSH screen-scraping, scrapli, or eAPI/NX-API command execution, and no CLI-based models or encodings that aren't YANG, such as gNMI's `cli:` origin, `ASCII` encoding for CLI text, or NETCONF RPCs that wrap CLI commands (`<command>`, `<cli>`). If a feature has no YANG path on a NOS, leave that method unimplemented (it falls back to `NotImplementedBackend`) rather than falling back to the CLI.
   - gNMI uses **pygnmi** today. NETCONF or RESTCONF may be added when a NOS needs them (e.g. `ncclient` for NETCONF, `httpx` for RESTCONF), but the library stays inside that NOS's `client.py` or a shared transport in `utils/`. It must not leak into `backend.py`, `dispatch.py`, or `registry.py`.

### Data flow

1. **`inventory.py`** — At import time, searches upward from cwd for `netmcp.yml` (static inventory) or a `containerlab/*.clab.yml` topology file. Builds `NODES: dict[str, NodeInfo]` mapping short node names (e.g. `"dcgw1"`) to `NodeInfo` objects containing the FQDN, NOS type, transport, and connection details.

2. **`server.py`** — Creates the `FastMCP("netmcp")` instance, builds `REGISTRY` (a `dict[str, NOSBackend]` mapping `nos_type` to backend singletons), then calls `dispatch.register_unified_tools(mcp, NODES, REGISTRY)`. There is no separate vendor-tool registration step.

3. **`dispatch.py`** — Registers 29 unified tools (e.g. `get_interfaces`, `get_bgp_summary`, `provision_evpn_instance`). Each tool calls `_resolve(nodes, registry, node)` to get `(NodeInfo, NOSBackend)`, then delegates to the matching Protocol method. Unknown nodes and unregistered NOS types return `"Error: ..."` strings — never exceptions.

4. **NOS modules** (`nos/sros/`, `nos/srl/`, `nos/eos/`, `nos/junos/`, `nos/iosxr/`) — Each exports `NOS_TYPE` and `BACKEND` (a singleton implementing `NOSBackend`). There are no vendor-specific tool registrations; all tools go through `dispatch.py`.

5. **`nos/sros/backend.py`** — `SROSBackend(NotImplementedBackend)` implements the `NOSBackend` Protocol for system, interfaces, BGP, and EVPN (read and write). Domains not yet implemented (IGP, MPLS/SR, VRF, Logging) fall through to `NotImplementedBackend` and return error strings.

6. **`nos/sros/client.py`** — Thin gNMI transport for SR OS. `gnmi_get(node: NodeInfo, path)` and `gnmi_set(node: NodeInfo, path, value, operation)` are the only public functions. A new `gNMIclient` is created per call because pygnmi consumes the client object on context exit.

7. **`nos/srl/`** — `SRLBackend` implements the 5 EVPN methods (MAC-VRF: bridged subinterface + `vxlan0.N` tunnel interface + `mac-vrf` network-instance) and the 4 BGP read methods, native SR Linux YANG. `client.py` exposes `gnmi_get(node, path, datatype="all")` and `gnmi_set(node, path, value, operation)`. Writes are three separate `gnmi_set` calls with a best-effort `_rollback()`. TLS with `skip_verify`. See "gNMI path conventions (SR Linux)".

8. **`nos/eos/`** — `EOSBackend` implements the 5 EVPN methods (VLAN-based EVPN) and the 4 BGP methods using OpenConfig + Arista experimental YANG, no CLI origin. `client.py` connects insecure (no TLS) and exposes `gnmi_get(node, path, datatype="all")` and `gnmi_set_batch(node, updates, deletes)`, which sends one atomic SetRequest, so provision/delete need no rollback. See "gNMI path conventions (EOS)".

9. **`nos/junos/`** — `JunOSBackend` implements the 4 BGP methods and the 5 EVPN methods (vlan-based EVPN-VXLAN `mac-vrf`), YANG only (no `cli:` origin). State comes from OpenConfig over Subscribe ONCE; config is read and written as native Junos YANG under the `juniper` origin. `client.py` exposes `gnmi_subscribe_once(node, path)` (nested dict rooted at `path`, or `None`), `gnmi_get_config(node, paths)` and `gnmi_set_batch(node, updates, deletes)` (one SetRequest = one commit, so no rollback). See "gNMI path conventions (Junos)".

10. **`utils/`** — `formatters.py` (`format_node_results`, `format_dry_run`), `yang.py` (`ns_get` — dict lookup that also matches module-prefixed json_ietf keys like `arista-exp-eos-vxlan:arista-vxlan`; `strip_prefix` for identityref values) and `openconfig.py` (`entries`, `leaf`, `peer_summary`, `active_afi_safis` — OpenConfig list/leaf walking and the compact BGP peer view shared by EOS and Junos). Use these rather than re-implementing reply parsing per backend.

11. **`registry.py`** — Defines the `NOSBackend` Protocol (the interface every NOS backend must satisfy) and `NotImplementedBackend` (the default for placeholder NOS directories).

### NodeInfo

`NodeInfo` is the single structure passed everywhere instead of a bare hostname string:

```python
@dataclass
class NodeInfo:
    name: str        # short name: "dcgw1"
    fqdn: str        # hostname: "clab-evpn-dcgw1"
    nos_type: str    # "sros" | "srl" | "eos" | "junos" | "iosxr"
    transport: str   # "gnmi" | "netconf"
    gnmi_port: int   # default 57400 (6030 for eos, 32767 for junos, via _NOS_GNMI_PORT_DEFAULTS)
    username: str    # default "admin"
    tags: list[str]
```

### gNMI path conventions (SR OS)

- Config paths: `nokia-conf:configure/...`
- State paths: `nokia-state:state/...`
- List keys use unquoted values: `router[router-name=Base]`, `service/vpls[service-name=1]`

### gNMI path conventions (SR Linux)

- Native YANG paths with no origin/prefix; replies are json_ietf with module-prefixed keys (parse with `ns_get`). Config and state share one tree; `datatype="config"` drops the state leaves.
- BGP (default network-instance): `/network-instance[name=default]/protocols/bgp` (`autonomous-system`, `router-id`, `statistics`, `group[group-name]`, `neighbor[peer-address=X]`). 64-bit counters (`total-paths`, `established-transitions`, …) arrive as strings; they are converted to int.
  - `get_bgp_summary` / `get_bgp_neighbors` return the same compact per-peer view as EOS and Junos: `session-state` is uppercased (`ESTABLISHED`), only AFI-SAFIs with `oper-state up` are listed, with names mapped to OpenConfig spelling (`evpn` → `L2VPN_EVPN`, `ipv4-unicast` → `IPV4_UNICAST`) and `received-routes`/`sent-routes`/`active-routes` → `received`/`sent`/`installed`. The summary also reports `total-paths`/`total-prefixes` from `statistics`.
  - `get_bgp_neighbor` returns the raw native neighbor tree. `get_bgp_config` reads `BGP_PATH` with `datatype="config"`.

### gNMI path conventions (EOS)

- Paths have no origin/prefix; replies are json_ietf with module-prefixed keys (parse with `ns_get`).
- One EVPN instance = four objects:
  - VLAN: `/network-instances/network-instance[name=default]/vlans/vlan[vlan-id=N]` → `{vlan-id, config:{vlan-id, name}}`
  - VLAN→VNI: `/interfaces/interface[name=Vxlan1]/arista-vxlan/vlan-to-vnis/vlan-to-vni[vlan=N]` → `{vlan, config:{vlan, vni}}` (`vni` must be inside `config`; a flat leaf is rejected)
  - BGP EVPN (`router bgp / vlan N`): `/arista/eos/evpn/evpn-instances/evpn-instance[name=N]` — key is the VLAN id as a string; `config:{route-distinguisher, redistribute}`, `route-target/config:{import[], export[]}` as `"65000:10"` (no `target:`), `vlans/vlan[vlan-id=N]`
  - Access trunk: `/interfaces/interface[name=EthernetX]/ethernet/switched-vlan/config/trunk-vlans` — empty list means all VLANs allowed, so it is left untouched
- Instances can be looked up by VLAN name or VLAN id. `evi`/`service_id` are ignored (VLAN id is reported as EVI). The access port must be a trunk switchport (routed uplinks are rejected).
- EOS reports `redistribute` as `LEARNED, ROUTER_MAC, HOST_ROUTE` even when only `LEARNED` is set.
- BGP (default VRF): `/network-instances/network-instance[name=default]/protocols/protocol[identifier=BGP][name=BGP]/bgp` (`global`, `neighbors/neighbor[neighbor-address=X]`, `peer-groups`).
  - `get_bgp_summary` / `get_bgp_neighbors` return a compact per-peer view: peer, peer-as, session state, and prefixes received/sent/installed for **active** AFI-SAFIs only, with names prefix-stripped (e.g. `L2VPN_EVPN`).
  - `get_bgp_neighbor` returns the raw OpenConfig tree. `get_bgp_config` reads `BGP_PATH` with `datatype="config"`.
  - EOS does not report `total-paths`/`total-prefixes` in global state, so the summary omits them. Neighbor `peer-as` comes from state, because it's often inherited from the peer group.

### gNMI path conventions (Junos)

- Port 32767, no TLS (`set system services extension-service request-response grpc clear-text port 32767`). Not 57400: it sits in the Linux ephemeral range (32768–60999), and Evolved's `trace-relay` can take it as a source port at boot, so nginx (the gNMI front end) fails to bind.
- **Get serves config only** (`type=CONFIG`, JSON_IETF or ASCII) and reads the `openconfig` origin by default, which is empty unless the node was configured through OpenConfig. Native config (`junos-conf-*` YANG) is under the **`juniper` origin**: `juniper:/configuration/protocols/bgp`, `juniper:/configuration/routing-options`. Do not use the `cli:` origin; it returns config text, not YANG.
- **State is Subscribe-only**: mode ONCE, PROTO encoding, `no_qos_marking=True` (otherwise "Qos not supported"). pygnmi's `subscribe2()` cannot decode `leaflist_val`, so `client.py` reads the raw protobuf stream and rebuilds a nested dict. An unknown key returns only `sync_response` → `None`.
- BGP state: `/network-instances/network-instance[name=DEFAULT]/protocols/protocol[identifier=BGP][name=DEFAULT]/bgp` (`global`, `neighbors/neighbor[neighbor-address=X]`). Both keys are `DEFAULT`, not `default`/`BGP`.
  - `get_bgp_summary` / `get_bgp_neighbors` produce the same compact view as EOS (via `utils/openconfig.py`). Junos does report `total-paths`/`total-prefixes`.
  - `get_bgp_neighbor` returns the OpenConfig state tree. `get_bgp_config` returns `{routing-options, protocols: {bgp}}` from native config.
- **Set** writes native config under the `juniper` origin with JSON_IETF values shaped like the Get reply (no module prefixes; empty leaves are `[null]`). A SetRequest is committed atomically: a rejected part applies nothing.
- EVPN instance = `juniper:/configuration/routing-instances/instance[name=X]` (`instance-type mac-vrf`, `service-type vlan-based`, `protocols/evpn {encapsulation vxlan, extended-vni-list}`, `vtep-source-interface lo0.0`, `route-distinguisher/rd-type`, `vrf-target/community`, `vlans/vlan[name=vlanN] {vlan-id, interface, vxlan {vni, ingress-node-replication}}`) + access unit `juniper:/configuration/interfaces/interface[name=et-0/0/2]/unit[name=N]` (`encapsulation vlan-bridge`, `vlan-id`).
  - Lookup by instance name, VLAN name or VLAN id; the VLAN id is reported as the EVI; `evi`/`service_id` are ignored.
  - Provision requires `interface_name` (`et-0/0/2` → unit = `vlan_id`, or `et-0/0/2.20`) and `vlan_id`. The parent port must already have `flexible-vlan-tagging` + `encapsulation flexible-ethernet-services` and is never modified. `export_rt` must equal `import_rt` (single `vrf-target community`). Delete removes the instance and its units.
  - State: one Subscribe on `/network-instances/network-instance[name=X]` (`state`, `vlan`, `jnx-evpn` VTEPs/peers/interfaces, `mac-table-info`, `inter-instance-policies`). Several subscriptions in one request are served sequentially (~2x slower), so subscribe to the common root. `.../evpn` is not a valid path.
  - The spine `ptx-gw` has no routing-instances (Get → NOT_FOUND → "No EVPN instances found").
- gRPC honours `https_proxy`; `.mcp.json` sets `grpc_proxy=""` so lab traffic bypasses the corporate proxy. Scripts run outside the MCP server need the same.

### Write tools and dry_run

Write tools accept a `dry_run: bool = False` parameter. When `True`, they return the formatted gNMI payload via `format_dry_run()` without calling `gnmi_set()`. Write tools that take structured inputs (e.g. `provision_evpn_instance`) validate parameters with a Pydantic model (e.g. `_VplsIntent` in `backend.py`) before any network call.

### Lab topology

The in-repo topology (`containerlab/nokia-evpn.clab.yml`) models a Nokia DC fabric: `clients → leaves (SR Linux) → spines (SR Linux) → DCGWs (SR OS)`. The topology name drives FQDN construction: `clab-{topo_name}-{node_name}`, or `{topo_name}-{node_name}` when the topology sets `prefix: __lab-name`.

`.mcp.json` currently points `NETMCP_CLAB_TOPOLOGY` at the multivendor lab (`/home/zaman/multivendor/multivendor.clab.yml`, lab `mv`). netmcp discovers `sros` (mv-sros), `srl` (mv-srl), `ceos` (mv-ceos, cEOS 4.34.2F, gNMI 6030, admin/admin) and the cJunos Evolved nodes `ptx` (mv-ptx, leaf) and `ptx-gw` (mv-ptx-gw, spine/RR), both junos, gNMI 32767, admin/admin@123 (set via `NETMCP_PTX_PASSWORD`/`NETMCP_PTX_GW_PASSWORD` in `.mcp.json`); other kinds are skipped. EVPN baseline: VLAN 10, VNI 1010, RT 65000:10 (`mac-vrf-10` on SR Linux; `EVPN-VXLAN10` mac-vrf on `ptx`, RD 10:3, access unit `et-0/0/2.10`). `ptx-gw` is the spine/RR and has no EVPN instance.

After changing backend code, reconnect the server (`/mcp` → netmcp → Reconnect) before testing through the MCP tools.

If a node was unreachable while the server was running (e.g. a cJunos VM still booting after a redeploy), MCP calls to it can keep failing after ~5 s with an empty error even once gNMI is back, while the same backend code run in a fresh process works. Reconnect the server to clear it. cJunos Evolved takes several minutes to boot; wait until its gNMI port answers before querying it.

### Inventory

Two discovery modes (in priority order):

1. **`netmcp.yml`** — Static YAML inventory listing nodes with their NOS type and connection details. Searched upward from cwd.
2. **Containerlab auto-discovery** — Scans `containerlab/*.clab.yml` upward from cwd. Override with `NETMCP_CLAB_TOPOLOGY` env var.

### Credentials

Per-node credential resolution order:
1. `NETMCP_{NODE_UPPER}_PASSWORD` (per-node env var; `-` in the node name becomes `_`, e.g. `NETMCP_PTX_GW_PASSWORD`)
2. `NETMCP_DEFAULT_PASSWORD` (global env var)
3. `SROS_PASSWORD` (legacy alias, SR OS only)
4. NOS-specific hardcoded default (e.g. `NokiaSros1!` for SR OS)

### Claude Code integration

`.mcp.json` registers the server automatically. To override credentials, add env vars to `.claude/settings.json` under `mcpServers.netmcp.env`.
