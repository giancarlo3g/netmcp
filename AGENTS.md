# CLAUDE.md

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

### Data flow

1. **`inventory.py`** — At import time, searches upward from cwd for `netmcp.yml` (static inventory) or a `containerlab/*.clab.yml` topology file. Builds `NODES: dict[str, NodeInfo]` mapping short node names (e.g. `"dcgw1"`) to `NodeInfo` objects containing the FQDN, NOS type, transport, and connection details.

2. **`server.py`** — Creates the `FastMCP("netmcp")` instance, builds `REGISTRY` (a `dict[str, NOSBackend]` mapping `nos_type` to backend singletons), then calls `dispatch.register_unified_tools(mcp, NODES, REGISTRY)`. There is no separate vendor-tool registration step.

3. **`dispatch.py`** — Registers 29 unified tools (e.g. `get_interfaces`, `get_bgp_summary`, `provision_evpn_instance`). Each tool calls `_resolve(nodes, registry, node)` to get `(NodeInfo, NOSBackend)`, then delegates to the matching Protocol method. Unknown nodes and unregistered NOS types return `"Error: ..."` strings — never exceptions.

4. **NOS modules** (`nos/sros/`, `nos/srl/`, `nos/eos/`, `nos/junos/`, `nos/iosxr/`) — Each exports `NOS_TYPE` and `BACKEND` (a singleton implementing `NOSBackend`). There are no vendor-specific tool registrations; all tools go through `dispatch.py`.

5. **`nos/sros/backend.py`** — `SROSBackend(NotImplementedBackend)` implements the `NOSBackend` Protocol for system, interfaces, BGP, and EVPN (read and write). Domains not yet implemented (IGP, MPLS/SR, VRF, Logging) fall through to `NotImplementedBackend` and return error strings.

6. **`nos/sros/client.py`** — Thin gNMI transport for SR OS. `gnmi_get(node: NodeInfo, path)` and `gnmi_set(node: NodeInfo, path, value, operation)` are the only public functions. A new `gNMIclient` is created per call because pygnmi consumes the client object on context exit.

7. **`nos/srl/`** — `SRLBackend` implements only the 5 EVPN methods (MAC-VRF: bridged subinterface + `vxlan0.N` tunnel interface + `mac-vrf` network-instance). Writes are three separate `gnmi_set` calls with a best-effort `_rollback()`. TLS with `skip_verify`.

8. **`nos/eos/`** — `EOSBackend` implements the 5 EVPN methods (VLAN-based EVPN) and the 4 BGP methods using OpenConfig + Arista experimental YANG, no CLI origin. `client.py` connects insecure (no TLS) and exposes `gnmi_get(node, path, datatype="all")` and `gnmi_set_batch(node, updates, deletes)`, which sends one atomic SetRequest, so provision/delete need no rollback. See "gNMI path conventions (EOS)".

9. **`utils/`** — `formatters.py` (`format_node_results`, `format_dry_run`) and `yang.py` (`ns_get` — dict lookup that also matches module-prefixed json_ietf keys like `arista-exp-eos-vxlan:arista-vxlan`; `strip_prefix` for identityref values). Use these rather than re-implementing reply parsing per backend.

10. **`registry.py`** — Defines the `NOSBackend` Protocol (the interface every NOS backend must satisfy) and `NotImplementedBackend` (the default for placeholder NOS directories).

### NodeInfo

`NodeInfo` is the single structure passed everywhere instead of a bare hostname string:

```python
@dataclass
class NodeInfo:
    name: str        # short name: "dcgw1"
    fqdn: str        # hostname: "clab-evpn-dcgw1"
    nos_type: str    # "sros" | "srl" | "eos" | "junos" | "iosxr"
    transport: str   # "gnmi" | "netconf"
    gnmi_port: int   # default 57400 (6030 for eos, via _NOS_GNMI_PORT_DEFAULTS)
    username: str    # default "admin"
    tags: list[str]
```

### gNMI path conventions (SR OS)

- Config paths: `nokia-conf:configure/...`
- State paths: `nokia-state:state/...`
- List keys use unquoted values: `router[router-name=Base]`, `service/vpls[service-name=1]`

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

### Write tools and dry_run

Write tools accept a `dry_run: bool = False` parameter. When `True`, they return the formatted gNMI payload via `format_dry_run()` without calling `gnmi_set()`. Write tools that take structured inputs (e.g. `provision_evpn_instance`) validate parameters with a Pydantic model (e.g. `_VplsIntent` in `backend.py`) before any network call.

### Lab topology

The in-repo topology (`containerlab/nokia-evpn.clab.yml`) models a Nokia DC fabric: `clients → leaves (SR Linux) → spines (SR Linux) → DCGWs (SR OS)`. The topology name drives FQDN construction: `clab-{topo_name}-{node_name}`, or `{topo_name}-{node_name}` when the topology sets `prefix: __lab-name`.

`.mcp.json` currently points `NETMCP_CLAB_TOPOLOGY` at the multivendor lab (`/home/zaman/multivendor/multivendor.clab.yml`, lab `mv`). netmcp discovers `sros` (mv-sros), `srl` (mv-srl) and `ceos` (mv-ceos, cEOS 4.34.2F, gNMI 6030, admin/admin); other kinds are skipped. EVPN baseline: VLAN 10 `mac-vrf-10`, VNI 1010, RT 65000:10.

After changing backend code, reconnect the server (`/mcp` → netmcp → Reconnect) before testing through the MCP tools.

### Inventory

Two discovery modes (in priority order):

1. **`netmcp.yml`** — Static YAML inventory listing nodes with their NOS type and connection details. Searched upward from cwd.
2. **Containerlab auto-discovery** — Scans `containerlab/*.clab.yml` upward from cwd. Override with `NETMCP_CLAB_TOPOLOGY` env var.

### Credentials

Per-node credential resolution order:
1. `NETMCP_{NODE_UPPER}_PASSWORD` (per-node env var)
2. `NETMCP_DEFAULT_PASSWORD` (global env var)
3. `SROS_PASSWORD` (legacy alias, SR OS only)
4. NOS-specific hardcoded default (e.g. `NokiaSros1!` for SR OS)

### Claude Code integration

`.mcp.json` registers the server automatically. To override credentials, add env vars to `.claude/settings.json` under `mcpServers.netmcp.env`.
