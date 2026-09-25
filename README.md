# netmcp

An [MCP](https://modelcontextprotocol.io) server that lets an AI agent such as Claude query and configure multivendor routers (Nokia SR OS, Nokia SR Linux, Arista EOS, Juniper Junos) over gNMI. You ask questions in plain English, and the agent calls vendor-neutral tools like `get_bgp_summary` or `get_evpn_instances`.

> 🎥 **Demo recording:** _coming soon_
>
> <!-- TODO: add the recording here (e.g. a GIF or a video link) -->

## Requirements

- A running [containerlab](https://containerlab.dev) lab (the examples use the multivendor lab `mv`)
- Docker with Compose v2
- [`uv`](https://docs.astral.sh/uv/) (only used to generate the inventory)
- [Claude Code](https://claude.com/claude-code)

## Quick start

**1. Clone the repo:**

```bash
git clone https://github.com/giancarlo3g/netmcp.git
cd netmcp
```

**2. Generate the inventory** from the running lab:

```bash
uv run netmcp inventory --from-clab mv -o netmcp.yml
```

This writes `netmcp.yml` with one entry per router (e.g. `sros`, `srl`, `ceos`, `ptx`, `ptx-gw`).

If a node doesn't use its NOS default password, copy `.env.example` to `.env` and set it there, e.g. `NETMCP_PTX_PASSWORD=admin@123`.

**3. Pull the image:**

```bash
docker compose pull
```

**4. Start the server:**

```bash
docker compose up -d
```

The container joins the `clab` Docker network and serves MCP at `http://localhost:8088/mcp`. Run `docker logs netmcp` to see the nodes it loaded.

**5. Start Claude Code** from the repo root:

```bash
claude
```

The repo's `.mcp.json` already points Claude Code at the server over HTTP:

```json
{ "mcpServers": { "netmcp": { "type": "http", "url": "http://localhost:8088/mcp" } } }
```

Approve the `netmcp` server when asked, then run `/mcp` to check that it's connected. If you started Claude before the container, pick **netmcp → Reconnect** there.

## Ask questions

The project skill `netmcp-ops` (`.claude/skills/netmcp-ops/SKILL.md`) loads automatically when you ask about routers. It tells Claude which tools each vendor supports, how to query every node at once, and to run a dry run and ask for confirmation before any change. You can also call it explicitly with `/netmcp-ops`.

Try:

- *Can you check BGP sessions in all routers?*
- *Can you list EVPN services in the mv-srl node?*
- *Show me the state of EVPN instance mac-vrf-10 on srl.*
- *Which BGP peers on ceos are not established?*
- *Compare the EVPN route-targets for VLAN 10 across all routers.*
- *Provision EVPN VLAN 20, VNI 1020, RT 65000:20 on ceos using Ethernet1. Show me a dry run first.*

Or with the skill named explicitly:

```
/netmcp-ops check BGP sessions in all routers
```

## Learn more

- [ARCHITECTURE.md](./ARCHITECTURE.md): the full tool list, per-vendor support, inventory options, environment variables, running without Docker (stdio), and how to add a new NOS.
- [AGENTS.md](./AGENTS.md): development notes and gNMI path conventions for each vendor.
