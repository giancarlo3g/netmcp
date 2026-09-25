---
name: netmcp-ops
description: Operate and troubleshoot network routers (Nokia SR OS, SR Linux, Arista EOS, Juniper Junos) through the netmcp MCP tools. Use whenever the user asks about routers, nodes, the lab, interfaces, ports, BGP peers/sessions, EVPN/VXLAN instances, VLANs, VNIs, route-targets, or wants to provision/delete a service or change an interface description — including requests that name a node (e.g. sros, srl, ceos, ptx, ptx-gw, dcgw1) or say "all routers".
---

# Operating routers with netmcp

## Guardrails (operator mode) — hard rules

These apply for the whole network task. They override any instinct to "just get it done".

1. **Devices are reached only through `mcp__netmcp__*` tools.** Never reach a router any other way:
   no `ssh`/`sshpass`/`telnet`, no `docker exec`/`containerlab exec`/`clab exec`, no `gnmic`/`gnmi_cli`/`netconf-console`,
   no `curl` to device addresses, no ad-hoc Python using pygnmi/ncclient/netmiko/scrapli, no importing `netmcp` modules in a script.
2. **Install nothing.** No `pip`, `uv add`, `uv pip`, `apt`, `npm`, `brew`, no new clients.
3. **Do not create, edit or delete repo files** (`src/`, `tests/`, `netmcp.yml`, `.mcp.json`, `containerlab/`, settings) during a network task,
   and never change code to work around a missing capability.
4. **When netmcp can't do it, stop and say so.** If a tool returns `Error: <method> is not implemented for NOS '<nos>'`, or the request needs something
   no tool exposes, tell the user which tool/NOS combination is missing and that it would be a netmcp development task. Do not improvise another path.
5. **Device changes only via netmcp write tools, and only with the safe-write protocol below.**
6. Code changes to netmcp happen only when the user explicitly asks to *develop* netmcp, as its own request — never inferred mid-operation.

## Start with the inventory

Call `list_nodes` first. It gives each node's short name and NOS type. Always pass the **short name** (`ceos`, not `mv-ceos`) as `node`; never guess FQDNs or node names.

## What each NOS supports

| Tools | sros | srl | eos | junos | iosxr |
|---|---|---|---|---|---|
| `get_system_info`, `get_system_alarms` | ✓ | – | – | – | – |
| `get_ports`, `get_interfaces`, `get_interface_state`, `set_interface_description` | ✓ | – | – | – | – |
| `get_bgp_summary`, `get_bgp_neighbors`, `get_bgp_neighbor`, `get_bgp_config` | ✓ | ✓ | ✓ | ✓ | – |
| `get_evpn_instances`, `get_evpn_instance`, `get_evpn_instance_state`, `provision_evpn_instance`, `delete_evpn_instance` | ✓ | ✓ | ✓ | ✓ | – |
| IS-IS, OSPF, MPLS, SR, VRF, log tools | – | – | – | – | – |

`–` returns a "not implemented" error string: that means *unsupported*, not *broken*. Don't retry it, and don't call it on purpose — skip that node and say so.

## "All routers" questions

1. `list_nodes`, then keep only the nodes whose NOS supports the tool (table above).
2. Call the tool once per node (independent calls can run in parallel).
3. Answer with **one consolidated table** (node, NOS, key fields), followed by a short "Skipped" line listing unsupported/unreachable nodes and why.
4. Highlight anomalies (sessions not ESTABLISHED, zero prefixes on an active AFI-SAFI, missing instances) rather than dumping raw output.

## Safe-write protocol

Applies to `provision_evpn_instance`, `delete_evpn_instance`, `set_interface_description`.

1. **Pre-check** with a read: `get_evpn_instance` (must not exist before provision / must exist before delete) or `get_interface_state`.
2. **Dry run**: call with `dry_run=True` and show the user the payload.
3. **Confirm**: wait for an explicit "yes" from the user. A request to make a change is not confirmation of a specific payload.
4. **Apply**: repeat the exact same call with `dry_run=False`.
5. **Verify** with the matching read (`get_evpn_instance`, then `get_evpn_instance_state`; or `get_interface_state`) and report the result.

For several nodes, dry-run all of them, confirm once for the whole set, then apply node by node and stop at the first error.

## `provision_evpn_instance` per NOS

| NOS | Model | Needs | Ignored |
|---|---|---|---|
| sros | VPLS + BGP-EVPN + VXLAN | `service_id`, `evi` | `interface_name`, `vlan_id` |
| srl | MAC-VRF + `vxlan0.<vni>` + bridged subinterface | `interface_name` (e.g. `ethernet-1/3`), `vlan_id` | `service_id` |
| eos | VLAN-based EVPN (VLAN, VLAN→VNI on Vxlan1, `router bgp / vlan`) | `interface_name` (a **trunk** switchport, e.g. `Ethernet1`), `vlan_id` | `service_id`, `evi` (VLAN id is the EVI) |
| junos | vlan-based `mac-vrf` + access unit | `interface_name` (`et-0/0/2` → unit = `vlan_id`, or `et-0/0/2.20`), `vlan_id`, **`export_rt == import_rt`** | `service_id`, `evi` |

- RTs: `target:65000:10`; EOS and Junos also accept `65000:10`.
- Junos: the parent port must already have `flexible-vlan-tagging` and `encapsulation flexible-ethernet-services`. netmcp never changes it, so if it's missing, tell the user.
- EOS and Junos instances can be looked up by VLAN name or VLAN id as well as by name.

## Troubleshooting recipes

**EVPN service end to end** (e.g. "is VNI 1010 working?"):
1. `get_bgp_summary` on every participating node: the EVPN peers must be ESTABLISHED with `L2VPN_EVPN` active and prefixes received > 0.
2. `get_evpn_instances` on every node: the same VNI/RT must be present everywhere it should be.
3. `get_evpn_instance` on each node: compare VNI, RD, import/export RT. A mismatched RT is the usual culprit.
4. `get_evpn_instance_state` on each node: VTEPs/remote peers and learned MACs. Each leaf should see the others as VTEPs.

**BGP peer down**: `get_bgp_summary` → `get_bgp_neighbor` (state, last error, AFI-SAFIs) → `get_bgp_config` on both ends (peer AS, address, group, families).

## Known quirks (not bugs)

- EOS reports `redistribute` as `LEARNED, ROUTER_MAC, HOST_ROUTE` even when only `LEARNED` was set.
- The EOS BGP summary has no `total-paths`/`total-prefixes`. SR OS, SR Linux and Junos report them.
- `ptx-gw` is the spine/route reflector: "No EVPN instances found" there is expected.
- Junos (cJunos Evolved) takes several minutes to boot. If calls to it fail after ~5 s with an empty error once it's up, ask the user to reconnect the server (`/mcp` → netmcp → Reconnect). Don't try to diagnose it yourself.
- An unknown node name returns an `Error:` string. Re-check `list_nodes`.

## Lab baseline (multivendor lab `mv` only)

EVPN reference service: VLAN 10, VNI 1010, RT 65000:10. It's `mac-vrf-10` on SR Linux and `EVPN-VXLAN10` (RD 10:3, access `et-0/0/2.10`) on `ptx`. `ptx-gw` has no EVPN instance. Use this as the expected state when checking the lab; it doesn't apply to other inventories.
