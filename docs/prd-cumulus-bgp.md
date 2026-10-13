# netmcp: NVIDIA Cumulus Linux BGP backend (PRD)

**Version:** 0.4 | **Date:** 2026-10-13 | **Status:** Implemented and verified live (P0–P3)

**Changes from 0.3:**
- Phase 0 is complete. §3 and §5 now use the captured NVUE replies.
- `get_bgp_config` redacts peer password hashes.

**Changes from 0.2:**
- Rule 7 is reworded from "YANG only" to "schema-modeled APIs only".
- NVUE REST, which is modeled by NVIDIA's published OpenAPI 3 specification, now qualifies under the rule itself instead of needing a Cumulus-specific exception (§2.3, §2.4).

**Changes from 0.1:**
- All Cumulus reads, state and config, go through NVUE REST.
- gNMI is dropped (§2.2), along with the shared Subscribe refactor (`utils/gnmi_stream.py`).

---

## Context

netmcp ships BGP read tools for `sros`, `srl`, `eos`, `junos` and `nxos`. NVIDIA Cumulus Linux is the next NOS in the development lab: `mv-nvidia` runs Cumulus VX 5.16.1 (containerlab kind `nvidia_cumulusvx`) as an EVPN leaf, with iBGP AS 65000 L2VPN-EVPN sessions from `lo` 192.1.1.7 to the route reflectors 192.1.2.1 and 192.1.2.2. netmcp doesn't see that node today, because `nvidia_cumulusvx` isn't in `CLAB_KIND_TO_NOS`, so `netmcp inventory --from-clab` skips it.

This PRD adds a `cumulus` NOS that implements the four existing BGP read methods. The work fits the fixed architecture in `AGENTS.md`: no new tools, no Protocol change, and one new NOS directory plus one `REGISTRY` entry.

---

## 1. Goals and non-goals

**Goals**

| # | Goal |
|---|------|
| G1 | `get_bgp_summary`, `get_bgp_neighbors`, `get_bgp_neighbor` and `get_bgp_config` work on a Cumulus node. |
| G2 | Summary and neighbors return the same compact per-peer view as EOS, Junos and NX-OS, so an agent can compare peers across vendors without vendor logic. |
| G3 | `netmcp inventory --from-clab` discovers Cumulus nodes with correct transport and username defaults. |
| G4 | Unit tests use reply shapes captured from a live Cumulus 5.16 node (rule 6). |

**Non-goals (this PRD)**

- EVPN, interfaces, system, IGP, VRF and logging. Each is a follow-up PRD that reuses the same client.
- **Any write.** The NVUE client exposes `GET` only. Writes would need NVUE's revision/apply workflow and a separate decision.
- gNMI (see §2.2), Cumulus 4.x (NCLU), and VRFs other than `default`.

---

## 2. Device API and rule 7

### 2.1 What Cumulus 5.16 offers

| API | Modeled by | Operations | Use here |
|-----|-----------|------------|----------|
| NVUE REST API (`/nvue_v1/...`, port 8765, HTTPS self-signed, basic auth) | NVIDIA's NVUE **OpenAPI 3** specification (JSON Schema); no YANG | Config (`rev=applied`/`startup`) and operational state (`rev=operational`), read and write | **All four BGP methods, GET only** |
| gNMI (port 9339) | OpenConfig YANG | Capabilities and Subscribe only; **Get is `Unimplemented`** | Not used (§2.2) |
| NETCONF / RESTCONF | Not available | - | - |
| CLI (`nv` / `vtysh` over SSH) | Not modeled | - | **Never** (rule 7) |

### 2.2 Why not gNMI (Phase 0 result)

Tested on `mv-nvidia` on 2026-10-13:

- Capabilities works: gNMI 0.10.0, encodings JSON/JSON_IETF/PROTO, models including `openconfig-network-instance` 4.4.1 and `openconfig-interfaces` 3.7.1.
- `Get` returns `rpc error: code = Unimplemented desc = method Get not implemented`.
- Subscribe is accepted, but **no updates arrived**, even for `/interfaces/interface[name=swp2]/state/oper-status`.
- Even if Subscribe worked, BGP config has no gNMI path, every call would wait for a sample interval, and the documented BGP leaf set is small.

NVUE REST returned both config and operational BGP data on the first working request. One transport is simpler than two.

### 2.3 What NVUE REST is

NVUE (NVIDIA User Experience) is the object model of Cumulus Linux 5.x, held by the `nvued` daemon. The `nv` CLI and the REST API are two front ends to that model. The REST API exposes the model tree directly as URLs; for example, `nv show vrf default router bgp neighbor` corresponds to `GET /nvue_v1/vrf/default/router/bgp/neighbor`. Reads take a `rev` parameter: `applied` (running config), `startup` (saved config) or `operational` (live state).

**NVUE REST is based on NVIDIA's OpenAPI 3 specification.** The data is described by JSON Schema, which defines:
- every object path;
- property types, enums and value ranges;
- which data is config and which is operational state.

Replies are typed JSON objects defined by that schema, not CLI text. As a data model, it's comparable to a vendor-native YANG model such as `Cisco-NX-OS-device` or Junos `junos-conf-*`. What it lacks is the YANG format and the protocols that carry YANG (gNMI, NETCONF, RESTCONF).

It is the Cumulus counterpart of Cisco's NX-API REST (the DME object model), **not** of NX-API CLI, which sends CLI commands over HTTP.

### 2.4 Decision: reword rule 7 to "schema-modeled APIs"

The current rule 7 says "YANG-modeled APIs only". Its purpose is to keep netmcp off the CLI (screen-scraping, CLI-wrapping RPCs, `cli:` origins) and on structured, model-defined data. NVUE REST meets that purpose. It fails the rule only because its model is written in OpenAPI rather than YANG.

**Decision:** reword rule 7 in `AGENTS.md`:

> 7. **Device access is schema-modeled APIs only, never the CLI.** Every read and write in `client.py` must target a published data model:
>    - **Preferred:** YANG (OpenConfig or the vendor's native YANG) over gNMI, NETCONF or RESTCONF.
>    - **Allowed only when the NOS has no usable YANG API for the feature:** a vendor REST API defined by a published OpenAPI 3 specification. Example: NVIDIA Cumulus NVUE (`/nvue_v1/...`).
>
>    Never use the CLI:
>    - no netmiko, paramiko/SSH screen-scraping, scrapli, or eAPI/NX-API CLI command execution;
>    - no CLI-based encodings or wrappers, such as gNMI's `cli:` origin, `ASCII` encoding for CLI text, NETCONF `<command>`/`<cli>` RPCs, or REST endpoints that take CLI commands.
>
>    If a feature has no schema-modeled path on a NOS, leave that method unimplemented rather than falling back to the CLI.

The library sub-bullet adds `httpx` for OpenAPI REST, under the same containment rule: the library stays in that NOS's `client.py` or a shared transport in `utils/`.

Under this wording NVUE is a regular, rule-compliant transport, so Cumulus needs no exception. YANG stays preferred: if Cumulus gains a working gNMI Get or NETCONF with BGP coverage, the backend should move to it.

### 2.5 Scope of the Cumulus client

- **GET only.** `nos/cumulus/client.py` exposes one function, `nvue_get`. It has no `PATCH`/`PUT`/`POST`/`DELETE` and no revision handling. Writes are out of scope for this PRD (§1), not prohibited by the rule.
- **Never the CLI.** The client calls only `/nvue_v1/...` resource paths. It never runs `nv` commands, SSH or `vtysh`.
- **Contained.** `httpx` (already installed through `mcp`; it gets listed explicitly in `pyproject.toml`) is imported only in `nos/*/client.py` or `utils/`.
- **Enforced.** `test_structure.py` gets two new checks:
  - `httpx` is imported only from a NOS `client.py` or `utils/`.
  - The Cumulus client calls no HTTP method other than `get`. This check is relaxed when a later PRD adds writes.

---

## 3. Phase 0: captures

The user runs these. Every command uses `--noproxy '*'` (see §3.2).

### 3.1 Endpoints

| # | Endpoint (`https://mv-nvidia:8765/nvue_v1/…`) | Used by | Status |
|---|-----------------------------------------------|---------|--------|
| N1 | `router/bgp?rev=applied` | `get_bgp_config`, summary AS / router-id | ✅ captured (§3.3) |
| N2 | `vrf/default/router/bgp/neighbor?rev=operational` | `get_bgp_summary`, `get_bgp_neighbors` | ✅ captured |
| N3 | `vrf/default/router/bgp/neighbor/192.1.2.1?rev=operational` | `get_bgp_neighbor` | ✅ captured |
| N4 | `vrf/default/router/bgp/neighbor/10.0.0.99?rev=operational` | `get_bgp_neighbor` (unknown peer) | ✅ HTTP 404, RFC 7807 JSON body |
| N5 | `vrf/default/router/bgp?rev=applied` | `get_bgp_config` (neighbors, AFIs) | ✅ captured |
| N6 | `vrf/default/router/bgp?rev=operational` | summary totals | Not run. N2 has no totals, so the summary omits them. |
| N7 | `vrf/default/router/bgp/neighbor/192.1.2.1/address-family?rev=operational` | per-AFI prefix counts | Not needed: N2 includes them |

```bash
NV() { curl -sk --noproxy '*' -u "cumulus:${NETMCP_NVIDIA_PASSWORD}" \
         -w '\nHTTP %{http_code}\n' "https://mv-nvidia:8765/nvue_v1/$1"; }
NV 'vrf/default/router/bgp/neighbor?rev=operational'
NV 'vrf/default/router/bgp/neighbor/192.1.2.1?rev=operational'
NV 'vrf/default/router/bgp/neighbor/10.0.0.99?rev=operational'
NV 'vrf/default/router/bgp?rev=applied'
```

### 3.2 Findings (2026-10-13)

| Topic | Result |
|-------|--------|
| Reachability | NVUE answers on the management address (`mv-nvidia:8765`) with no device change. |
| Proxy | Through the corporate proxy (curl honouring `https_proxy`), the request returns **nothing at all**. The client must ignore proxy env vars (`httpx` `trust_env=False`). |
| Auth failure | A wrong password returns **HTTP 401 with an nginx HTML body**, not JSON. The client decides by status code and never parses error bodies. |
| Unknown object | **HTTP 404** with an RFC 7807 JSON body (`"type": "/errors/ItemDoesNotExist"`). The client returns `None`. |
| Neighbor state (N2) | Keyed by peer IP. The state field is `session-state` (`established`), not `state`. `remote-as` is a number. Also `uptime` (591683000 in the lab), `last-reset-reason`, `last-reset-time` and `message-stats`. |
| Address families (N2) | Only active families are listed (`l2vpn-evpn` in the lab), with `rx-prefix` / `tx-prefix`. There is **no installed-prefix count**. |
| Neighbor detail (N3) | Full session object: capabilities, timers, `local-as`, `local-ip`, `remote-router-id`, message counters, per-AFI graceful-restart and prefix counts. |
| VRF config (N5) | `autonomous-system` and `router-id` are `auto` at VRF level; the real values are in N1. Neighbors carry `remote-as: internal`, `type: numbered`, `update-source: lo`, per-AFI `state`. **Each neighbor includes a `password` hash (`$nvsec$…`)**, so `get_bgp_config` redacts `password` leaves. |
| Credentials | `cumulus` / `Clab123!`. A wrong value in `NETMCP_NVIDIA_PASSWORD` (`Clab@123!`) was the cause of the 401. |
| Reply format | Plain JSON, no wrapper; numbers are JSON numbers. |

### 3.3 Captured replies

**N1** `router/bgp?rev=applied`:

```json
{
  "autonomous-system": 65000,
  "convergence-wait": {"establish-wait-time": 0, "time": 0},
  "graceful-restart": {"mode": "helper-only", "path-selection-deferral-time": 120,
                       "restart-time": 120, "stale-routes-time": 360},
  "graceful-shutdown": "disabled",
  "policy-update-timer": 5,
  "queue-limit": {"input": 10000, "output": 10000},
  "router-id": "192.1.1.7",
  "state": "enabled",
  "wait-for-install": "disabled"
}
```

N2–N5 are stored as fixtures in `tests/unit/test_cumulus_backend.py`. N2 is complete; N3 and N5 are trimmed, and every key kept is as captured.

### 3.4 Exit criteria

Met:
- Replies for N1–N5 are saved as fixtures.
- The §5 mapping is confirmed against N2/N3.
- §10 is updated.

---

## 4. Design

### 4.1 Files

```
src/netmcp/
├── nos/cumulus/
│   ├── __init__.py      # NOS_TYPE = "cumulus"; BACKEND = CumulusBackend()
│   ├── backend.py       # CumulusBackend(NotImplementedBackend): 4 BGP methods
│   ├── client.py        # nvue_get(): transport only, GET only
│   └── README.md        # API notes, credentials, known gaps
├── server.py            # + "cumulus": cumulus.BACKEND
└── inventory.py         # + kind map, transport / username defaults
tests/unit/
├── test_cumulus_backend.py
└── test_structure.py    # + httpx allow-list, GET-only check
```

### 4.2 `nos/cumulus/client.py`

```python
NVUE_PORT = 8765
TIMEOUT = 10  # seconds

def nvue_get(node: NodeInfo, path: str, rev: str = "operational") -> dict | None:
    """GET https://{fqdn}:8765/nvue_v1/{path}?rev={rev}, basic auth, verify=False.

    Read-only by construction: no other HTTP method is exposed.
    200 → parsed JSON; 404 → None; 401/403 → RuntimeError("authentication failed");
    anything else (incl. connect errors) → RuntimeError. Decided by status code only:
    a 401 body is nginx HTML, a 404 body is an RFC 7807 problem object.
    """
```

- `httpx.Client(verify=False, timeout=TIMEOUT, trust_env=False)`, with a new client per call (no shared state, same as the other clients). The self-signed cert is the default on Cumulus.
- `rev` is restricted to `{"operational", "applied"}`.
- The username comes from `node.username` and the password from `resolve_password(node)`.
- No logging to stdout (stdio mode safety).

### 4.3 `nos/cumulus/backend.py`

| Method | NVUE calls | Output |
|--------|-----------|--------|
| `get_bgp_summary` | N1 (`router/bgp`, applied) + N2 (`neighbor`, operational) | `{"as", "router-id", "peers": [compact peer…]}` |
| `get_bgp_neighbors` | N2 | `[compact peer…]`, or `"No BGP neighbors found on …"` |
| `get_bgp_neighbor(peer_ip)` | N3 | Raw NVUE operational neighbor object; N4 (unknown peer) → `"Error: could not retrieve BGP neighbor …"` |
| `get_bgp_config` | N1 + N5 | `{"router": {"bgp": N1}, "vrf": {"default": {"router": {"bgp": N5}}}}` (mirrors the NVUE tree), with every `password` leaf replaced by `"<redacted>"` |

- `_peer_summary(peer_ip, nvue_neighbor) -> dict` is a private module-level helper that maps NVUE fields to the shared compact view (§5). `utils/openconfig.peer_summary` doesn't apply, because NVUE isn't OpenConfig. The output keys are the same, so agents see one shape.
- `peer_ip` is validated with `ipaddress.ip_address()` before it goes into a URL.
- Every failure returns an `"Error: …"` string and never raises at the tool boundary (`RuntimeError` is caught in the backend).

### 4.4 Registration and inventory

| Change | File |
|--------|------|
| `"cumulus": cumulus.BACKEND` in `REGISTRY` | `server.py` |
| `"nvidia_cumulusvx": "cumulus"`, `"cumulus_cvx": "cumulus"` in `CLAB_KIND_TO_NOS` | `inventory.py` |
| `_NOS_TRANSPORT_DEFAULTS["cumulus"] = "rest"` (new transport value; the `NodeInfo.transport` comment updated) | `inventory.py` |
| New `_NOS_USERNAME_DEFAULTS = {"cumulus": "cumulus"}`, used by the YAML loader (when `username:` is absent) and the clab loader | `inventory.py` |
| `"httpx"` added explicitly to `dependencies` | `pyproject.toml` |
| `NETMCP_NVIDIA_PASSWORD=` example | `.env.example` |
| Example cumulus node | `netmcp.yml.example` |

- The NVUE port stays a client constant (8765) rather than a new `NodeInfo` field. See §10, Q3.
- No Cumulus password default is hardcoded. The password comes from `NETMCP_<NODE>_PASSWORD` or `NETMCP_DEFAULT_PASSWORD`.
- No changes go to `registry.py` or `dispatch.py`, because the BGP methods and tools already exist.

---

## 5. Output contract

The compact peer view matches EOS, Junos and NX-OS:

| Compact key | NVUE source (N2, operational) | Notes |
|-------------|-------------------------------|-------|
| `peer` | neighbor key (the IP) | |
| `peer-as` | `remote-as` | A number in operational state (`internal` only appears in config) |
| `state` | `session-state` | Upper-cased (`established` → `ESTABLISHED`) |
| `uptime` | `uptime` | Passed through as reported (591683000 in the lab) |
| `last-reset-reason` | `last-reset-reason` | Cumulus-only extra; useful when a peer is down |
| `afi-safis` | `address-family.<afi>` | Active families only (NVUE lists no others). Names: `l2vpn-evpn` → `L2VPN_EVPN`, `ipv4-unicast` → `IPV4_UNICAST`. Counts: `rx-prefix` → `received`, `tx-prefix` → `sent`. No `installed`, as on NX-OS. |

NVUE has no `peer-group` in operational state (the config shows `peer-group: none`), and no `established-transitions` or `last-established`. Those keys are omitted.

Summary as produced from the lab fixtures:

```json
{
  "as": 65000,
  "router-id": "192.1.1.7",
  "peers": {"total": 2, "established": 2},
  "neighbors": [
    {"peer": "192.1.2.1", "peer-as": 65000, "state": "ESTABLISHED",
     "afi-safis": {"L2VPN_EVPN": {"received": 10, "sent": 2}}},
    {"peer": "192.1.2.2", "peer-as": 65000, "state": "ESTABLISHED",
     "afi-safis": {"L2VPN_EVPN": {"received": 0, "sent": 2}}}
  ]
}
```

NVUE reports no BGP totals, so `total-paths` / `total-prefixes` are omitted, as on EOS and NX-OS. JSON goes through the shared formatter used by the other backends.

---

## 6. Documentation updates

| File | Change |
|------|--------|
| `AGENTS.md` | <ul><li>Rule 7 reworded to "schema-modeled APIs" (text in §2.4).</li><li>Rule 7's library note: `httpx` for OpenAPI REST.</li><li>"Data flow": the NOS list and a `nos/cumulus/` entry.</li><li>A new "NVUE REST conventions (Cumulus)" section: port 8765, basic auth, `trust_env=False`, `rev=` values, endpoints, error bodies are HTML.</li><li>`mv` lab facts for `nvidia`: `cumulus`, user `cumulus`, `NETMCP_NVIDIA_PASSWORD`, BGP peers.</li><li>`NodeInfo.transport` gains `"rest"`.</li></ul> |
| `.claude/skills/netmcp-ops/SKILL.md` | <ul><li>Description lists NVIDIA Cumulus.</li><li>A `cumulus` column in the support table (BGP ✓, everything else –).</li><li>Notes on fields Cumulus lacks.</li></ul> |
| `src/netmcp/nos/cumulus/README.md` | NVUE REST (NVIDIA's OpenAPI 3 model) notes, the link to NVIDIA's NVUE API documentation, credentials, known gaps |
| `README.md` | Add Cumulus to the supported-NOS list |

---

## 7. Testing and acceptance

### 7.1 Unit tests (no lab)

| Test | Covers |
|------|--------|
| `test_cumulus_backend.py` | Mocks `nvue_get` with the Phase 0 fixtures. Covers:<ul><li>summary: 2 peers ESTABLISHED, `L2VPN_EVPN` counts, AS and router-id from N1</li><li>neighbors</li><li>neighbor found</li><li>neighbor unknown (`None`) → error string</li><li>invalid `peer_ip` → error string</li><li>config shape</li><li>`RuntimeError` (auth failure, connection error) → error string</li><li>empty neighbors → "No BGP neighbors found"</li></ul> |
| client tests (in the same file) | 200/404/401/500 handling with `httpx.MockTransport`; `trust_env=False`; `rev` validation |
| `test_inventory.py` | `nvidia_cumulusvx` maps to `cumulus` with transport `rest` and username `cumulus`; an explicit `username:` in YAML still wins |
| `test_structure.py` | All existing rules pass. New: `httpx` is imported only from a NOS `client.py` or `utils/`; the Cumulus client calls no HTTP method other than `get`. The existing CLI-library checks stay as they are. |

`uv run pytest` must be green.

### 7.2 Live acceptance (`mv` lab)

1. Regenerate the inventory: `uv run netmcp inventory --from-clab mv -o netmcp.yml`. `nvidia` appears with `nos: cumulus`.
2. Set `NETMCP_NVIDIA_PASSWORD=Clab123!` in `.env`, then run `docker compose up -d --build` and reconnect.
3. Check the MCP tools:

| Call | Expected |
|------|----------|
| `list_nodes` | `nvidia` · `mv-nvidia` · `cumulus` |
| `get_bgp_summary(node="nvidia")` | AS 65000, router-id 192.1.1.7, peers 192.1.2.1 and 192.1.2.2 `ESTABLISHED`, `L2VPN_EVPN` received > 0 from at least one RR (192.1.2.2 sent 0 at capture time) |
| `get_bgp_neighbors(node="nvidia")` | The same two peers |
| `get_bgp_neighbor(node="nvidia", peer_ip="192.1.2.1")` | Full NVUE operational neighbor object |
| `get_bgp_neighbor(node="nvidia", peer_ip="10.0.0.99")` | `Error: …` string |
| `get_bgp_config(node="nvidia")` | AS 65000, router-id 192.1.1.7, neighbors with `remote-as internal`, `update-source lo`, l2vpn-evpn enabled, `password` shown as `<redacted>` |
| `get_evpn_instances(node="nvidia")` | `Error: get_evpn_instances is not implemented for NOS 'cumulus'.` |

4. Cross-check against `get_bgp_summary` on `sros` / `ptx-gw` (the RRs): both show 192.1.1.7 as ESTABLISHED.

---

## 8. Milestones

| # | Milestone | Done when |
|---|-----------|-----------|
| P0 ✅ | Captures N1–N5 | §3.4 exit criteria met |
| P1 ✅ | `nos/cumulus` client + backend + unit tests | `uv run pytest` green |
| P2 ✅ | Registration, inventory defaults, structure-test checks, docs | `test_structure.py` green, docs reviewed |
| P3 ✅ | Live verification (2026-10-13, via netmcp) | Every row in §7.2 passes |

---

## 9. Risks

| Risk | Impact | Mitigation |
|------|--------|-----------|
| The OpenAPI allowance is used where a YANG API exists, or for REST endpoints that wrap CLI | Erodes rule 7 | The reworded rule makes YANG preferred and bans CLI-wrapping endpoints. Reviewers check that any new OpenAPI transport cites the NOS's missing YANG support. |
| Config replies expose peer password hashes | Secrets leak to the agent | `get_bgp_config` redacts every `password` leaf (unit-tested) |
| Proxy env vars intercept NVUE requests | Tools return empty or failing results | `trust_env=False` in the client |
| NVUE listens on localhost only in other deployments | All Cumulus tools unreachable | Document `nv set system api listening-address` in the cumulus README (not needed in the `mv` lab) |
| NVUE operational schema changes between releases | Field mapping breaks | Mapping isolated in `_peer_summary`; fixtures record the release (5.16.1); missing fields become `null`, not errors |
| `rev=operational` is slow on large tables | Tool latency | BGP neighbor lists are small; measure in P3 |
| Cumulus VX takes minutes to boot | False negatives during verification | Wait for port 8765 to answer before testing |

---

## 10. Open questions

| # | Question | Recommendation | Resolved by |
|---|----------|----------------|-------------|
| Q1 | Exact NVUE field names for AS, state, uptime and per-AFI counts | ✅ Resolved: §5 | Phase 0 N2/N3 |
| Q2 | Does NVUE report BGP totals (paths/prefixes)? | ✅ Not in the neighbor reply; omitted | Phase 0 N2 |
| Q3 | NVUE port: a client constant or a new `NodeInfo.rest_port`? | Constant (8765) for now; add a field only when a deployment needs another port | Reviewer |
| Q4 | Hardcode `Clab123!` as the Cumulus default password? | ✅ No. Env only (`NETMCP_NVIDIA_PASSWORD`), with an entry in `.env.example`. | Implemented |
| Q5 | Allow NVUE writes later (EVPN provisioning)? | Out of scope. It needs its own PRD and an explicit rule-7 decision. | Future PRD |
| Q6 | Support VRFs other than `default`? | Out of scope; the path builder takes the VRF name so it can be extended | Future PRD |
