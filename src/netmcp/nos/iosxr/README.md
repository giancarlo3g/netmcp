# Cisco IOS-XR Backend

This directory is a placeholder for the Cisco IOS-XR NOS backend.

**Transport:** gNMI (preferred) or NETCONF (via `ncclient`)

## How to implement

Follow the fixed NOS layout (see "Architecture rules" in `CLAUDE.md`); `nos/srl/` and `nos/eos/` are the reference implementations.

1. Create `client.py`: transport only. Implement `gnmi_get(node, path)` / `gnmi_set(node, path, value, op)` using pygnmi with IOS-XR YANG paths (Cisco-IOS-XR-* namespaces), or ncclient for NETCONF.
2. Create `backend.py`: `IOSXRBackend(NotImplementedBackend)` from `netmcp.registry`, overriding only `NOSBackend` Protocol methods. Parse replies with `netmcp.utils.yang.ns_get` and format output with `netmcp.utils.formatters`.
3. Update `__init__.py`: set `BACKEND = IOSXRBackend()`.
4. `REGISTRY` in `src/netmcp/server.py` already maps `"iosxr"`, so nothing else needs registering.

Do not add MCP tools, `contexts/` directories, or vendor-prefixed tools here. Every tool lives in `src/netmcp/dispatch.py`. A new capability means a new method on the `NOSBackend` Protocol in `registry.py` plus a unified tool in `dispatch.py`.

## Containerlab kind

`cisco_xrd` → auto-discovered as `nos_type = "iosxr"`.

## Useful references

- IOS-XR gNMI: `https://www.cisco.com/c/en/us/td/docs/iosxr/ncs5500/programmability/b-programmability-cg-ncs5500-75x/m-gnmi-protocol.html`
- IOS-XR YANG models: `https://github.com/YangModels/yang/tree/main/vendor/cisco/xr`
