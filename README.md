# netmcp

An [MCP](https://modelcontextprotocol.io) server that lets an AI agent such as Claude query and configure multivendor routers (Nokia SR OS, Nokia SR Linux, Arista EOS, Juniper Junos) over gNMI. You ask questions in plain English, and the agent calls vendor-neutral tools like `get_bgp_summary` or `get_evpn_instances`.

> 🎥 **Demo recording:** _coming soon_
>
> <!-- TODO: add the recording here (e.g. a GIF or a video link) -->

## Requirements

- A running [containerlab](https://containerlab.dev) lab with SR OS, SR Linux, EOS or Junos nodes (any topology), or your own `netmcp.yml` inventory
- Docker with Compose v2
- [`uv`](https://docs.astral.sh/uv/) (only used to generate the inventory)
- [Claude Code](https://claude.com/claude-code)

## Quick start

**1. Clone the repo and generate inventory:**

Make sure your containerlab lab is running (`containerlab inspect --all` shows its name).

```bash
git clone https://github.com/giancarlo3g/netmcp.git
cd netmcp
uv run netmcp inventory --from-clab <lab-name> -o netmcp.yml
```

This writes `netmcp.yml` with one entry per router; other nodes (e.g. linux clients) are skipped. You can omit `<lab-name>` when only one lab is running. Regenerate the file whenever you switch labs. To use devices outside containerlab, write `netmcp.yml` by hand from `netmcp.yml.example`.

If a node doesn't use its NOS default password, copy `.env.example` to `.env` and set it there: `NETMCP_<NODE>_PASSWORD` for one node (e.g. `NETMCP_LEAF1_PASSWORD`), or `NETMCP_DEFAULT_PASSWORD` for all of them.

**2. Pull the image and run the MCP container:**

```bash
docker compose up -d
```

The container joins the `clab` Docker network (containerlab's default management network; if your topology sets `mgmt.network`, run `CLAB_NETWORK=<network> docker compose up -d` instead) and serves MCP at `http://localhost:8088/mcp`. Run `docker logs netmcp` to see the nodes it loaded.

**3. Start Claude Code** from the repo root:

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

Try (replace the node names with ones from your lab; ask *Which nodes are available?* to list them):

- *Can you check BGP sessions in all routers?*
- *Can you list EVPN services on leaf1?*
- *Show me the state of EVPN instance mac-vrf-10 on leaf1.*
- *Which BGP peers on spine1 are not established?*
- *Compare the EVPN route-targets for VLAN 10 across all routers.*
- *Provision EVPN VLAN 20, VNI 1020, RT 65000:20 on leaf2 using Ethernet1. Show me a dry run first.*

Or with the skill named explicitly:

```
/netmcp-ops check BGP sessions in all routers
```

## Learn more

- [ARCHITECTURE.md](./ARCHITECTURE.md): the full tool list, per-vendor support, inventory options, environment variables, running without Docker (stdio), and how to add a new NOS.
- [AGENTS.md](./AGENTS.md): development notes and gNMI path conventions for each vendor.
