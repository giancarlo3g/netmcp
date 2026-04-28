# netmcp

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server that exposes network routers from multiple vendors to LLM agents. It allows an AI assistant (such as Claude) to query and configure routers directly — reading BGP state, managing interfaces, provisioning EVPN services, and more — without knowing vendor-specific CLI syntax.

**Currently implemented:** Nokia SR OS (via gNMI)
**Placeholder support:** Nokia SR Linux, Arista EOS, Juniper JunOS, Cisco IOS-XR

## Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) package manager
- A running containerlab topology or a `netmcp.yml` inventory file pointing at reachable nodes

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

`.mcp.json` (already included in the repo):
```json
{
  "mcpServers": {
    "netmcp": {
      "command": "uv",
      "args": ["run", "netmcp"]
    }
  }
}
```

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

### Option 2 — Docker container (HTTP/SSE)

The server runs as a long-lived container and exposes an HTTP endpoint. This is useful when you want a shared server, run multiple clients, or prefer not to install Python locally.

Build and start the container:
```bash
docker compose up -d --build
```

Point `.mcp.json` at the HTTP endpoint:
```json
{
  "mcpServers": {
    "netmcp": {
      "url": "http://localhost:8088/mcp"
    }
  }
}
```

Credentials and inventory overrides are set in `compose.yaml` (or a `.env` file) as environment variables — see [Environment Variables](#environment-variables) below.

---

Prompt examples (either option):
- Check interfaces in all SROS routers
- Provide BGP status of all neighbors by router

## Node Inventory

The server supports two discovery modes (in priority order):

### 1. Static inventory file (`netmcp.yml`)

Create a `netmcp.yml` in your project root (searched upward from cwd):

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

### 2. Containerlab auto-discovery

If no `netmcp.yml` is found, the server scans for `containerlab/*.clab.yml` upward from cwd and auto-discovers nodes by their containerlab kind. Override the topology file path with `NETMCP_CLAB_TOPOLOGY=/path/to/topo.yml`.

### Environment Variables

| Variable | Description |
|---|---|
| `NETMCP_{NODE_UPPER}_PASSWORD` | Per-node password (e.g. `NETMCP_DCGW1_PASSWORD`) |
| `NETMCP_DEFAULT_PASSWORD` | Global password for all nodes |
| `SROS_PASSWORD` | Legacy alias for SR OS nodes |
| `NETMCP_CLAB_TOPOLOGY` | Explicit path to a containerlab topology file |
| `NETMCP_NO_INVENTORY` | Set to `1` to start without any inventory (CI/testing) |

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
├── server.py          # FastMCP entrypoint — builds REGISTRY, registers all unified tools
├── inventory.py       # NodeInfo dataclass, static YAML + containerlab discovery
├── registry.py        # NOSBackend Protocol and NotImplementedBackend
├── dispatch.py        # Unified cross-vendor tools (29 tools)
├── nos/
│   ├── sros/          # Nokia SR OS — fully implemented
│   │   ├── backend.py # SROSBackend — implements NOSBackend Protocol
│   │   └── client.py  # gNMI transport (gnmi_get / gnmi_set)
│   ├── srl/           # Nokia SR Linux — placeholder (Phase 3)
│   ├── eos/           # Arista EOS — placeholder
│   ├── junos/         # Juniper JunOS — placeholder
│   └── iosxr/         # Cisco IOS-XR — placeholder
└── utils/
    └── formatters.py  # Output formatting helpers

tests/
└── unit/
    └── test_dispatch.py  # 30 tests — dispatch routing, error handling, NotImplementedBackend
```

## Adding a New NOS

Each `nos/{vendor}/` directory is self-contained. See the `README.md` inside any placeholder directory (e.g. `nos/eos/README.md`) for step-by-step instructions.
