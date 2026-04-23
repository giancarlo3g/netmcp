# router-mcp

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server that exposes Nokia SR OS routers to LLM agents via gNMI. It allows an AI assistant (such as Claude) to query and configure SR OS devices directly — reading BGP state, managing interfaces, and provisioning EVPN/VPLS services.

## Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) package manager
- A running containerlab topology using `nokia_srsim` nodes (see [Lab Environment](#lab-environment))
- SR OS simulator license (required by `nokia_srsim`)

## Installation

```bash
git clone <repo-url>
cd router-mcp
uv sync
```

## Usage

### With Claude Code (recommended)

The repository includes `.mcp.json`, which registers the server automatically when you open the project in Claude Code. No additional setup is needed — Claude will discover and connect to the server.

To override the default gNMI password, edit `.claude/settings.json`:

```json
{
  "mcpServers": {
    "router-mcp": {
      "command": "uv",
      "args": ["run", "router-mcp"],
      "env": { "SROS_PASSWORD": "your-password" }
    }
  }
}
```

### Standalone

```bash
uv run router-mcp
```

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SROS_PASSWORD` | `NokiaSros1!` | gNMI password for all SR OS nodes |

The server auto-discovers SR OS nodes from `containerlab/nokia-evpn.clab.yml` (searched upward from the working directory). gNMI connects on port `57400` with username `admin`.

## Available Tools

### System
| Tool | Description |
|---|---|
| `sros_list_nodes` | List all discovered SR OS nodes and their hostnames |
| `sros_system_info` | Get system information (version, uptime, etc.) |
| `sros_system_alarms` | Get active system alarms |

### Interfaces
| Tool | Description |
|---|---|
| `sros_get_ports` | List physical ports and their operational state |
| `sros_get_interfaces` | List router interfaces |
| `sros_get_interface_state` | Get detailed state for a specific interface |
| `sros_set_interface_description` | Set an interface description (supports `dry_run`) |

### BGP
| Tool | Description |
|---|---|
| `sros_bgp_summary` | BGP summary statistics from the Base routing instance |
| `sros_bgp_neighbors` | All BGP neighbor states |
| `sros_bgp_neighbor` | Detailed state for a specific BGP neighbor |
| `sros_bgp_config` | BGP configuration from the Base routing instance |

### EVPN / VPLS
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

> **Note:** `nokia_srsim` requires a local Docker image (`nokia_srsim:25.10.R2`) and a valid license file. See the [containerlab documentation](https://containerlab.dev) and contact Nokia for the simulator image and license.

Once the lab is running, the MCP server can be started from the repository root and will automatically resolve node hostnames (e.g., `clab-evpn-dcgw1`).

## Project Structure

```
src/sros_mcp/
├── server.py          # FastMCP entrypoint — registers all context modules
├── client.py          # gNMI transport (gnmi_get / gnmi_set)
├── config.py          # Node discovery from containerlab topology YAML
├── contexts/
│   ├── system.py      # System tools
│   ├── interfaces.py  # Interface tools
│   ├── bgp.py         # BGP tools
│   └── evpn.py        # EVPN/VPLS tools
└── utils/
    └── formatters.py  # Output formatting helpers
```
