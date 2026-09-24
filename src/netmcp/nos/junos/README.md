# Juniper JunOS Backend

This directory is a placeholder for the Juniper JunOS NOS backend.

**Transport:** NETCONF (via `ncclient`)

## How to implement

Follow the fixed NOS layout (see "Architecture rules" in `CLAUDE.md`); `nos/srl/` and `nos/eos/` are the reference implementations.

0. Add `ncclient>=0.6.0` to `pyproject.toml` dependencies.
1. Create `client.py`: transport only. Implement `netconf_get(node, filter)` / `netconf_edit(node, config)` using ncclient with JunOS YANG/XML.
2. Create `backend.py`: `JunOSBackend(NotImplementedBackend)` from `netmcp.registry`, overriding only `NOSBackend` Protocol methods. Parse replies with `netmcp.utils.yang.ns_get` and format output with `netmcp.utils.formatters`.
3. Update `__init__.py`: set `BACKEND = JunOSBackend()`.
4. `REGISTRY` in `src/netmcp/server.py` already maps `"junos"`, so nothing else needs registering.

Do not add MCP tools, `contexts/` directories, or vendor-prefixed tools here. Every tool lives in `src/netmcp/dispatch.py`. A new capability means a new method on the `NOSBackend` Protocol in `registry.py` plus a unified tool in `dispatch.py`.

## Containerlab kind

`juniper_vjunosrouter` → auto-discovered as `nos_type = "junos"`.

## Useful references

- JunOS NETCONF: `https://www.juniper.net/documentation/us/en/software/junos/netconf/`
- JunOS YANG models: `https://github.com/Juniper/yang`
