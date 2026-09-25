# netmcp — architecture and reference

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server that exposes network routers from multiple vendors to LLM agents. It allows an AI assistant (such as Claude) to query and configure routers directly — reading BGP state, managing interfaces, provisioning EVPN services, and more — without knowing vendor-specific CLI syntax.

**Currently implemented (via gNMI):** Nokia SR OS (system, interfaces, BGP, EVPN), Nokia SR Linux (EVPN), Arista EOS (EVPN, BGP), Juniper Junos Evolved (EVPN, BGP)
**Placeholder support:** Cisco IOS-XR

## Prerequisites

- A running containerlab topology or a `netmcp.yml` inventory file pointing at reachable nodes
- Local process: Python 3.11+ and the [`uv`](https://docs.astral.sh/uv/) package manager
- Docker: Docker with Compose v2. `uv` is only needed to generate `netmcp.yml` with `netmcp inventory --from-clab`

## Installation

```bash
git clone <repo-url>
cd netmcp
uv sync
```

## Usage

Two options are available depending on whether you want to run the server as a local process or a container.

### Option 1 — Local process (stdio)

The server runs as a child process of the MCP client using stdio transport. This is the simplest setup and requires no extra infrastructure.

The repo's `.mcp.json` uses the HTTP setup from Option 2 by default and keeps the stdio entry under a disabled `_disabled_local_stdio` key (JSON has no comments, and Claude Code only starts servers listed under `mcpServers`). To use the local process, swap the two `netmcp` blocks:
```json
{
  "mcpServers": {
    "netmcp": {
      "command": "uv",
      "args": ["run", "netmcp"]
    }
  },
  "_disabled_http": {
    "netmcp": { "type": "http", "url": "http://localhost:8088/mcp" }
  }
}
```

Then reload the server in Claude Code: run `/mcp`, select **netmcp**, and choose **Reconnect** (or restart Claude Code). Keep the server name `netmcp`, so existing approvals and `mcp__netmcp__*` permissions still apply. To switch back to HTTP, swap the blocks again and reconnect.

To pass credentials, add them under `env` in `.claude/settings.json`:
```json
{
  "mcpServers": {
    "netmcp": {
      "command": "uv",
      "args": ["run", "netmcp"],
      "env": { "NETMCP_DEFAULT_PASSWORD": "your-password" }
    }
  }
}
```

Start the lab and open Claude:
```bash
cd containerlab && containerlab deploy
claude
```

### Option 2 — Docker container (HTTP or stdio)

The image serves MCP over streamable HTTP at `http://<host>:8088/mcp` by default, so any coding agent that supports remote MCP servers can use it. It needs an inventory file and a network path to the routers, plus passwords for nodes that don't use the NOS default.

**1. Inventory.** Mount a `netmcp.yml` at `/inventory/netmcp.yml` (see `netmcp.yml.example`). For a running containerlab lab, generate it on the lab host:
```bash
uv run netmcp inventory --from-clab            # the only running lab
uv run netmcp inventory --from-clab mv -o netmcp.yml
```
Each node's `fqdn` is its container name (e.g. `mv-sros`), which resolves once netmcp joins the lab network.

**2. Network.** Attach the container to the lab's Docker network (`clab` unless the topology sets `mgmt.network`). Containerlab node names only resolve through the host's `/etc/hosts`, and Docker blocks traffic between separate bridge networks, so a container on its own network can't reach the lab.

**3. Passwords (optional).** Nodes use the NOS default password unless `NETMCP_<NODE>_PASSWORD` or `NETMCP_DEFAULT_PASSWORD` is set. With compose, put them in a `.env` file (see `.env.example`).

Start it with compose from a clone of this repo. It pulls the published image, reads `./netmcp.yml` and `.env`, and joins `clab`:
```bash
docker compose pull          # fetch the latest image (compose only pulls on its own when the image is missing)
docker compose up -d
```
To run your working tree instead, build the image locally with `docker compose up -d --build`. The local build is tagged with the same image name, so a later `docker compose pull` replaces it with the published one.

Or run the published image with plain Docker (drop `--env-file` if you have no `.env`):
```bash
docker run -d --name netmcp --network clab -p 127.0.0.1:8088:8088 \
  -v "$PWD/netmcp.yml:/inventory/netmcp.yml:ro" --env-file .env \
  ghcr.io/giancarlo3g/netmcp:latest
```
Images for linux/amd64 and linux/arm64 are published by `.github/workflows/docker.yml`: `latest` from `main`, and `X.Y.Z` / `X.Y` from `vX.Y.Z` tags. If the pull fails with `denied`, you have stale credentials for ghcr.io; the image is public, so `docker logout ghcr.io` fixes it. (Maintainers: GHCR makes a new package private on its first push; set it to public once under the package's settings on GitHub.)

Compose settings, all optional, in `.env` or the shell:

| Variable | Default | Description |
|---|---|---|
| `NETMCP_IMAGE` | `ghcr.io/giancarlo3g/netmcp:latest` | Image to pull or build, e.g. a pinned `:0.1` |
| `NETMCP_INVENTORY` | `./netmcp.yml` | Inventory file to mount |
| `CLAB_NETWORK` | `clab` | Lab Docker network (topology `mgmt.network`) |
| `NETMCP_HTTP_PORT` | `8088` | Host port, bound to `127.0.0.1` |

Check it with `docker compose ps` (the image has a healthcheck) and `docker logs netmcp`, which lists the loaded nodes at startup.

Point your agent at the endpoint. The repo's `.mcp.json` already does this for Claude Code (after `docker compose up -d`, run `/mcp` → **netmcp** → **Reconnect** if Claude Code was started first):
```bash
claude mcp add --transport http netmcp http://localhost:8088/mcp   # Claude Code
```
```json
{ "mcpServers": { "netmcp": { "type": "http", "url": "http://localhost:8088/mcp" } } }
```

For agents that only launch stdio servers, run the image as the command and set `MCP_PORT` empty:
```json
{
  "mcpServers": {
    "netmcp": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "--network", "clab", "-e", "MCP_PORT=",
               "-v", "/abs/path/netmcp.yml:/inventory/netmcp.yml:ro",
               "ghcr.io/giancarlo3g/netmcp:latest"]
    }
  }
}
```

The server has write tools and no authentication. Compose publishes the port on `127.0.0.1` only; keep it that way unless the network in front of it is trusted. Behind a corporate proxy, the image already sets `grpc_proxy=""` so gNMI does not go through `https_proxy`.

---

Prompt examples (either option):
- Check interfaces in all SROS routers
- Provide BGP status of all neighbors by router

### Operator mode (Claude Code)

The project skill `.claude/skills/netmcp-ops/SKILL.md` teaches the agent how to use the netmcp tools safely: which tools each NOS supports, how to fan out across nodes, and a dry-run → confirm → apply → verify flow for every change. It also tells the agent to reach devices only through netmcp and to leave the code alone. `.claude/settings.json` enforces the device-access part by denying `ssh`, `docker exec`, `containerlab exec`, `gnmic` and package installs.

Code edits can't be denied project-wide without blocking development. For a session that can only talk to routers through netmcp, start Claude Code with the file and shell tools removed:

```bash
claude --disallowedTools "Edit,Write,NotebookEdit,Bash"
```

## Node Inventory

The server supports two discovery modes (in priority order):

### 1. Static inventory file (`netmcp.yml`)

Create a `netmcp.yml` in your project root (searched upward from cwd), starting from `netmcp.yml.example`. It is gitignored. When it exists it takes priority over containerlab discovery, including `NETMCP_CLAB_TOPOLOGY`.

```yaml
inventory:
  nodes:
    - name: dcgw1
      fqdn: clab-evpn-dcgw1
      nos: sros
      tags: [dcgw, dc1]

    - name: spine1
      fqdn: clab-evpn-spine1
      nos: srl

    - name: pe1
      fqdn: pe1.prod.example.com
      nos: iosxr
      transport: netconf
```

`netmcp inventory --from-clab [LAB] [-o FILE]` writes one from a running containerlab lab (`containerlab inspect`), mapping each kind to its NOS and skipping `linux` nodes.

### 2. Containerlab auto-discovery

If no `netmcp.yml` is found, the server scans for `containerlab/*.clab.yml` upward from cwd and auto-discovers nodes by their containerlab kind. Override the topology file path with `NETMCP_CLAB_TOPOLOGY=/path/to/topo.yml`; the server refuses to start if that path doesn't exist.

### gNMI ports

Each NOS has a default gNMI port; set `gnmi_port` on a node in `netmcp.yml` to override it.

| NOS | Default port | Notes |
|---|---|---|
| SR OS, SR Linux | 57400 | |
| Arista EOS | 6030 | |
| Juniper Junos | 32767 | Enable with `set system services extension-service request-response grpc clear-text port 32767`. Don't use 57400: it is in the Linux ephemeral range (32768–60999), and Junos Evolved's internal `trace-relay` can take it as a source port at boot, leaving gNMI refusing connections. |

### Environment Variables

| Variable | Description |
|---|---|
| `NETMCP_{NODE_UPPER}_PASSWORD` | Per-node password (e.g. `NETMCP_DCGW1_PASSWORD`) |
| `NETMCP_DEFAULT_PASSWORD` | Global password for all nodes |
| `SROS_PASSWORD` | Legacy alias for SR OS nodes |
| `NETMCP_CLAB_TOPOLOGY` | Explicit path to a containerlab topology file |
| `NETMCP_NO_INVENTORY` | Set to `1` to start without any inventory (CI/testing) |
| `MCP_PORT` | Serve streamable HTTP on this port (`/mcp`); unset or empty means stdio. The Docker image sets `8088` |
| `MCP_HOST` | HTTP bind address (default `127.0.0.1`; the Docker image sets `0.0.0.0`) |

## Available Tools

All tools are vendor-agnostic and dispatch automatically to the correct NOS backend based on the node's type. No vendor-specific knowledge is required from the caller.

### System
| Tool | Description |
|---|---|
| `list_nodes` | List all nodes in the inventory with their NOS type and hostname |
| `get_system_info` | System information (platform, version, uptime) from any node |
| `get_system_alarms` | Active alarms from any node |

### Interfaces
| Tool | Description |
|---|---|
| `get_ports` | Get all physical ports and their operational state |
| `get_interfaces` | Get all logical interfaces from any node |
| `get_interface_state` | Get detailed operational state for a specific interface |
| `set_interface_description` | Set an interface description (supports `dry_run`) |

### BGP
| Tool | Description |
|---|---|
| `get_bgp_summary` | BGP summary statistics |
| `get_bgp_neighbors` | All BGP neighbor states |
| `get_bgp_neighbor` | Detailed state for a specific BGP neighbor |
| `get_bgp_config` | BGP configuration |

### EVPN
| Tool | Description |
|---|---|
| `get_evpn_instances` | List all EVPN instances normalised to `{name, type, vni, evi}` |
| `get_evpn_instance` | Full configuration for a specific EVPN instance by name |
| `get_evpn_instance_state` | Operational state for a specific EVPN instance by name |
| `provision_evpn_instance` | Create an EVPN instance (VPLS + BGP-EVPN + VXLAN) — supports `dry_run` |
| `delete_evpn_instance` | Delete an EVPN instance — supports `dry_run` |

How each NOS models an EVPN instance, and what `provision_evpn_instance` needs:

| NOS | EVPN instance | Provision needs | Notes |
|---|---|---|---|
| SR OS | VPLS service with BGP-EVPN + VXLAN | `service_id`, `evi` | |
| SR Linux | `mac-vrf` + bridged subinterface + `vxlan0.N` tunnel interface | `interface_name` (e.g. `ethernet-1/3`), `vlan_id` | |
| EOS | VLAN + Vxlan1 VLAN-to-VNI + `router bgp / vlan N` | `interface_name` (trunk port, e.g. `Ethernet1`), `vlan_id` | VLAN id is reported as the EVI; `evi`/`service_id` ignored |
| Junos | vlan-based `mac-vrf` routing-instance (VXLAN, VTEP source `lo0.0`) + `vlan-bridge` access unit | `interface_name` (`et-0/0/2`, unit = `vlan_id`, or `et-0/0/2.20`), `vlan_id` | `export_rt` must equal `import_rt` (one `vrf-target`); the parent port needs `flexible-vlan-tagging` + `encapsulation flexible-ethernet-services` already; VLAN id is reported as the EVI |

On EOS and Junos, provision and delete are a single atomic gNMI Set: if the device rejects any part, nothing is applied.

### IGP *(SR OS only for now)*
| Tool | Description |
|---|---|
| `get_isis_adjacencies` | All IS-IS adjacencies and their state |
| `get_isis_database` | IS-IS link-state database (LSDB) summary |
| `get_isis_config` | IS-IS instance configuration |
| `get_ospf_neighbors` | OSPF neighbor states |
| `get_ospf_config` | OSPF instance configuration |

### MPLS / Segment Routing *(SR OS only for now)*
| Tool | Description |
|---|---|
| `get_mpls_lsps` | Active MPLS LSPs |
| `get_sr_sid_table` | Segment Routing SID binding table |
| `get_sr_config` | Segment Routing configuration |

### VRF / L3VPN *(SR OS only for now)*
| Tool | Description |
|---|---|
| `get_vrfs` | All VRFs / network-instances |
| `get_vrf_routes` | Route table for a specific VRF |
| `get_vrf_interfaces` | Interfaces bound to a specific VRF |

### Logging *(SR OS only for now)*
| Tool | Description |
|---|---|
| `get_log_events` | Recent log events — `count` (default 50) and `severity` filter params |
| `get_log_config` | Logging destinations configuration |

> **Note:** IGP, MPLS/SR, VRF, and Logging tools are wired up but SR OS backend implementations are Phase 4 work. Calling them today returns `"Error: <method> is not implemented for NOS 'sros'."` until Phase 4 lands.

Write tools that support `dry_run: true` show the full gNMI payload that would be sent without making any changes to the device.

## Lab Environment

A full containerlab topology is provided in `containerlab/nokia-evpn.clab.yml`. It models a data-center fabric with EVPN/VXLAN:

```
clients → leaves (SR Linux) → spines (SR Linux) → DCGWs (SR OS)
```

| Node | Kind | Role |
|---|---|---|
| `dcgw1`, `dcgw2` | Nokia SR OS SR-1 (`nokia_srsim`) | Data-center gateways — **MCP targets** |
| `spine1`, `spine2` | Nokia SR Linux IXR-D3L | Clos spines |
| `leaf1`–`leaf4` | Nokia SR Linux IXR-D2L | Clos leaves |
| `client1`, `client2` | Linux | Test endpoints |

The lab runs eBGP as the underlay and MP-BGP EVPN over VXLAN as the overlay, with L2VPN services configured on the DCGWs. Startup configs are in `containerlab/configs/`.

### Starting the lab

```bash
cd containerlab
containerlab deploy
```

> **Note:** `nokia_srsim` requires a local Docker image and a valid license file. See the [containerlab documentation](https://containerlab.dev) and contact Nokia for the simulator image and license.

Once the lab is running, start the MCP server from the repository root — it will auto-discover nodes and resolve hostnames (e.g., `clab-evpn-dcgw1`).

## Project Structure

```
src/netmcp/
├── cli.py             # `netmcp` command — runs the server, or `netmcp inventory --from-clab`
├── server.py          # FastMCP app — loads the inventory, builds REGISTRY, registers all unified tools
├── inventory.py       # NodeInfo dataclass, static YAML + containerlab discovery, netmcp.yml generation
├── registry.py        # NOSBackend Protocol and NotImplementedBackend
├── dispatch.py        # Unified cross-vendor tools — the only place MCP tools are registered
├── nos/
│   ├── sros/          # Nokia SR OS — system, interfaces, BGP, EVPN
│   │   ├── __init__.py  # NOS_TYPE, BACKEND
│   │   ├── backend.py   # SROSBackend — implements NOSBackend Protocol
│   │   └── client.py    # gNMI transport
│   ├── srl/           # Nokia SR Linux — EVPN (MAC-VRF); same three files
│   ├── eos/           # Arista EOS — EVPN (VLAN-based), BGP; same three files
│   ├── junos/         # Juniper Junos Evolved — EVPN (mac-vrf), BGP; same three files
│   └── iosxr/         # Cisco IOS-XR — placeholder
└── utils/
    ├── formatters.py  # Output formatting helpers
    ├── yang.py        # json_ietf reply helpers (ns_get, strip_prefix)
    └── openconfig.py  # OpenConfig list/leaf walking, compact BGP peer view (EOS, Junos)

tests/
└── unit/
    ├── test_dispatch.py     # dispatch routing, error handling, NotImplementedBackend
    ├── test_inventory.py    # netmcp.yml generation from containerlab inspect, discovery errors
    ├── test_srl_backend.py  # SR Linux EVPN parsing
    ├── test_eos_backend.py  # EOS EVPN parsing, provision, delete; BGP
    ├── test_junos_backend.py # Junos EVPN parsing, provision, delete; BGP
    └── test_structure.py    # enforces the layout below
```

## Adding a New NOS

Each `nos/{vendor}/` directory has a fixed layout: `__init__.py` (`NOS_TYPE`, `BACKEND`), `backend.py` (a `NotImplementedBackend` subclass that overrides only `NOSBackend` Protocol methods), `client.py` (transport only), and an optional `README.md`. MCP tools are registered only in `dispatch.py`. `tests/unit/test_structure.py` fails if this layout is broken. See `nos/junos/README.md` for step-by-step instructions, and `nos/srl/` and `nos/eos/` for reference implementations.
