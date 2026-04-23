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

### With Claude Code (recommended)

The repository includes `.mcp.json`, which registers the server automatically when you open the project in Claude Code. No additional setup is needed — Claude will discover and connect to the server.

To override credentials, edit `.claude/settings.json`:

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

### Standalone

```bash
uv run netmcp
```

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

Tools come in two categories:

- **Unified tools** — vendor-agnostic, work across all supported NOS (e.g. `get_interfaces`). Implemented in Phase 3.
- **Vendor tools** — NOS-specific, prefixed with the vendor name (e.g. `sros_evpn_provision_vpls`). Available now.

### Unified tools *(Phase 3 — coming soon)*

| Tool | Description |
|---|---|
| `list_nodes` | List all nodes in the inventory with their NOS type and hostname |
| `get_system_info` | Get system information from any node |
| `get_system_alarms` | Get active alarms from any node |
| `get_interfaces` | Get all interfaces from any node |
| `get_interface_state` | Get detailed state for a specific interface |
| `set_interface_description` | Set an interface description (supports `dry_run`) |
| `get_bgp_summary` | BGP summary statistics |
| `get_bgp_neighbors` | All BGP neighbor states |
| `get_bgp_neighbor` | Detailed state for a specific BGP neighbor |
| `get_bgp_config` | BGP configuration |

### SR OS vendor tools *(available now)*

#### System
| Tool | Description |
|---|---|
| `sros_list_nodes` | List all discovered SR OS nodes and their hostnames |
| `sros_system_info` | Get system information (version, uptime, etc.) |
| `sros_system_alarms` | Get active system alarms |

#### Interfaces
| Tool | Description |
|---|---|
| `sros_get_ports` | List physical ports and their operational state |
| `sros_get_interfaces` | List router interfaces in the Base routing instance |
| `sros_get_interface_state` | Get detailed state for a specific interface |
| `sros_set_interface_description` | Set an interface description (supports `dry_run`) |

#### BGP
| Tool | Description |
|---|---|
| `sros_bgp_summary` | BGP summary statistics from the Base routing instance |
| `sros_bgp_neighbors` | All BGP neighbor states |
| `sros_bgp_neighbor` | Detailed state for a specific BGP neighbor |
| `sros_bgp_config` | BGP configuration from the Base routing instance |

#### EVPN / VPLS
| Tool | Description |
|---|---|
| `sros_evpn_list_services` | List all VPLS services on a node |
| `sros_evpn_get_service` | Get configuration for a specific VPLS service |
| `sros_evpn_get_service_state` | Get operational state for a specific VPLS service |
| `sros_evpn_provision_vpls` | Provision an EVPN VPLS service with VXLAN (supports `dry_run`) |
| `sros_evpn_delete_vpls` | Delete a VPLS service (supports `dry_run`) |

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
sudo containerlab deploy -t nokia-evpn.clab.yml
```

> **Note:** `nokia_srsim` requires a local Docker image and a valid license file. See the [containerlab documentation](https://containerlab.dev) and contact Nokia for the simulator image and license.

Once the lab is running, start the MCP server from the repository root — it will auto-discover nodes and resolve hostnames (e.g., `clab-evpn-dcgw1`).

## Project Structure

```
src/netmcp/
├── server.py          # FastMCP entrypoint — loads inventory, registers NOS tools
├── inventory.py       # NodeInfo dataclass, static YAML + containerlab discovery
├── registry.py        # NOSBackend Protocol and NotImplementedBackend
├── dispatch.py        # Unified cross-vendor tools (Phase 3)
├── nos/
│   ├── sros/          # Nokia SR OS — fully implemented
│   │   ├── client.py  # gNMI transport (gnmi_get / gnmi_set)
│   │   └── contexts/
│   │       ├── system.py, interfaces.py, bgp.py, evpn.py
│   ├── srl/           # Nokia SR Linux — placeholder (Phase 2)
│   ├── eos/           # Arista EOS — placeholder
│   ├── junos/         # Juniper JunOS — placeholder
│   └── iosxr/         # Cisco IOS-XR — placeholder
└── utils/
    └── formatters.py  # Output formatting helpers
```

## Adding a New NOS

Each `nos/{vendor}/` directory is self-contained. See the `README.md` inside any placeholder directory (e.g. `nos/eos/README.md`) for step-by-step instructions.
