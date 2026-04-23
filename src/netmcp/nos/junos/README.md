# Juniper JunOS Backend

This directory is a placeholder for the Juniper JunOS NOS backend.

**Transport:** NETCONF (via `ncclient`)

## How to implement

See `CONTRIBUTING.md` in the repo root for the full step-by-step guide. In summary:

1. Add `ncclient>=0.6.0` to `pyproject.toml` dependencies.
2. Create `client.py` — implement `netconf_get(node, filter)` / `netconf_edit(node, config)` using ncclient with JunOS YANG/XML.
3. Create `backend.py` — subclass `NotImplementedBackend` from `netmcp.registry`, overriding each supported method.
4. Create `contexts/` — add `system.py`, `interfaces.py`, `bgp.py` following the SR OS contexts as templates. Name tools `junos_*`.
5. Update `__init__.py` — set `BACKEND = JunOSBackend()` and implement `register_vendor_tools()`.
6. Add `"junos"` to `REGISTRY` in `src/netmcp/registry.py`.
7. Call `junos.register_vendor_tools()` in `src/netmcp/server.py`.

## Containerlab kind

`juniper_vjunosrouter` → auto-discovered as `nos_type = "junos"`.

## Useful references

- JunOS NETCONF: `https://www.juniper.net/documentation/us/en/software/junos/netconf/`
- JunOS YANG models: `https://github.com/Juniper/yang`
