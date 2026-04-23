# Arista EOS Backend

This directory is a placeholder for the Arista EOS NOS backend.

**Transport:** gNMI (preferred) or eAPI (HTTP/JSON)

## How to implement

See `CONTRIBUTING.md` in the repo root for the full step-by-step guide. In summary:

1. Create `client.py` — implement `gnmi_get(node, path)` / `gnmi_set(node, path, value, op)` using pygnmi with EOS YANG paths, or an eAPI client.
2. Create `backend.py` — subclass `NotImplementedBackend` from `netmcp.registry`, overriding each supported method.
3. Create `contexts/` — add `system.py`, `interfaces.py`, `bgp.py` following the SR OS contexts as templates. Name tools `eos_*`.
4. Update `__init__.py` — set `BACKEND = EOSBackend()` and implement `register_vendor_tools()`.
5. Add `"eos"` to `REGISTRY` in `src/netmcp/registry.py`.
6. Call `eos.register_vendor_tools()` in `src/netmcp/server.py`.

## Containerlab kind

`arista_ceos` → auto-discovered as `nos_type = "eos"`.

## Useful references

- EOS gNMI: `arista/yang` GitHub repo
- eAPI: `https://{host}/command-api`
