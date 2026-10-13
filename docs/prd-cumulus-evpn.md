# netmcp: NVIDIA Cumulus Linux EVPN tools (PRD)

**Version:** 0.2 | **Date:** 2026-10-13 | **Status:** Implemented (P0–P3); live acceptance (P4) pending

**Changes from 0.1:**
- Phase 0 ran on `mv-nvidia` (captures R1–R10, write spike W1–W8). The results are in §5.4, and the design follows them.
- `get_evpn_instance_state` is built on `evpn/vni/<VNI>` and `evpn/vni/<VNI>/mac`; there is no VLAN or VNI up/down state.
- Success states include `applied_and_saved`. The switch auto-saves.

Transport, credentials and the rule-7 rationale are covered in [`prd-cumulus-bgp.md`](./prd-cumulus-bgp.md). In short, Cumulus is reached through the NVUE REST API, which is based on NVIDIA's OpenAPI 3 specification, on port 8765 with `trust_env=False`. This PRD adds the five EVPN methods on top of that client, including the first **writes** over NVUE.

Anything marked **(confirm in Phase 0)** is taken from NVIDIA's documentation or the lab startup config and still has to be checked against live replies.

---

## Context

The `cumulus` backend implements the four BGP reads and was verified live on `mv-nvidia` (Cumulus VX 5.16.1). All five EVPN tools still return `Error: … is not implemented for NOS 'cumulus'`. The `mv` lab runs a VLAN-based EVPN service on `nvidia` that netmcp can't see or manage today. Its config, from `nvidia_startup.cfg`:

```
nv set bridge domain br_default type vlan-aware
nv set bridge domain br_default untagged none
nv set bridge domain br_default vlan 10 vni 1010
nv set evpn state enabled
nv set evpn vni 1010 rd 10:7
nv set evpn vni 1010 route-target both 65000:10
nv set interface swp1 bridge domain br_default untagged none
nv set interface swp1 bridge domain br_default vlan 10
nv set nve vxlan source address 192.1.1.7
nv set nve vxlan state enabled
```

This PRD specifies reads and writes for that service model. The work fits the fixed architecture: no new tools, no Protocol change, and only `nos/cumulus/` changes, plus docs and one structure-test adjustment.

---

## 1. Goals and non-goals

**Goals**

| # | Goal |
|---|------|
| G1 | `get_evpn_instances`, `get_evpn_instance` and `get_evpn_instance_state` work on Cumulus, with the same shapes as NX-OS (the other VLAN-based NOS). |
| G2 | `provision_evpn_instance` and `delete_evpn_instance` work on Cumulus, with `dry_run`. Each write is one NVUE revision, applied all-or-nothing. |
| G3 | A failed apply never leaves a pending revision behind. |
| G4 | Unit tests use reply shapes captured from `mv-nvidia` (rule 6). |

**Non-goals**

- L3 VNIs, symmetric IRB, SVIs, ARP/ND suppression, EVPN multihoming.
- Bridges other than `br_default`, and VRFs.
- `nv config save`: changes go to the applied (running) config only, as on NX-OS and EOS.
- Device-side dry run (NVUE `dry_run_complete`).
- Creating prerequisites: NVE, EVPN enable, the bridge, or bridge membership of a port.

---

## 2. Decisions

| Topic | Decision |
|---|---|
| Instance name | `vlanN` (e.g. `vlan10`). NVUE has no VLAN name. |
| Lookup | `instance_name` may be `vlanN`, the VLAN id (`10`) or the VNI (`1010`). |
| EVI | Reported as the VLAN id, as on EOS, Junos and NX-OS. |
| Ignored arguments | `service_name`, `service_id`, `evi`. The tool reports the instance as `vlanN`. |
| Bridge | `br_default` only (a backend constant). Provision fails if it doesn't exist or isn't VLAN-aware. |
| Access port | `interface_name` (`swpN`) must already be a `br_default` member. Provision adds `vlan_id` to its tagged VLAN list and never changes `untagged`. |
| Delete and ports | The VLAN is removed from every port that carries it, **except** where it is the port's only VLAN (the port is reported and left alone), as on NX-OS. |
| RD | `x:y` or `auto` (NVUE derives it). |
| RTs | Explicit only (`65000:10` or `target:65000:10`; `auto` is rejected). Equal import and export → `route-target both`, otherwise separate `import` and `export` entries. |
| Persistence | netmcp never requests a save. **However**, `system config auto-save` is enabled by default on Cumulus 5.x (it is in `mv`), so every applied revision is also saved to startup (`applied_and_saved`). |
| Dry run | Local: returns the NVUE PATCH JSON and doesn't contact the device for the write (it still reads, for pre-checks). |
| Failure handling | If apply ends `invalid` / `ays_fail` / times out, netmcp deletes the revision it created and returns NVUE's issue text. |
| Write spike | The user runs it, on throwaway service VLAN 20 / VNI 1020 / swp1. |

---

## 3. Data model

One Cumulus EVPN instance is three NVUE objects (paths under `/nvue_v1/`):

| Object | NVUE path | Config (`rev=applied`) |
|---|---|---|
| VLAN + VNI mapping | `bridge/domain/br_default/vlan/<N>` | `{"vni": {"<VNI>": {…}}}` |
| EVPN VNI | `evpn/vni/<VNI>` | `{"rd": "10:7" \| "auto", "route-target": {"both"\|"import"\|"export": {"65000:10": {}}}}` |
| Port membership | `interface/<swpN>/bridge/domain/br_default/vlan/<N>` | `{}` |

Prerequisites are checked before any write and are never created:

| Prerequisite | NVUE path | Check |
|---|---|---|
| Bridge | `bridge/domain/br_default` | exists, `type: vlan-aware` |
| VXLAN | `nve/vxlan` | `state: enabled`, `source.address` set |
| EVPN | `evpn` | `state: enabled` |
| Port | `interface/<swpN>/bridge/domain/br_default` | exists (the port is a bridge member) |

All JSON key names and nesting in these tables are **(confirm in Phase 0)**.

### 3.1 Write payloads

A write is a single root PATCH into a new revision.

**Provision** (VLAN 20, VNI 1020, RD 20:7, RT 65000:20 both ways, swp1):

```json
{
  "bridge": {"domain": {"br_default": {"vlan": {"20": {"vni": {"1020": {}}}}}}},
  "evpn": {"vni": {"1020": {"rd": "20:7", "route-target": {"both": {"65000:20": {}}}}}},
  "interface": {"swp1": {"bridge": {"domain": {"br_default": {"vlan": {"20": {}}}}}}}
}
```

When import and export differ, `route-target` is `{"import": {"<rt>": {}}, "export": {"<rt>": {}}}`. With RD `auto`, `rd` is `"auto"` (confirm in Phase 0 whether `auto` must be sent or the key simply omitted).

**Delete** (`null` unsets):

```json
{
  "bridge": {"domain": {"br_default": {"vlan": {"20": null}}}},
  "evpn": {"vni": {"1020": null}},
  "interface": {"swp1": {"bridge": {"domain": {"br_default": {"vlan": {"20": null}}}}}}
}
```

The `interface` part lists every port that carries the VLAN, apart from ports where it's the only VLAN.

---

## 4. NVUE write workflow

From the [NVUE API documentation](https://docs.nvidia.com/networking-ethernet-software/cumulus-linux/System-Configuration/NVIDIA-User-Experience-NVUE/NVUE-API/):

| Step | Request | Result |
|---|---|---|
| 1. Create revision | `POST /nvue_v1/revision` | `{"<rev-id>": {"state": "pending", "transition": {…}}}` |
| 2. Stage changes | `PATCH /nvue_v1/?rev=<rev-id>` with the JSON tree | staged in the revision |
| 3. Apply | `PATCH /nvue_v1/revision/<rev-id>` with `{"state": "apply", "auto-prompt": {"ays": "ays_yes"}}` | `{"state": "apply", …}` |
| 4. Poll | `GET /nvue_v1/revision/<rev-id>` | `applied` (done), `invalid` (validation failed), `ays_fail` (prompt not confirmed); `transition.issue` holds the reasons |
| Cleanup on failure | `DELETE /nvue_v1/revision/<rev-id>` | revision removed |

- **Atomicity:** one revision is applied as a whole. A failed apply leaves the running config unchanged, so no rollback helper is needed, as with EOS, Junos and NX-OS.
- **Revision ids** may contain `/` (e.g. `changeset/cumulus/2026-10-13_…`) and must be URL-encoded **(confirm in Phase 0)**.
- **`ays_yes`** auto-confirms NVUE's "are you sure" prompts, as `nv config apply -y` does. netmcp's own pre-checks and the user's dry-run confirmation are the safety gates.
- **Saving** (`PATCH revision/applied` with `{"state": "save"}`) is not used.

---

## 5. Phase 0: captures and write spike (the user runs these)

The user runs these with the project's credentials. Everything bypasses the proxy, and responses include the HTTP status.

```bash
export NETMCP_NVIDIA_PASSWORD='Clab123!'
# Reads: prints body + HTTP status
NV()  { curl -sk --noproxy '*' -u "cumulus:${NETMCP_NVIDIA_PASSWORD}" \
          -w '\nHTTP %{http_code}\n' "https://mv-nvidia:8765/nvue_v1/$1"; }
# Any method: NVJ METHOD PATH [JSON]  (body only, for piping into jq)
NVJ() { curl -sk --noproxy '*' -u "cumulus:${NETMCP_NVIDIA_PASSWORD}" \
          -H 'Content-Type: application/json' -X "$1" \
          "https://mv-nvidia:8765/nvue_v1/$2" ${3:+-d "$3"}; }
# Poll a revision until it settles
POLL() { for i in $(seq 1 60); do s=$(NVJ GET "revision/$1" | jq -r .state);
           echo "$(date +%T) $s"; case $s in applied|invalid|ays_fail|apply_fail) break;; esac;
           sleep 1; done; NVJ GET "revision/$1" | jq .; }
```

### 5.1 Reads (VLAN 10 baseline)

| # | Command | Answers |
|---|---------|---------|
| R1 | `NV 'bridge/domain/br_default?rev=applied'` | VLAN → VNI shape, bridge type |
| R2 | `NV 'evpn?rev=applied'` | `evpn/vni` RD / RT shape (`both`) |
| R3 | `NV 'nve?rev=applied'` | VXLAN state and source-address keys |
| R4 | `NV 'interface/swp1?rev=applied'` | Port bridge membership shape (`untagged`, `vlan`) |
| R5 | `NV 'evpn/vni?rev=operational'` and `NV 'evpn/vni/1010?rev=operational'` | VNI oper state, RD/RT in use, remote VTEPs? MAC/route counts? |
| R6 | `NV 'bridge/domain/br_default/vlan/10?rev=operational'` | VLAN oper state and members |
| R7 | `NV 'bridge/domain/br_default/mac-table?rev=operational'` | MAC entries: VLAN, interface, local vs remote, remote VTEP IP |
| R8 | `NV 'nve/vxlan?rev=operational'` | Flooding list / remote VTEPs |
| R9 | `NV 'evpn/vni/9999?rev=operational'` | Unknown VNI: expected 404 |
| R10 | `NV 'interface?rev=applied' \| jq 'with_entries(select(.value.bridge != null)) \| keys'` | Which ports are `br_default` members |

### 5.2 Write spike (VLAN 20 / VNI 1020 on swp1)

```bash
# W1: create a revision; the id may contain "/" → URL-encode it
REV=$(NVJ POST revision | jq -r 'keys[0]'); R=$(printf %s "$REV" | jq -sRr @uri); echo "rev=$REV"

# W2: stage the provision tree
NVJ PATCH "?rev=$R" '{
  "bridge": {"domain": {"br_default": {"vlan": {"20": {"vni": {"1020": {}}}}}}},
  "evpn": {"vni": {"1020": {"rd": "20:7", "route-target": {"both": {"65000:20": {}}}}}},
  "interface": {"swp1": {"bridge": {"domain": {"br_default": {"vlan": {"20": {}}}}}}}
}' | jq .

# W3: apply, then W4: poll (note how long it takes)
NVJ PATCH "revision/$R" '{"state": "apply", "auto-prompt": {"ays": "ays_yes"}}' | jq .
POLL "$R"

# W5: read back
NV 'bridge/domain/br_default/vlan/20?rev=applied'
NV 'evpn/vni/1020?rev=applied'
NV 'interface/swp1/bridge?rev=applied'
NV 'evpn/vni/1020?rev=operational'

# W6: delete with nulls in a new revision
REV=$(NVJ POST revision | jq -r 'keys[0]'); R=$(printf %s "$REV" | jq -sRr @uri)
NVJ PATCH "?rev=$R" '{
  "bridge": {"domain": {"br_default": {"vlan": {"20": null}}}},
  "evpn": {"vni": {"1020": null}},
  "interface": {"swp1": {"bridge": {"domain": {"br_default": {"vlan": {"20": null}}}}}}
}' | jq .
NVJ PATCH "revision/$R" '{"state": "apply", "auto-prompt": {"ays": "ays_yes"}}' | jq .
POLL "$R"
NV 'bridge/domain/br_default/vlan/20?rev=applied'   # expect 404
NV 'interface/swp1/bridge?rev=applied'              # VLAN 10 still there

# W7a: schema-invalid PATCH (VNI 0): is it rejected at PATCH time?
REV=$(NVJ POST revision | jq -r 'keys[0]'); R=$(printf %s "$REV" | jq -sRr @uri)
curl -sk --noproxy '*' -u "cumulus:${NETMCP_NVIDIA_PASSWORD}" -H 'Content-Type: application/json' \
  -w '\nHTTP %{http_code}\n' -X PATCH "https://mv-nvidia:8765/nvue_v1/?rev=$R" \
  -d '{"bridge": {"domain": {"br_default": {"vlan": {"21": {"vni": {"0": {}}}}}}}}'

# W7b: semantically invalid apply (VNI 1010 already used by VLAN 10): what do state/issue look like?
NVJ PATCH "?rev=$R" '{"bridge": {"domain": {"br_default": {"vlan": {"21": {"vni": {"1010": {}}}}}}}}' | jq .
NVJ PATCH "revision/$R" '{"state": "apply", "auto-prompt": {"ays": "ays_yes"}}' | jq .
POLL "$R"

# W8: delete the leftover revision, then confirm it's gone
curl -sk --noproxy '*' -u "cumulus:${NETMCP_NVIDIA_PASSWORD}" -w '\nHTTP %{http_code}\n' \
  -X DELETE "https://mv-nvidia:8765/nvue_v1/revision/$R"
NV "revision/$R"
NVJ GET revision | jq 'keys'   # no pending revisions left from the spike
```

### 5.3 Questions Phase 0 answers

| # | Question | From |
|---|----------|------|
| P1 | Exact key names and nesting for the VLAN→VNI mapping, `evpn/vni` RD/RT, NVE and port membership | R1–R4, W5 |
| P2 | Where VNI oper state, remote VTEPs and remote MACs (with VTEP IP) live in operational state | R5–R8 |
| P3 | Revision id format and whether it needs URL encoding | W1 |
| P4 | Does `null` cleanly remove the VLAN, its VNI mapping, the EVPN VNI and port membership? | W6 |
| P5 | Typical apply duration → poll interval and timeout (proposed: 1 s / 60 s) | W4 |
| P6 | Error shapes: PATCH-time 400 body vs apply-time `invalid` + `transition.issue` | W7a, W7b |
| P7 | Does `DELETE revision/<id>` work for pending and failed revisions? | W8 |
| P8 | Does RD `auto` need to be sent explicitly? Does NVUE accept `target:` RTs or only `65000:10`? | Extra PATCH in W2 if needed |
| P9 | Valid VLAN range (reserved range, default 3725–3999) | `NV 'bridge/domain/br_default?rev=operational'` or docs |

**Exit criteria:** P1–P9 answered and recorded in §11. Replies saved as fixtures. The VLAN 20 / VNI 1020 test service is gone from `mv-nvidia`, and no spike revisions are left.

### 5.4 Results (2026-10-13, run on `mv-nvidia` 5.16.1)

| # | Answer |
|---|--------|
| P1 | Shapes are as in §3:<ul><li>`bridge/domain/br_default/vlan/<N>/vni/<VNI>`</li><li>`evpn/vni/<VNI>` with `rd` and `route-target/{both,import,export}/<rt>: {}`</li><li>port `interface/<swp>/bridge/domain/br_default/vlan/<N>: {}`</li><li>`nve/vxlan` `state` + `source.address`, `evpn.state`</li></ul>`rev=applied` replies include defaults (e.g. `route-advertise`, empty `import`/`export`). |
| P2 | `evpn/vni/<VNI>` (operational) has `local-vtep`, `mac-count`, `host-count`, `remote-vtep-count`, `remote-vtep/<ip>/flood` (`HER`), `vxlan-interface` (`vxlan48`). There is **no up/down state** for a VNI or VLAN. `evpn/vni/<VNI>/mac` lists EVPN MACs with `type` (`remote`/local) and `remote-vtep`. `bridge/domain/br_default/mac-table` is the raw FDB (not used). |
| P3 | Revision ids are plain numbers (`6`, `7`, …). The client still URL-encodes them. |
| P4 | The `null` delete removed the VLAN, its VNI mapping, the EVPN VNI and swp1's VLAN 20 cleanly. VLAN 10 was untouched. |
| P5 | An apply took ~9 s (`apply` → `readying` → `reloading` → `auto_saving` → `applied_and_saved`), a delete ~8 s. Poll interval 1 s, timeout 60 s. |
| P6 | Schema errors are rejected at PATCH time: HTTP 400 with `detail` (`"Error: 0 is less than the minimum of 1"`) plus `validation.selected_errors`. Semantic errors end in `state: invalid` within ~4 s, with `transition.issue.<n>` = `{code: "duplicate_vni", message: "VNI 1010 configured for bridge br_default already in use. …", severity: "error"}`. |
| P7 | `DELETE revision/<id>` returns 204 for both an `invalid` and a `pending` revision. A later GET returns 404. |
| P8 | `"rd": "auto"` is accepted as a literal. Separate `import`/`export` RTs are accepted. `target:65000:31` passes schema validation, but netmcp strips `target:` so values match what NVUE shows. |
| P9 | The schema allows VLANs 1–4094. `system/global/reserved` reserves 3725–3999 (internal) and 4000–4064 (L3-VNI VLANs), so netmcp accepts 2–3724. |
| — | A pending revision `5` from before the spike existed on the switch. netmcp works in its own revision and doesn't touch it. |

---

## 6. Design

### 6.1 Files

```
src/netmcp/nos/cumulus/
├── client.py    # + nvue_apply(node, tree, timeout=60)
├── backend.py   # + 5 EVPN methods, _EvpnIntent, _collect/_find_instance, _provision_tree/_delete_tree
└── README.md    # EVPN section
tests/unit/
├── test_cumulus_backend.py   # + EVPN fixtures/tests, nvue_apply tests
└── test_structure.py         # GET-only check → "writes only in nvue_apply"
```

No change to `registry.py`, `server.py` or `inventory.py`. `dispatch.py` changes only in the `provision_evpn_instance` docstring.

### 6.2 `client.py`: `nvue_apply`

```python
APPLY_TIMEOUT = 60   # seconds (P5)
POLL_INTERVAL = 1

def nvue_apply(node: NodeInfo, tree: dict, timeout: int = APPLY_TIMEOUT) -> None:
    """Apply `tree` as one NVUE revision: create, PATCH /nvue_v1/?rev=, apply with ays_yes, poll.

    Returns when the revision is `applied`. On a PATCH error, `invalid`, `ays_fail`
    or timeout, deletes the revision it created and raises RuntimeError with
    NVUE's issue text. Never saves to startup.
    """
```

- This is the only function in `client.py` that issues `POST`/`PATCH`/`DELETE`. `nvue_get` stays as it is.
- It uses one `httpx.Client` for the whole sequence, with the same settings as `nvue_get` (`verify=False`, `trust_env=False`, basic auth).
- The revision id is URL-encoded (P3).
- Errors come from status codes and the revision `state`. The issue text comes from `transition.issue` (P6).

### 6.3 `backend.py`

**Reads**
- `_collect(node)` reads `bridge/domain/br_default`, `evpn/vni` and the bridge membership of every interface (all `rev=applied`). It returns records `{vlan_id, vni, evpn, ports}`, where `ports` lists the interfaces carrying the VLAN.
- `_find_instance(records, name)` matches on `vlanN`, the VLAN id or the VNI.

| Method | Output |
|---|---|
| `get_evpn_instances` | `[{"name": "vlan10", "type": "vlan", "vni": 1010, "evi": 10}]`, or `"No EVPN instances found on …"` |
| `get_evpn_instance` | `{name, vlan-id, vni, route-distinguisher, import-rt, export-rt, bridge: "br_default", access-interfaces: [swp…], vlan: <raw NVUE>, evpn: <raw NVUE>}` |
| `get_evpn_instance_state` | `{name, vlan-id, vni, access-interfaces, vni-state: {bridge-domain, vxlan-interface, local-vtep, tenant-vrf, mac-count, host-count, remote-vtep-count}, vteps: [{peer, flood}], macs: [{mac, learned, vtep}]}` (sources: `evpn/vni/<VNI>` and `evpn/vni/<VNI>/mac`, operational) |

**Writes**
- `_EvpnIntent` (pydantic):
  - `vni` 1–16777214
  - `vlan_id` in the valid range (P9; proposed 2–3724)
  - `interface_name` matches `swp\d+(s\d+)?`
  - `route_distinguisher` is `auto` or `x:y`
  - `export_rt` / `import_rt` are explicit `x:y` with an optional `target:` prefix
- Pre-checks, each failing with `Error: …` before any write:
  1. The bridge exists and is VLAN-aware, NVE is enabled with a source address, and EVPN is enabled.
  2. The VLAN is not already in `br_default`, and the VNI is not already mapped.
  3. `interface_name` exists and is a `br_default` member.
- `_provision_tree(intent)` and `_delete_tree(record)` build the §3.1 JSON.
- `provision_evpn_instance`: validate → pre-check → build the tree. Then, if `dry_run`, return `format_dry_run(node, "PATCH /nvue_v1/?rev=<new>", tree)`. Otherwise call `nvue_apply`, which returns `OK: EVPN instance 'vlan20' (VLAN=20, VNI=1020, iface=swp1, RD=…, export-RT=…, import-RT=…) provisioned on nvidia.`
- `delete_evpn_instance`: find the instance → build the delete tree (skip ports where it is the only VLAN; list them in a note) → dry run or apply → `OK: EVPN instance 'vlan20' (VLAN=20, VNI=1020) deleted from nvidia.`
- A `RuntimeError` from `nvue_apply` becomes `Error: provisioning failed on nvidia; no changes were applied. <issue>`.

### 6.4 Structure test

The BGP PRD's `test_read_only_rest_clients_use_get_only` is replaced by `test_cumulus_writes_only_in_nvue_apply`. In `nos/cumulus/client.py`, `.post()`, `.patch()`, `.put()` and `.delete()` may appear only inside `nvue_apply`. The httpx containment check is unchanged.

---

## 7. Output examples (target; values confirmed in Phase 0)

`get_evpn_instances(node="nvidia")`:

```json
{"nvidia": [{"name": "vlan10", "type": "vlan", "vni": 1010, "evi": 10}]}
```

`get_evpn_instance(node="nvidia", instance_name="1010")`:

```json
{"nvidia": {
  "name": "vlan10", "vlan-id": 10, "vni": 1010,
  "route-distinguisher": "10:7", "import-rt": ["65000:10"], "export-rt": ["65000:10"],
  "bridge": "br_default", "access-interfaces": ["swp1"],
  "vlan": {"vni": {"1010": {}}},
  "evpn": {"rd": "10:7", "route-target": {"both": {"65000:10": {}}}}
}}
```

---

## 8. Documentation updates

| File | Change |
|---|---|
| `.claude/skills/netmcp-ops/SKILL.md` | `cumulus` gets ✓ in the EVPN row. Add a provision table row: "VLAN in `br_default` + VLAN→VNI + `evpn vni` (RD/RT); needs `interface_name` (a `br_default` member, e.g. `swp1`) and `vlan_id`; ignores `service_id`, `evi`, `service_name` (named `vlanN`)". Add a lookup note (`vlanN`, VLAN id or VNI) and the `mv` baseline (`vlan10` on `nvidia`). |
| `src/netmcp/dispatch.py` | `provision_evpn_instance` docstring: Cumulus in the `service_id`/`evi`/RD/RT/`interface_name` notes. |
| `AGENTS.md` | NVUE section: EVPN paths, the revision workflow, failure cleanup, apply-only. Data-flow entry for `nos/cumulus/`. `mv` lab facts. |
| `ARCHITECTURE.md` | EVPN per-NOS table row and atomicity sentence. |
| `src/netmcp/nos/cumulus/README.md` | EVPN section, write workflow, troubleshooting (a pending revision left behind → `nv config detach` / `DELETE revision/<id>`). |

---

## 9. Testing and acceptance

### 9.1 Unit tests (no lab)

| Test | Covers |
|---|---|
| EVPN reads | Lookup by `vlan10` / `10` / `1010`. Unknown name → not-found string. List, instance and state outputs from R1–R8 fixtures. |
| Provision | Validation errors: VLAN range, VNI, interface name, `auto` RT. Pre-check errors: VLAN exists, VNI mapped, port not a member, NVE/EVPN disabled. Tree with `both` vs separate import/export RTs, and RD `auto`. Dry run returns the tree and calls no `nvue_apply`. |
| Delete | Tree removes the VLAN, VNI and port memberships. Port where it's the only VLAN → skipped and noted. Dry run. |
| `nvue_apply` (`httpx.MockTransport`) | Success sequence (POST → PATCH → apply → poll `applied`). PATCH 400 → revision deleted + `RuntimeError`. `invalid` → revision deleted + issue text. Timeout → revision deleted. URL-encoded revision id. |
| `test_structure.py` | Write verbs only inside `nvue_apply`. |

### 9.2 Live acceptance (`mv` lab, MCP tools, safe-write protocol)

All calls are on `nvidia` unless stated.

| # | Call | Expected |
|---|------|----------|
| 1 | `get_evpn_instances` | `vlan10`, VNI 1010, EVI 10 |
| 2 | `get_evpn_instance("vlan10")` (also `"10"`, `"1010"`) | RD 10:7, RT 65000:10 both ways, `access-interfaces: [swp1]` |
| 3 | `get_evpn_instance_state("vlan10")` | VLAN/VNI up. Remote VTEPs include other leaves. MACs: client-7 local on swp1, other clients remote. |
| 4 | `provision_evpn_instance(vlan_id=20, vni=1020, interface_name="swp1", route_distinguisher="20:7", export_rt="65000:20", import_rt="65000:20", dry_run=True)` | PATCH tree as §3.1 |
| 5 | Same with `dry_run=False`, then `get_evpn_instance("vlan20")` | `OK: …`; instance present with swp1 |
| 6 | `get_bgp_summary` | `sent` for L2VPN_EVPN increases |
| 7 | `delete_evpn_instance("vlan20")`: dry run, then apply | `vlan20` gone; `get_evpn_instance("vlan10")` still shows swp1 |
| 8 | Error cases: provision VLAN 10 again; `interface_name="swp9"`; `interface_name="swp2"` (routed uplink, not a bridge member) | `Error: …`, nothing applied |
| 9 | After the run, no pending revisions are left on the device | Checked with `NVJ GET revision` (user) |

---

## 10. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Apply takes longer than the timeout | A false error while the change still lands | Timeout from P5 measurements. On timeout, netmcp re-reads the revision state once before deleting it. |
| Another user has a pending revision | NVUE may refuse a concurrent apply | netmcp always uses its own new revision and reports NVUE's error. Document `nv config detach`. |
| `ays_yes` confirms warnings blindly | Unexpected side effects | Strict pre-checks, the minimal PATCH tree (only the three objects), and dry run + user confirmation (safe-write protocol) |
| `null` removes more than intended | Collateral config loss | Delete tree targets only `vlan/<N>`, `evpn/vni/<VNI>` and port `vlan/<N>`. Verified in W6 and live check 7. |
| NVUE reply shapes change across releases | Parsing breaks | Fixtures record 5.16.1. Missing keys become `null`, not exceptions. |
| Leftover revisions after a crash mid-apply | Clutter, possible conflicts | `try/finally` cleanup in `nvue_apply`. README troubleshooting entry. |

---

## 11. Open questions

| # | Question | Recommendation | Resolved by |
|---|----------|----------------|-------------|
| Q1 | Key names for the VLAN→VNI mapping, RD/RT and port membership | ✅ §5.4 P1 | Phase 0 |
| Q2 | Source of remote VTEPs and remote MACs | ✅ `evpn/vni/<VNI>` and `.../mac` | Phase 0 |
| Q3 | Apply timeout | ✅ 60 s (an apply takes ~9 s) | Phase 0 |
| Q4 | Does `rd: "auto"` need to be sent, or is the key omitted? | ✅ Sent literally | Phase 0 |
| Q5 | VLAN range | ✅ 2–3724 | Phase 0 |
| Q7 | Removing a port's only VLAN would leave it with no VLAN list, which NVUE treats as all bridge VLANs. Is skipping such ports (keeping the VLAN there while removing it from the bridge) accepted by NVUE? | Not exercised live; if NVUE rejects it, the whole delete fails safely (`invalid`, nothing applied) | P4 or later |
| Q6 | Should delete refuse when the VLAN is the only one on a port, instead of skipping that port? | Skip and note, as on NX-OS | Decided |

---

## 12. Milestones

| # | Milestone | Done when |
|---|-----------|-----------|
| P0 ✅ | Captures (R1–R10) + write spike (W1–W8) | §5.3 exit criteria met |
| P1 ✅ | EVPN reads + tests | `uv run pytest` green |
| P2 ✅ | `nvue_apply`, provision and delete + tests, structure-test update | `uv run pytest` green |
| P3 ✅ | Docs (§8) | Reviewed |
| P4 | Live acceptance | Every row in §9.2 passes |
