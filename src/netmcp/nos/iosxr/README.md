# Cisco IOS-XR Backend

This directory is a placeholder for the Cisco IOS-XR NOS backend.

**Transport:** gNMI (preferred) or NETCONF (via `ncclient`)

## How to implement

See `CONTRIBUTING.md` in the repo root for the full step-by-step guide. In summary:

1. Create `client.py` — implement `gnmi_get(node, path)` / `gnmi_set(node, path, value, op)` using pygnmi with IOS-XR YANG paths (Cisco-IOS-XR-* namespaces), or ncclient for NETCONF.
2. Create `backend.py` — subclass `NotImplementedBackend` from `netmcp.registry`, overriding each supported method.
3. Create `contexts/` — add `system.py`, `interfaces.py`, `bgp.py` following the SR OS contexts as templates. Name tools `iosxr_*`.
4. Update `__init__.py` — set `BACKEND = IOSXRBackend()` and implement `register_vendor_tools()`.
5. Add `"iosxr"` to `REGISTRY` in `src/netmcp/registry.py`.
6. Call `iosxr.register_vendor_tools()` in `src/netmcp/server.py`.

## Containerlab kind

`cisco_xrd` → auto-discovered as `nos_type = "iosxr"`.

## Useful references

- IOS-XR gNMI: `https://www.cisco.com/c/en/us/td/docs/iosxr/ncs5500/programmability/b-programmability-cg-ncs5500-75x/m-gnmi-protocol.html`
- IOS-XR YANG models: `https://github.com/YangModels/yang/tree/main/vendor/cisco/xr`
