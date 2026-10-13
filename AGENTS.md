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

# Generate netmcp.yml from a running containerlab lab
uv run netmcp inventory --from-clab [LAB] -o netmcp.yml

# Docker: HTTP server on 127.0.0.1:8088/mcp, reads ./netmcp.yml and .env, joins the clab network
docker compose pull && docker compose up -d   # published image (ghcr.io/giancarlo3g/netmcp:latest)
docker compose up -d --build                  # build from the working tree instead
```

## Architecture

This is an MCP (Model Context Protocol) server that exposes network routers from multiple vendors to LLM agents via gNMI (primary), NETCONF (used for NX-OS) or an OpenAPI-modeled REST API (NVUE, used for NVIDIA Cumulus). The `netmcp` command is `src/netmcp/cli.py`: with no arguments it imports and runs `src/netmcp/server.py`, which instantiates a `FastMCP` object and registers all tools through a single unified dispatch path.

### Architecture rules (do not change)

This structure is fixed. Fit new work into it; do not restructure around it. `tests/unit/test_structure.py` enforces these rules. If it fails, fix the code, not the test.

1. **Tools live only in `dispatch.py`.** No `@mcp.tool`/`add_tool` anywhere else, no vendor-prefixed tools (`sros_*`, `eos_*`, …), no `register_vendor_tools`, no `contexts/` directories.
2. **One directory per NOS: `src/netmcp/nos/<nos>/`** with exactly:
   - `__init__.py`: only `NOS_TYPE = "<nos>"` and `BACKEND = <Class>()`; no functions.
   - `backend.py`: `<Class>(NotImplementedBackend)`. Public methods must be `NOSBackend` Protocol methods; everything else is a `_private` module-level helper.
   - `client.py`: transport only (gNMI/NETCONF get/set). No MCP imports, no formatting, no business logic.
   - `README.md`: optional.

   A placeholder NOS has only `__init__.py` (+ `README.md`) with `BACKEND = NotImplementedBackend("<nos>", ...)`. No other files or subdirectories.
3. **New capability = Protocol + dispatch.** Add the method to `NOSBackend` and `NotImplementedBackend` in `registry.py`, add one unified tool in `dispatch.py`, then implement it in the backends that support it. Vendor-specific arguments become optional unified parameters, as `interface_name`/`vlan_id` did for `provision_evpn_instance`. Then update the support table in `.claude/skills/netmcp-ops/SKILL.md`.
4. **New NOS = new directory + one `REGISTRY` entry** in `server.py` (plus its containerlab kind in `inventory.CLAB_KIND_TO_NOS`). Nothing else registers anything.
5. **Shared helpers go in `utils/`** (`formatters.py`, `yang.py`), not copied between NOS directories and not imported across them (e.g. `nos/eos` must not import from `nos/srl`).
6. **Every implemented backend gets `tests/unit/test_<nos>_backend.py`**, with `gnmi_get`/set mocked using reply shapes captured from a live node.
7. **Device access is schema-modeled APIs only, never the CLI.** Every read and write in `client.py` must target a published data model:
   - **Preferred:** YANG (OpenConfig or the vendor's native YANG) over gNMI, NETCONF or RESTCONF.
   - **Allowed only when the NOS has no usable YANG API for the feature:** a vendor REST API defined by a published OpenAPI 3 specification, e.g. NVIDIA Cumulus NVUE (`/nvue_v1/...`).

   Never use the CLI: no netmiko, paramiko/SSH screen-scraping, scrapli, or eAPI/NX-API CLI command execution, and no CLI-based encodings or wrappers, such as gNMI's `cli:` origin, `ASCII` encoding for CLI text, NETCONF RPCs that wrap CLI commands (`<command>`, `<cli>`), or REST endpoints that take CLI commands. If a feature has no schema-modeled path on a NOS, leave that method unimplemented (it falls back to `NotImplementedBackend`) rather than falling back to the CLI.
   - gNMI uses **pygnmi**, NETCONF uses **ncclient**, and OpenAPI REST uses **httpx**. The library stays inside that NOS's `client.py` or a shared transport in `utils/`. It must not leak into `backend.py`, `dispatch.py`, or `registry.py`.

### Data flow

1. **`inventory.py`** — `load_nodes()` searches upward from cwd for `netmcp.yml` (static inventory) or a `containerlab/*.clab.yml` topology file and returns `dict[str, NodeInfo]` mapping short node names (e.g. `"dcgw1"`) to `NodeInfo` objects containing the FQDN, NOS type, transport, and connection details. Importing the module has no side effects. `clab_inspect_to_yml()` builds a netmcp.yml from `containerlab inspect --all --format json` (used by `netmcp inventory --from-clab`).

2. **`server.py`** — Creates the `FastMCP("netmcp")` instance, loads `NODES = load_nodes()` at import, removes pygnmi's stdout log handler (it corrupts stdio mode), builds `REGISTRY` (a `dict[str, NOSBackend]` mapping `nos_type` to backend singletons), then calls `dispatch.register_unified_tools(mcp, NODES, REGISTRY)`. There is no separate vendor-tool registration step.

3. **`dispatch.py`** — Registers 29 unified tools (e.g. `get_interfaces`, `get_bgp_summary`, `provision_evpn_instance`). Each tool calls `_resolve(nodes, registry, node)` to get `(NodeInfo, NOSBackend)`, then delegates to the matching Protocol method. Unknown nodes and unregistered NOS types return `"Error: ..."` strings — never exceptions.

4. **NOS modules** (`nos/sros/`, `nos/srl/`, `nos/eos/`, `nos/junos/`, `nos/nxos/`, `nos/cumulus/`, `nos/iosxr/`) — Each exports `NOS_TYPE` and `BACKEND` (a singleton implementing `NOSBackend`). There are no vendor-specific tool registrations; all tools go through `dispatch.py`.

5. **`nos/sros/backend.py`** — `SROSBackend(NotImplementedBackend)` implements the `NOSBackend` Protocol for system, interfaces, BGP, and EVPN (read and write). Domains not yet implemented (IGP, MPLS/SR, VRF, Logging) fall through to `NotImplementedBackend` and return error strings.

6. **`nos/sros/client.py`** — Thin gNMI transport for SR OS. `gnmi_get(node: NodeInfo, path)` and `gnmi_set(node: NodeInfo, path, value, operation)` are the only public functions. A new `gNMIclient` is created per call because pygnmi consumes the client object on context exit.

7. **`nos/srl/`** — `SRLBackend` implements the 5 EVPN methods (MAC-VRF: bridged subinterface + `vxlan0.N` tunnel interface + `mac-vrf` network-instance) and the 4 BGP read methods, native SR Linux YANG. `client.py` exposes `gnmi_get(node, path, datatype="all")` and `gnmi_set(node, path, value, operation)`. Writes are three separate `gnmi_set` calls with a best-effort `_rollback()`. TLS with `skip_verify`. See "gNMI path conventions (SR Linux)".

8. **`nos/eos/`** — `EOSBackend` implements the 5 EVPN methods (VLAN-based EVPN) and the 4 BGP methods using OpenConfig + Arista experimental YANG, no CLI origin. `client.py` connects insecure (no TLS) and exposes `gnmi_get(node, path, datatype="all")` and `gnmi_set_batch(node, updates, deletes)`, which sends one atomic SetRequest, so provision/delete need no rollback. See "gNMI path conventions (EOS)".

9. **`nos/junos/`** — `JunOSBackend` implements the 4 BGP methods and the 5 EVPN methods (vlan-based EVPN-VXLAN `mac-vrf`), YANG only (no `cli:` origin). State comes from OpenConfig over Subscribe ONCE; config is read and written as native Junos YANG under the `juniper` origin. `client.py` exposes `gnmi_subscribe_once(node, path)` (nested dict rooted at `path`, or `None`), `gnmi_get_config(node, paths)` and `gnmi_set_batch(node, updates, deletes)` (one SetRequest = one commit, so no rollback). See "gNMI path conventions (Junos)".

10. **`nos/nxos/`** — `NXOSBackend` implements the 4 BGP read methods and the 5 EVPN methods (VLAN-based EVPN-VXLAN) over **NETCONF** (ncclient) with the native `Cisco-NX-OS-device` YANG model. `client.py` exposes `netconf_get(node, filter_xml)` and `netconf_get_config(node, filter_xml)` (subtree filters), both returning the `<data>` reply decoded to a nested dict (namespaces stripped, `*-list` elements always lists, leaves as strings) or `None`, and `netconf_edit_config(node, config_xml)` (one `<edit-config>` on running, merge, `rollback-on-error`, so no rollback helper). See "NETCONF path conventions (NX-OS)".

11. **`nos/cumulus/`** — `CumulusBackend` implements the 4 BGP read methods and the 5 EVPN methods (VLAN-based EVPN in bridge `br_default`) over the **NVUE REST API** (NVIDIA's OpenAPI 3 model; Cumulus has no usable YANG API: gNMI has no Get and its Subscribe sent no data). `client.py` exposes `nvue_get(node, path, rev="operational")` (GET, `rev` = `operational` | `applied`), returning parsed JSON, `None` on 404, and raising `RuntimeError` on auth or other HTTP errors, and `nvue_apply(node, tree)`, which applies one JSON tree as a single NVUE revision (so no rollback helper). `test_structure.py` allows HTTP write verbs only inside `nvue_apply`. See "NVUE REST conventions (Cumulus)".

12. **`utils/`** — `formatters.py` (`format_node_results`, `format_dry_run`), `yang.py` (`ns_get` — dict lookup that also matches module-prefixed json_ietf keys like `arista-exp-eos-vxlan:arista-vxlan`; `strip_prefix` for identityref values) and `openconfig.py` (`entries`, `leaf`, `peer_summary`, `active_afi_safis` — OpenConfig list/leaf walking and the compact BGP peer view shared by EOS and Junos). Use these rather than re-implementing reply parsing per backend.

13. **`registry.py`** — Defines the `NOSBackend` Protocol (the interface every NOS backend must satisfy) and `NotImplementedBackend` (the default for placeholder NOS directories).

### NodeInfo

`NodeInfo` is the single structure passed everywhere instead of a bare hostname string:

```python
@dataclass
class NodeInfo:
    name: str        # short name: "dcgw1"
    fqdn: str        # hostname: "clab-evpn-dcgw1"
    nos_type: str    # "sros" | "srl" | "eos" | "junos" | "iosxr" | "nxos" | "cumulus"
    transport: str   # "gnmi" | "netconf" | "rest"
    netconf_port: int  # default 830
    gnmi_port: int   # default 57400 (6030 for eos, 32767 for junos, via _NOS_GNMI_PORT_DEFAULTS)
    username: str    # default "admin" ("cumulus" for cumulus, via _NOS_USERNAME_DEFAULTS)
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
  - A node with no routing-instances (e.g. a spine/RR) returns NOT_FOUND on Get → "No EVPN instances found".
- gRPC honours `https_proxy`; the Docker image and the local stdio entry in `.mcp.json` set `grpc_proxy=""` so lab traffic bypasses the corporate proxy. Scripts run outside the MCP server need the same.

### NETCONF path conventions (NX-OS)

- NETCONF over SSH, port 830 (`feature netconf`), default login admin/admin, `device_params={"name": "nexus"}`. gNMI (50051) is not used: it is TLS-only and, with no certificate configured, NX-OS serves an auto-generated day-1 cert that expires after 24 h (handshake then fails with `certificate has expired`, even with skip_verify).
- Model: `Cisco-NX-OS-device`, namespace `http://cisco.com/ns/yang/cisco-nx-os-device`, root `<System>`. Reads use `<get>` (config + oper) or `<get-config source=running>` with subtree filters; writes use one `<edit-config>` on `running` (merge, `rollback-on-error`, deletes as inline `nc:operation="delete"`); never `<exec>`/`<command>` RPCs.
- BGP: `System/bgp-items/inst-items` (`asn`) → `dom-items/Dom-list[name=default]` (`rtrId`) → `peer-items/Peer-list[addr=X]` (`peerImp` = inherited template) → `ent-items/PeerEntry-list` (`operSt`, `operAsn`, `connEst`, `lastFlapTs`) → `af-items/PeerAfEntry-list[type=l2vpn-evpn]` (`tblSt`, `acceptedPaths`, `pfxSent`). Templates: `Dom-list/peercont-items/PeerCont-list[name=T]`.
  - A filter scoped to one `Dom-list` drops `inst-items` leaves, so it also selects `<asn/>`. There is an internal `Dom-list` named `egress-loadbalance-resolution-`; always filter on `name=default`.
  - `get_bgp_summary` / `get_bgp_neighbors` return the same compact view as the other NOSes: `operSt` uppercased, only AFIs with `tblSt up`, `l2vpn-evpn` → `L2VPN_EVPN`, `received` = `acceptedPaths`, `sent` = `pfxSent`. There is no per-peer installed count and no `total-paths`/`total-prefixes`.
  - `get_bgp_neighbor` returns the raw `Peer-list` entry; a filter for an unknown peer returns only the `Dom-list` key skeleton (→ not found). `get_bgp_config` returns `inst-items` from running config.
- EVPN instance = VLAN `bd-items/bd-items/BD-list[fabEncap=vlan-N]` (`name`, `accEncap=vxlan-VNI`) + NVE member `eps-items/epId-items/Ep-list[epId=1]/nws-items/vni-items/Nw-list[vni]` (`IngRepl-items/proto bgp`) + `evpn-items/bdevi-items/BDEvi-list[encap=vxlan-VNI]` (`rd`, `rttp-items/RttP-list[type]/ent-items/RttEntry-list[rtt]`) + access trunk `intf-items/phys-items/PhysIf-list[id=eth1/1]` (`mode`, `layer`, `trunkVlans` string like `10,20-30`).
  - RD/RT values are typed: `rd:as2-nn2:10:6`, `route-target:as2-nn2:65000:10` (`as2-nn4`/`as4-nn2`/`ipv4-nn2` by size; `rd:unknown:0:0` = auto); the backend shows and accepts `10:6`/`65000:10`.
  - Lookup by VLAN name or id; VLAN id is reported as the EVI; `evi`/`service_id` are ignored. Provision needs `interface_name` (a Layer2 trunk, `Ethernet1/1`) and `vlan_id` (2-3967), and an existing `nve1`. Delete leaves the default `1-4094` trunk list and single-VLAN lists alone.
  - State: `BD-list` `operSt`, `Ep-list` `operState`/`opervni-items/OperNw-list`, VTEPs in `peers-items/dy_peer-items/DyPeer-list` (all NVE peers), MACs in `mac-items/table-items/vlan-items/MacAddressEntry-list` (both keys required in a filter, so the whole table is read; remote MACs have `port` `Nve`).

### NVUE REST conventions (Cumulus)

- NVUE is the Cumulus Linux 5.x object model; the `nv` CLI and the REST API are two front ends to it. The REST API is defined by NVIDIA's OpenAPI 3 specification and serves the model tree as URLs: `nv show vrf default router bgp neighbor` ↔ `GET /nvue_v1/vrf/default/router/bgp/neighbor`.
- HTTPS on port 8765 (`NVUE_PORT` in `client.py`), self-signed cert (`verify=False`), HTTP basic auth, user `cumulus`. `rev=applied` is the running config, `rev=operational` the live state.
- `trust_env=False`: through the corporate proxy the request comes back empty. Test by hand with `curl -k --noproxy '*'`.
- Errors are decided by status code only: a wrong password returns HTTP 401 with an nginx HTML body; an unknown object returns HTTP 404 with an RFC 7807 JSON body (`/errors/ItemDoesNotExist`) → `None`.
- BGP: `router/bgp` (applied: `autonomous-system`, `router-id`), `vrf/default/router/bgp` (applied: neighbors, address families), `vrf/default/router/bgp/neighbor[/<ip>]` (operational).
  - The operational neighbor list is keyed by peer IP: `session-state` (`established`), `remote-as` (number), `uptime`, `last-reset-reason`, `message-stats`, and `address-family.<afi>.{rx-prefix,tx-prefix}` for the active families only. There is no installed-prefix count.
  - `get_bgp_summary` / `get_bgp_neighbors` return the same compact per-peer view as the other NOSes: `session-state` uppercased, address-family names mapped to OpenConfig spelling (`l2vpn-evpn` → `L2VPN_EVPN`), `rx-prefix`/`tx-prefix` → `received`/`sent` (no `installed`, like NX-OS). AS and router-id come from `router/bgp`; the VRF-level config shows `auto` for both.
  - `get_bgp_neighbor` returns the raw NVUE operational neighbor object; `get_bgp_config` returns `{router: {bgp}, vrf: {default: {router: {bgp}}}}` from `rev=applied`, with neighbor `password` hashes (`$nvsec$…`) replaced by `<redacted>`.
- gNMI (port 9339) is not used: Get is `Unimplemented` on 5.16 and Subscribe returned no updates.
- Debugging by hand: empty curl output means the request went through the proxy (add `--noproxy '*'`); HTTP 401 means a wrong password, not a missing API.
- EVPN instance = VLAN `bridge/domain/br_default/vlan/<N>` → `vni/<VNI>` + `evpn/vni/<VNI>` (`rd`, `route-target/{both|import|export}/<rt>`) + tagged port membership `interface/<swpN>/bridge/domain/br_default/vlan/<N>`.
  - Named `vlanN`; looked up by `vlanN`, VLAN id or VNI; the VLAN id is reported as the EVI; `service_name`/`service_id`/`evi` are ignored.
  - Provision needs `interface_name` (already a `br_default` member) and `vlan_id` (2-3724: NVUE reserves 3725-3999 and 4000-4064), and checks that the bridge is vlan-aware and that `nve vxlan` and `evpn` are enabled; it never creates them. RD may be `auto` (sent literally); RTs are explicit, `target:` is stripped, equal import/export → `both`.
  - Delete removes the VLAN, the EVPN VNI and the VLAN on every port, except a port where it's the only VLAN (an empty VLAN list means the port carries every bridge VLAN).
  - State: `evpn/vni/<VNI>` (operational: `local-vtep`, `mac-count`, `remote-vtep/<ip>/flood`; no up/down) and `evpn/vni/<VNI>/mac` (`type` local/remote, `remote-vtep`). `rev=applied` replies include defaults.
- Writes: `POST revision` → `{"<id>": {"state": "pending"}}` (ids are numbers on 5.16) → one `PATCH /nvue_v1/?rev=<id>` with the JSON tree (`null` unsets) → `PATCH revision/<id>` `{"state":"apply","auto-prompt":{"ays":"ays_yes"}}` → poll `GET revision/<id>`: `apply` → `readying` → `reloading` → `auto_saving` → `applied_and_saved` (about 9 s). Failures: a schema error is a 400 on the PATCH (`detail`); a semantic error ends `invalid` with `transition.issue.<n>.message` (e.g. `duplicate_vni`). `nvue_apply` deletes its revision on any failure (`DELETE revision/<id>` → 204).
- `system config auto-save` is enabled by default, so a successful apply is also saved to startup (`applied_and_saved`). netmcp never requests a save itself.
- Other users' pending revisions (e.g. one left by an interrupted `nv set`) don't block netmcp, which always works in its own new revision; leave them alone.
- Design and captured reply shapes: `docs/prd-cumulus-bgp.md`, `docs/prd-cumulus-evpn.md`. Both verified live on mv-nvidia (5.16.1) through the MCP tools on 2026-10-13: BGP reads; EVPN reads; provision and delete of VLAN 20 / VNI 1020 on swp1 (dry run, apply, verify). The pre-checks were also tested live and rejected an existing VLAN, an unknown port (`swp9`) and a routed uplink (`swp2`).

### Write tools and dry_run

Write tools accept a `dry_run: bool = False` parameter. When `True`, they return the formatted gNMI payload via `format_dry_run()` without calling `gnmi_set()` (NX-OS returns the `<edit-config>` XML instead, without calling `netconf_edit_config()`). Write tools that take structured inputs (e.g. `provision_evpn_instance`) validate parameters with a Pydantic model (e.g. `_VplsIntent` in `backend.py`) before any network call.

### Lab topology

netmcp is not tied to a particular lab. It works against whatever the inventory lists: any running containerlab lab, or any hand-written `netmcp.yml` (which can point at non-containerlab devices too). Nodes whose containerlab kind is not in `CLAB_KIND_TO_NOS` (linux clients, …) are skipped. Do not assume node names, lab names or service baselines; call `list_nodes` (or read the inventory) to find out what the current lab contains.

To point netmcp at a lab:

1. Generate the inventory on the lab host: `uv run netmcp inventory --from-clab [LAB] -o netmcp.yml` (`LAB` may be omitted when only one lab is running). The fqdn is the container name, so it works with any containerlab `prefix`. Regenerate (or delete) `netmcp.yml` whenever the lab changes; a stale one wins over everything else.
2. Set passwords for nodes that don't use their NOS default: `NETMCP_<NODE>_PASSWORD` or `NETMCP_DEFAULT_PASSWORD` in `.env` (Docker) or in the stdio entry's `env` (see Credentials).
3. Docker: set `CLAB_NETWORK` if the lab uses a management network other than containerlab's default `clab` (`mgmt.network` in the topology), then `docker compose up -d`.
4. Reconnect the MCP server (`/mcp` → netmcp → Reconnect).

Without a `netmcp.yml`, the local stdio setup can instead read a topology file directly (`NETMCP_CLAB_TOPOLOGY=/path/to/lab.clab.yml`, or a `containerlab/*.clab.yml` found upward from cwd). The FQDN is then derived from the topology: `clab-{topo_name}-{node_name}`, or `{topo_name}-{node_name}` when it sets `prefix: __lab-name`. The in-repo `containerlab/nokia-evpn.clab.yml` is a sample Nokia DC fabric (`clients → leaves (SR Linux) → spines (SR Linux) → DCGWs (SR OS)`).

#### Example: multivendor lab `mv`

The lab used during development is `~/multivendor/multivendor.clab.yml` (lab `mv`). These facts apply only when that lab is loaded. Nodes: `sros` (mv-sros), `srl` (mv-srl), `ceos` (mv-ceos, cEOS 4.34.2F, gNMI 6030, admin/admin) and the cJunos Evolved nodes `ptx` (mv-ptx, leaf) and `ptx-gw` (mv-ptx-gw, spine/RR), both junos, gNMI 32767, admin/admin@123 (`NETMCP_PTX_PASSWORD`/`NETMCP_PTX_GW_PASSWORD`), `nexus` (mv-nexus, Cisco N9Kv 10.6.3F, nxos, NETCONF 830, admin/admin; iBGP AS 65000 EVPN peers 192.1.2.1/192.1.2.2 via template `iBGP-evpn`; EVPN VLAN 10 `mac-vrf-10`, VNI 1010, RD 10:6, trunk `eth1/1`) and `nvidia` (mv-nvidia, Cumulus VX 5.16.1, cumulus, NVUE REST 8765, cumulus/Clab123! via `NETMCP_NVIDIA_PASSWORD`; iBGP AS 65000, router-id 192.1.1.7, L2VPN-EVPN peers 192.1.2.1/192.1.2.2 from `lo`; EVPN instance `vlan10`: VLAN 10 → VNI 1010 in bridge `br_default`, RD 10:7, RT 65000:10 `both`, port swp1, 6 remote VTEPs 192.1.1.1-192.1.1.6. `swp1` is the only `br_default` member; `swp2`/`swp3` are routed uplinks. VLAN 20 / VNI 1020 is free and was used as the throwaway test service (provisioned and deleted on 2026-10-13). The switch has an old pending NVUE revision `5`, not created by netmcp; leave it alone). Observed 2026-10-13: every leaf's EVPN session to `ptx-gw` (RR 192.1.2.2) is ESTABLISHED with 0 routes received and sent, so EVPN is reflected only by `sros` (192.1.2.1). That is a ptx-gw issue (likely hidden routes), not a netmcp bug. EVPN baseline: VLAN 10, VNI 1010, RT 65000:10 (`mac-vrf-10` on SR Linux; `EVPN-VXLAN10` mac-vrf on `ptx`, RD 10:3, access unit `et-0/0/2.10`). `ptx-gw` has no routing-instances, so "No EVPN instances found" is expected there.

After changing backend code, reload the server before testing through the MCP tools: with the HTTP setup, rebuild the container (`docker compose up -d --build`) and then reconnect (`/mcp` → netmcp → Reconnect); with the local stdio setup, reconnecting is enough.

If a node was unreachable while the server was running (e.g. a cJunos VM still booting after a redeploy), MCP calls to it can keep failing after ~5 s with an empty error even once gNMI is back, while the same backend code run in a fresh process works. Reconnect the server to clear it. cJunos Evolved takes several minutes to boot; wait until its gNMI port answers before querying it.

### Inventory

Two discovery modes (in priority order):

1. **`netmcp.yml`** — Static YAML inventory listing nodes with their NOS type and connection details. Searched upward from cwd. When found (and not empty) it wins over `NETMCP_CLAB_TOPOLOGY`.
2. **Containerlab auto-discovery** — Scans `containerlab/*.clab.yml` upward from cwd. Override with `NETMCP_CLAB_TOPOLOGY` env var (a missing path is a startup error, not a fallback).

`netmcp.yml` is gitignored; `netmcp.yml.example` is the template. Generate a real one with `netmcp inventory --from-clab`.

Log only to stderr: in stdio mode stdout is the MCP stream.

### Docker

- Two-stage `Dockerfile`: uv builds `/app/.venv` (`--no-install-project`, then the project `--no-editable`); the runtime is `python:3.11-slim-bookworm` with only the venv, running `netmcp` as a non-root user.
- Defaults: `MCP_HOST=0.0.0.0`, `MCP_PORT=8088` (HTTP at `/mcp`), `grpc_proxy=""`. `-e MCP_PORT=` switches to stdio (`docker run -i`).
- `compose.yaml` uses `image: ${NETMCP_IMAGE:-ghcr.io/giancarlo3g/netmcp:latest}` with `pull_policy: missing` plus `build: .`: `docker compose up` pulls the published image only if none is present locally (`docker compose pull` refreshes it), and `--build` builds the working tree under the same tag. A local build therefore shadows the published image until the next `docker compose pull`.
- `WORKDIR /inventory`: the inventory is mounted at `/inventory/netmcp.yml`, found by the normal upward search.
- The container must join the lab's Docker network (`clab`, or `CLAB_NETWORK` in compose) to resolve and reach node container names.
- `compose.yaml` publishes on `127.0.0.1` only (write tools, no auth) and reads passwords from an optional `.env`.
- The compose container is named `netmcp`; check it with `docker logs netmcp` (pygnmi messages go there) and over HTTP with the `mcp` client (`streamablehttp_client("http://127.0.0.1:8088/mcp")`, with `NO_PROXY=127.0.0.1` set so httpx skips the corporate proxy). After changing code, rebuild: `docker compose up -d --build`.
- `.github/workflows/docker.yml` runs the unit tests and pushes `ghcr.io/giancarlo3g/netmcp` (linux/amd64 + arm64) on `main` (`latest`, `main`) and `v*` tags (`X.Y.Z`, `X.Y`); pull requests only build.
- This host has stale `ghcr.io` credentials in `~/.docker/config.json`, so pulling the uv base image fails with `denied`. Build with an empty `DOCKER_CONFIG`, or run `docker logout ghcr.io`.
- Behind the corporate proxy, `uv sync` inside the build cannot reach PyPI and times out (`Failed to download …`). Pass the proxy as build args: `docker compose build --build-arg http_proxy=$http_proxy --build-arg https_proxy=$https_proxy --build-arg no_proxy=$no_proxy`, then `docker compose up -d`. Lab traffic still bypasses the proxy at run time (`grpc_proxy=""` for gNMI, `trust_env=False` for NVUE).

### Credentials

Per-node credential resolution order:
1. `NETMCP_{NODE_UPPER}_PASSWORD` (per-node env var; `-` in the node name becomes `_`, e.g. `NETMCP_PTX_GW_PASSWORD`)
2. `NETMCP_DEFAULT_PASSWORD` (global env var)
3. `SROS_PASSWORD` (legacy alias, SR OS only)
4. NOS-specific hardcoded default (e.g. `NokiaSros1!` for SR OS). Cumulus has none: set `NETMCP_<NODE>_PASSWORD` (in `mv`, `NETMCP_NVIDIA_PASSWORD=Clab123!`).

### Claude Code integration

`.mcp.json` registers the server as `netmcp` (approved in `.claude/settings.local.json` via `enabledMcpjsonServers`). It currently uses the **HTTP** setup: Claude Code connects to the compose container at `http://localhost:8088/mcp`.

```json
{
  "mcpServers": {
    "netmcp": { "type": "http", "url": "http://localhost:8088/mcp" }
  },
  "_disabled_local_stdio": {
    "netmcp": { "command": "uv", "args": ["run", "netmcp"], "env": { ... } }
  }
}
```

- **HTTP (current)**: start the server with `docker compose up -d --build`. The container reads `./netmcp.yml` (mounted at `/inventory/netmcp.yml`) and passwords from `.env`, and joins the lab's management network (`CLAB_NETWORK`, default `clab`). `NO_PROXY` must include `localhost` so Claude Code does not send the request through the corporate proxy.
- **Local stdio (disabled)**: JSON has no comments, so the stdio entry is kept under the top-level `_disabled_local_stdio` key, which Claude Code ignores. It runs `uv run netmcp` from the working tree with `NETMCP_CLAB_TOPOLOGY` (the lab's topology file), per-node passwords and `grpc_proxy=""` in its `env`. To switch to it, swap the two `netmcp` blocks: move the stdio entry under `mcpServers` and the HTTP entry under a disabled key (e.g. `_disabled_http`), then run `/mcp` → netmcp → Reconnect (or restart Claude Code). Keep the server name `netmcp` so the approval and `mcp__netmcp__*` permissions still apply. The container can keep running; stdio does not use port 8088.

Only keys under `mcpServers` are started. To override credentials for the stdio entry, add env vars to its `env` (or to `.claude/settings.json` under `mcpServers.netmcp.env`); for HTTP, put them in `.env` and recreate the container.
