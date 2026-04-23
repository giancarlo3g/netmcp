# netmcp — Product Requirements & Architecture Spec

**Version:** 0.1-draft | **Date:** 2026-04-23 | **Status:** For review

---

## Context

`router-mcp` is an MCP server that connects LLM agents to Nokia SR OS routers via gNMI. It works well for a single vendor, but real networks are heterogeneous. This spec defines how to evolve it into **netmcp**: a multi-vendor, open-source MCP server that supports any network router through a unified tool interface, with per-NOS directories for clean extensibility.

---

## 1. Project Overview

**netmcp** is an open-source MCP server that exposes network routers from multiple vendors to LLM agents via a unified tool catalog. Agents call `get_interfaces(node="spine1")` without knowing the vendor — the server dispatches to the correct NOS backend and returns clean JSON.

**Target users:**
- Network engineers who want AI agents to help operate, troubleshoot, and configure routers
- AI/LLM developers building automation workflows on top of MCP

**Non-goals (v1):**
- Production AAA/RBAC/audit logging
- GUI or REST API (MCP over stdio only)
- Scale beyond ~50 nodes
- Full intent-based provisioning engine

---

## 2. Architecture

### 2.1 Directory Structure

```
netmcp/
├── pyproject.toml              # name="netmcp", script="netmcp"
├── .mcp.json                   # server name: "netmcp"
├── netmcp.yml.example          # inventory file example
├── containerlab/               # kept from router-mcp
│   └── nokia-evpn.clab.yml
│
└── src/netmcp/
    ├── server.py               # FastMCP("netmcp") orchestrator
    ├── inventory.py            # NodeInfo, static YAML + clab discovery
    ├── dispatch.py             # registers unified tools (dispatcher)
    ├── registry.py             # NOSBackend Protocol + BackendRegistry
    │
    ├── nos/
    │   ├── sros/               # Nokia SR OS — fully implemented
    │   │   ├── __init__.py     # NOS_TYPE, BACKEND, register_vendor_tools()
    │   │   ├── client.py       # gnmi_get(node: NodeInfo, path) / gnmi_set(...)
    │   │   ├── backend.py      # SROSBackend implements NOSBackend Protocol
    │   │   ├── models.py       # Pydantic models (_VplsIntent, etc.)
    │   │   └── contexts/
    │   │       ├── system.py
    │   │       ├── interfaces.py
    │   │       ├── bgp.py
    │   │       └── evpn.py
    │   │
    │   ├── srl/                # Nokia SR Linux — fully implemented
    │   │   ├── __init__.py
    │   │   ├── client.py       # gNMI, same pygnmi pattern, different YANG paths
    │   │   ├── backend.py
    │   │   └── contexts/
    │   │       ├── system.py
    │   │       ├── interfaces.py
    │   │       ├── bgp.py
    │   │       └── evpn.py
    │   │
    │   ├── eos/                # Arista EOS — placeholder
    │   │   ├── __init__.py     # BACKEND = NotImplementedBackend("eos")
    │   │   └── README.md
    │   ├── junos/              # Juniper JunOS — placeholder
    │   │   ├── __init__.py
    │   │   └── README.md
    │   └── iosxr/              # Cisco IOS-XR — placeholder
    │       ├── __init__.py
    │       └── README.md
    │
    └── utils/
        └── formatters.py       # shared: to_json(), format_node_results(), format_dry_run()
```

### 2.2 NodeInfo Dataclass

Replaces the current `nodes: dict[str, str]` (name → fqdn) everywhere:

```python
# inventory.py
@dataclass
class NodeInfo:
    name: str           # "dcgw1"
    fqdn: str           # "clab-evpn-dcgw1"
    nos_type: str       # "sros" | "srl" | "eos" | "junos" | "iosxr"
    transport: str      # "gnmi" | "netconf"  (default per-NOS)
    gnmi_port: int      # default 57400
    netconf_port: int   # default 830
    username: str       # default "admin"
    tags: list[str]     # e.g. ["dcgw", "dc1"]
```

**Credential resolution** (replaces `config.get_password()`):
```python
def resolve_password(node: NodeInfo) -> str:
    # 1. NETMCP_{NODE_UPPER}_PASSWORD  (per-node)
    # 2. NETMCP_DEFAULT_PASSWORD       (global)
    # 3. SROS_PASSWORD                 (legacy alias for sros nodes)
    # 4. NOS-specific hardcoded default
```

### 2.3 Startup Flow

```
inventory.py  →  list[NodeInfo]         (static YAML or clab auto-discover)
registry.py   →  dict[nos_type → NOSBackend]
server.py:
  dispatch.register_unified_tools(mcp, nodes, registry)
  sros.register_vendor_tools(mcp, sros_nodes)
  srl.register_vendor_tools(mcp, srl_nodes)
```

Startup prints to stderr: nodes loaded, tool counts registered, warnings for unknown NOS types.

### 2.4 Unified Tool Dispatcher (`dispatch.py`)

Registers one tool per shared operation. Each tool:
1. Resolves `NodeInfo` by name
2. Looks up backend from registry
3. Calls `backend.<method>(node_info)`
4. Returns the string result

```python
@mcp.tool()
def get_interfaces(node: str) -> str:
    info = nodes.get(node)
    if not info:
        return f"Error: unknown node {node!r}. Available: {list(nodes)}"
    return registry[info.nos_type].get_interfaces(info)
```

### 2.5 NOSBackend Protocol (`registry.py`)

```python
class NOSBackend(Protocol):
    nos_type: str
    transport: str

    # system
    def get_system_info(self, node: NodeInfo) -> str: ...
    def get_system_alarms(self, node: NodeInfo) -> str: ...
    # interfaces
    def get_interfaces(self, node: NodeInfo) -> str: ...
    def get_interface_state(self, node: NodeInfo, interface_name: str) -> str: ...
    # bgp
    def get_bgp_summary(self, node: NodeInfo) -> str: ...
    def get_bgp_neighbors(self, node: NodeInfo) -> str: ...
    def get_bgp_neighbor(self, node: NodeInfo, peer_ip: str) -> str: ...
    def get_bgp_config(self, node: NodeInfo) -> str: ...
    # igp
    def get_isis_adjacencies(self, node: NodeInfo) -> str: ...
    def get_ospf_neighbors(self, node: NodeInfo) -> str: ...
    # mpls / sr
    def get_mpls_lsps(self, node: NodeInfo) -> str: ...
    def get_sr_sid_table(self, node: NodeInfo) -> str: ...
    # vrf / l3vpn
    def get_vrfs(self, node: NodeInfo) -> str: ...
    def get_vrf_routes(self, node: NodeInfo, vrf_name: str) -> str: ...
    # evpn (unified read)
    def get_evpn_instances(self, node: NodeInfo) -> str: ...
    # logging
    def get_log_events(self, node: NodeInfo, count: int, severity: str) -> str: ...
```

`NotImplementedBackend` base class returns `"Error: <method> is not implemented for NOS '<nos_type>'."` for every method. Placeholder NOS directories subclass it.

### 2.6 Transport

No shared transport base class. Each NOS `client.py` owns its transport:

- **sros, srl** → gNMI via `pygnmi`. New client per call (pygnmi consumes on context exit — keep current pattern).
- **eos** (future) → eAPI (HTTP/JSON)
- **junos, iosxr** (future) → NETCONF via `ncclient`

The public function signatures are the same concept (`get(node, path)`, `set(node, path, value, op)`), but are private to each NOS directory — no cross-NOS sharing of transport code.

---

## 3. Inventory & Discovery Model

### 3.1 Static YAML Inventory (`netmcp.yml`)

```yaml
inventory:
  nodes:
    - name: dcgw1
      fqdn: clab-evpn-dcgw1
      nos: sros
      transport: gnmi       # optional; per-NOS default if omitted
      gnmi_port: 57400      # optional
      tags: [dcgw, dc1]     # optional

    - name: spine1
      fqdn: clab-evpn-spine1
      nos: srl

    - name: pe1
      fqdn: pe1.prod.example.com
      nos: iosxr
      transport: netconf
```

Searched upward from cwd (same walk-upward strategy as current `config.py`).

### 3.2 Containerlab Auto-Discovery

Fallback when no `netmcp.yml` found. Scans `containerlab/*.clab.yml`. `NETMCP_CLAB_TOPOLOGY` env var overrides the file path.

```python
CLAB_KIND_TO_NOS = {
    "nokia_srsim":          "sros",
    "nokia_srlinux":        "srl",
    "arista_ceos":          "eos",
    "juniper_vjunosrouter": "junos",
    "cisco_xrd":            "iosxr",
}
```

Nodes with unmapped kinds (e.g. `linux` clients) are silently skipped.

### 3.3 Precedence

```
1. netmcp.yml (static)               ← wins entirely
2. NETMCP_CLAB_TOPOLOGY (env var)    ← explicit clab file
3. containerlab/*.clab.yml           ← auto-discovered
4. Error: no inventory found
```

No merging in v1. Static inventory wins entirely over clab.

---

## 4. Tool Catalog

**Naming:**
- Unified tools: `verb_noun` (no prefix) — e.g. `get_interfaces`, `get_bgp_summary`
- Vendor tools: `{nos}_{domain}_{action}` — e.g. `sros_evpn_provision_vpls`
- All tools return `str` (JSON on success, `"Error: ..."` on failure)
- Write tools: always accept `dry_run: bool = False` as last param

### System

| Tool | Type | NOS | Notes |
|------|------|-----|-------|
| `list_nodes` | unified R | all | name, fqdn, nos_type for all nodes |
| `get_system_info` | unified R | sros, srl | platform, SW version, uptime |
| `get_system_alarms` | unified R | sros, srl | active alarms/faults |

### Interfaces

| Tool | Type | NOS | Notes |
|------|------|-----|-------|
| `get_interfaces` | unified R | sros, srl | all logical interfaces |
| `get_interface_state` | unified R | sros, srl | oper/admin state, counters |
| `set_interface_description` | unified W | sros, srl | supports dry_run |
| `sros_get_ports` | vendor R | sros | physical port layer |

### BGP

| Tool | Type | NOS | Notes |
|------|------|-----|-------|
| `get_bgp_summary` | unified R | sros, srl | session/prefix counts |
| `get_bgp_neighbors` | unified R | sros, srl | all peers + state |
| `get_bgp_neighbor` | unified R | sros, srl | single peer, `peer_ip` param |
| `get_bgp_config` | unified R | sros, srl | running BGP config |

### EVPN

| Tool | Type | NOS | Notes |
|------|------|-----|-------|
| `get_evpn_instances` | unified R | sros, srl | normalized: `{name, vni, evi, oper_state}` |
| `sros_evpn_list_services` | vendor R | sros | VPLS service list |
| `sros_evpn_get_service` | vendor R | sros | single VPLS config |
| `sros_evpn_get_service_state` | vendor R | sros | single VPLS oper state |
| `sros_evpn_provision_vpls` | vendor W | sros | full VPLS + VXLAN + BGP-EVPN |
| `sros_evpn_delete_vpls` | vendor W | sros | |
| `srl_evpn_list_mac_vrfs` | vendor R | srl | mac-vrf network instances |
| `srl_evpn_get_mac_vrf` | vendor R | srl | single mac-vrf config+state |
| `srl_evpn_provision_mac_vrf` | vendor W | srl | mac-vrf + VXLAN + BGP-EVPN |
| `srl_evpn_delete_mac_vrf` | vendor W | srl | |

### IGP (ISIS / OSPF)

| Tool | Type | NOS | Notes |
|------|------|-----|-------|
| `get_isis_adjacencies` | unified R | sros, srl | all IS-IS adjacencies + state |
| `get_isis_database` | unified R | sros | LSDB summary |
| `get_isis_config` | unified R | sros | IS-IS instance config |
| `get_ospf_neighbors` | unified R | sros | OSPF neighbor states |
| `get_ospf_config` | unified R | sros | OSPF instance config |

### MPLS / Segment Routing

| Tool | Type | NOS | Notes |
|------|------|-----|-------|
| `get_mpls_lsps` | unified R | sros | active MPLS LSPs |
| `get_sr_sid_table` | unified R | sros, srl | SID binding table |
| `get_sr_config` | unified R | sros, srl | SR config |

### VRF / L3VPN

| Tool | Type | NOS | Notes |
|------|------|-----|-------|
| `get_vrfs` | unified R | sros, srl | all VRFs / network-instances |
| `get_vrf_routes` | unified R | sros, srl | route table for a VRF |
| `get_vrf_interfaces` | unified R | sros | interfaces in a VRF |
| `sros_vprn_provision` | vendor W | sros | provision VPRN service |
| `sros_vprn_delete` | vendor W | sros | |

### Logging / Events

| Tool | Type | NOS | Notes |
|------|------|-----|-------|
| `get_log_events` | unified R | sros | `count=50`, `severity=""` params |
| `get_log_config` | unified R | sros, srl | logging destinations config |

---

## 5. NOS Backend Contract

### What `nos/{name}/__init__.py` must expose

```python
NOS_TYPE: str = "sros"           # matches inventory nos: field
BACKEND: NOSBackend              # singleton instance

def register_vendor_tools(mcp: FastMCP, nodes: dict[str, NodeInfo]) -> None:
    from netmcp.nos.sros.contexts import system, interfaces, bgp, evpn
    system.register(mcp, nodes)
    interfaces.register(mcp, nodes)
    bgp.register(mcp, nodes)
    evpn.register(mcp, nodes)
```

Context modules keep the existing `register(mcp, nodes)` pattern. The only change: `nodes.get(node)` returns `NodeInfo` instead of a bare `fqdn` string.

### Placeholder NOS

```python
# nos/eos/__init__.py
from netmcp.registry import NotImplementedBackend
NOS_TYPE = "eos"
BACKEND = NotImplementedBackend("eos")
def register_vendor_tools(mcp, nodes): pass
```

Returns clean error strings; never crashes the server.

### Backend Registry (`registry.py`)

Explicit import list (not dynamic discovery — intentional contributor checkpoint):

```python
from netmcp.nos import sros, srl, eos, junos, iosxr

REGISTRY: dict[str, NOSBackend] = {
    "sros": sros.BACKEND,
    "srl":  srl.BACKEND,
    "eos":  eos.BACKEND,
    "junos": junos.BACKEND,
    "iosxr": iosxr.BACKEND,
}
```

---

## 6. Contributing: Adding a New NOS

Steps to add a new NOS backend (also goes in `CONTRIBUTING.md` and each placeholder `README.md`):

1. Choose an identifier (e.g. `nxos`)
2. Create `src/netmcp/nos/nxos/` with: `__init__.py`, `client.py`, `backend.py`, `models.py`, `contexts/`
3. Implement `client.py`: `get(node: NodeInfo, path) -> dict | None` and `set(...)`. Follow `nos/sros/client.py` for gNMI; use `ncclient` for NETCONF.
4. Implement `backend.py`: class inheriting from `NotImplementedBackend`, overriding supported methods
5. Implement context modules following `nos/sros/contexts/` as templates; name tools `nxos_*`
6. Wire up `__init__.py` with `NOS_TYPE`, `BACKEND`, `register_vendor_tools()`
7. Add to `REGISTRY` in `registry.py` and call `register_vendor_tools` in `server.py`
8. Add containerlab kind mapping in `inventory.py` (if applicable)
9. Add example node to `netmcp.yml.example`

---

## 7. Migration Plan

### Phase 1 — Rename & restructure (no behavioral change)

- `pyproject.toml`: `name = "netmcp"`, script `netmcp = "netmcp.server:run"`
- `src/router_mcp/` → `src/netmcp/`
- `client.py` → `nos/sros/client.py`; change signature `gnmi_get(hostname, path)` → `gnmi_get(node: NodeInfo, path)`
- `contexts/*.py` → `nos/sros/contexts/*.py`; update imports
- `config.py` → `inventory.py`; introduce `NodeInfo`, static YAML loader, `resolve_password()`
- Create `nos/sros/__init__.py`, `nos/sros/backend.py`
- Add placeholder `nos/{eos,junos,iosxr}/__init__.py`
- Update `.mcp.json`: `"sros-mcp"` → `"netmcp"`
- Legacy `SROS_PASSWORD` env var aliased in `resolve_password()` for backward compat

### Phase 2 — SR Linux implementation

- `nos/srl/client.py`: gNMI with SRL YANG paths (`srl_nokia-*` namespaces)
- `nos/srl/contexts/`: system, interfaces, bgp, evpn
- `nos/srl/backend.py`
- Update `CLAB_KIND_TO_NOS`: `nokia_srlinux → srl`
- Register SRL in `registry.py` and `server.py`
- Existing `nokia-evpn.clab.yml` spine/leaf nodes now managed

### Phase 3 — Unified dispatch

- Implement `dispatch.py` with all unified tools
- Verify `get_interfaces("spine1")` → SRL, `get_interfaces("dcgw1")` → SROS

### Phase 4 — New domains

- Implement IGP, MPLS/SR, VRF, Logging for SR OS
- Implement same domains for SR Linux where gNMI paths exist

### Breaking change

`.mcp.json` server name changes from `"sros-mcp"` to `"netmcp"`. Existing Claude Code users must update `.claude/settings.json`. Documented in `MIGRATION.md`.

---

## 8. Verification

### Unit tests (no lab)

- `test_inventory.py`: YAML loading, clab auto-discovery, credential resolution, unknown NOS warnings
- `test_dispatch.py`: mock backend, assert correct method dispatch by nos_type; assert unknown node → error string
- `test_backend_not_implemented.py`: all Protocol methods return error strings
- `test_models.py`: `_VplsIntent` Pydantic bounds and missing-field errors

### End-to-end (containerlab lab)

```bash
cd containerlab && sudo containerlab deploy -t nokia-evpn.clab.yml
uv run netmcp
# Then via Claude Code or mcp-client:
list_nodes                                          # dcgw1/dcgw2 (sros), spines/leaves (srl)
get_system_info(node="dcgw1")                       # SR OS version JSON
get_interfaces(node="dcgw1")                        # interfaces JSON
get_bgp_summary(node="dcgw1")                       # BGP stats
sros_evpn_provision_vpls(node="dcgw1", ..., dry_run=True)   # dry run output
sros_evpn_provision_vpls(node="dcgw1", ..., dry_run=False)  # live provision
sros_evpn_get_service(node="dcgw1", service_name="2")       # confirm exists
sros_evpn_delete_vpls(node="dcgw1", service_name="2")       # delete
get_system_info(node="spine1")                      # SR Linux version JSON (Phase 2+)
```

### CI (no lab)

```yaml
- run: uv run pytest tests/unit/ -v
- run: NETMCP_NO_INVENTORY=1 uv run netmcp --check   # startup validation, exit 0
```

---

## Open Questions

| # | Question | Recommendation |
|---|----------|----------------|
| A | Unimplemented methods: raise or return error string? | Always return string — never raise at tool boundary |
| B | `list_nodes` tag filtering? | Add `tags` to schema now; `tag=` filter param in v1.1 |
| C | Clab topology file selection | Scan `containerlab/*.clab.yml`; `NETMCP_CLAB_TOPOLOGY` env override |
| D | Merge static + clab inventory? | No merge in v1; document as v2 gap |
| E | `get_log_events` severity filter? | Yes — `severity: str = ""` optional param |
| F | Plugin-style NOS discovery via entry points? | No in v1; document as v2 |
| G | `--check` probe gNMI reachability? | Yes, as separate `--probe` flag |
| H | SRL EVPN write tools in v1? | If scope is tight, SRL EVPN can be read-only in initial cut |
| I | `nodes` dict type change from `str` to `NodeInfo` | Accepted — one-line change per lookup in all context modules |

---

## Critical Files

| File (current path) | Destination | Change |
|---|---|---|
| `src/router_mcp/server.py` | `src/netmcp/server.py` | Full rewrite |
| `src/router_mcp/config.py` | `src/netmcp/inventory.py` | Full rewrite — NodeInfo, YAML loader |
| `src/router_mcp/client.py` | `src/netmcp/nos/sros/client.py` | Signature: `gnmi_get(node: NodeInfo, path)` |
| `src/router_mcp/contexts/evpn.py` | `src/netmcp/nos/sros/contexts/evpn.py` | Import path update only |
| `src/router_mcp/contexts/system.py` | `src/netmcp/nos/sros/contexts/system.py` | Import path update only |
| `src/router_mcp/contexts/interfaces.py` | `src/netmcp/nos/sros/contexts/interfaces.py` | Import path update only |
| `src/router_mcp/contexts/bgp.py` | `src/netmcp/nos/sros/contexts/bgp.py` | Import path update only |
| `src/router_mcp/utils/formatters.py` | `src/netmcp/utils/formatters.py` | No content change |
| `pyproject.toml` | (in place) | Rename, update entry point |
| `.mcp.json` | (in place) | `"sros-mcp"` → `"netmcp"` |
| *(new)* | `src/netmcp/registry.py` | NOSBackend Protocol, NotImplementedBackend, REGISTRY |
| *(new)* | `src/netmcp/dispatch.py` | All unified tool registrations |
| *(new)* | `src/netmcp/nos/sros/backend.py` | SROSBackend class |
| *(new)* | `src/netmcp/nos/srl/` | Full SR Linux implementation |
| *(new)* | `src/netmcp/nos/{eos,junos,iosxr}/__init__.py` | Placeholder stubs |
