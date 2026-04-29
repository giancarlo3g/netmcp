"""Node discovery and inventory for netmcp.

Supports two discovery modes (in priority order):
  1. Static YAML inventory file (netmcp.yml), searched upward from cwd.
  2. Containerlab topology YAML, via NETMCP_CLAB_TOPOLOGY env var or by
     scanning containerlab/*.clab.yml upward from cwd.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# --- Default connection constants ---
GNMI_PORT = 57400
GNMI_USER = "admin"

# Containerlab kind → netmcp nos_type
CLAB_KIND_TO_NOS: dict[str, str] = {
    "nokia_srsim": "sros",
    "nokia_srlinux": "srl",
    "arista_ceos": "eos",
    "juniper_vjunosrouter": "junos",
    "cisco_xrd": "iosxr",
}

# Per-NOS transport defaults
_NOS_TRANSPORT_DEFAULTS: dict[str, str] = {
    "sros": "gnmi",
    "srl": "gnmi",
    "eos": "gnmi",
    "junos": "netconf",
    "iosxr": "netconf",
}


@dataclass
class NodeInfo:
    """All connection details for a single managed node."""

    name: str           # short name used in tool calls, e.g. "dcgw1"
    fqdn: str           # hostname or IP the server connects to
    nos_type: str       # "sros" | "srl" | "eos" | "junos" | "iosxr"
    transport: str = "gnmi"
    gnmi_port: int = GNMI_PORT
    netconf_port: int = 830
    username: str = GNMI_USER
    tags: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Credential resolution
# ---------------------------------------------------------------------------

def resolve_password(node: NodeInfo) -> str:
    """Resolve the management password for a node.

    Priority:
      1. NETMCP_{NAME_UPPER}_PASSWORD   per-node env var
      2. NETMCP_DEFAULT_PASSWORD        global env var
      3. SROS_PASSWORD                  legacy alias (sros nodes only)
      4. NOS-specific hardcoded default
    """
    per_node = os.environ.get(f"NETMCP_{node.name.upper()}_PASSWORD")
    if per_node:
        return per_node

    default = os.environ.get("NETMCP_DEFAULT_PASSWORD")
    if default:
        return default

    if node.nos_type == "sros":
        legacy = os.environ.get("SROS_PASSWORD")
        if legacy:
            return legacy

    nos_defaults = {
        "sros": "NokiaSros1!",
        "srl": "NokiaSrl1!",
        "eos": "admin",
        "junos": "admin",
        "iosxr": "admin",
    }
    return nos_defaults.get(node.nos_type, "admin")


# ---------------------------------------------------------------------------
# Static YAML inventory loader
# ---------------------------------------------------------------------------

def _find_netmcp_yml() -> Path | None:
    """Search upward from cwd for netmcp.yml."""
    for parent in [Path.cwd()] + list(Path.cwd().parents):
        candidate = parent / "netmcp.yml"
        if candidate.exists():
            return candidate
    return None


def _load_from_yml(path: Path) -> dict[str, NodeInfo] | None:
    """Parse a netmcp.yml inventory file into {name: NodeInfo}.

    Returns None if the file is empty or contains only comments, so the
    caller can fall through to the next discovery source.
    """
    with open(path) as f:
        data = yaml.safe_load(f)

    if not data:
        return None

    nodes: dict[str, NodeInfo] = {}
    for entry in data.get("inventory", {}).get("nodes") or []:
        nos_type = entry["nos"]
        transport = entry.get("transport", _NOS_TRANSPORT_DEFAULTS.get(nos_type, "gnmi"))
        node = NodeInfo(
            name=entry["name"],
            fqdn=entry["fqdn"],
            nos_type=nos_type,
            transport=transport,
            gnmi_port=int(entry.get("gnmi_port", GNMI_PORT)),
            netconf_port=int(entry.get("netconf_port", 830)),
            username=entry.get("username", GNMI_USER),
            tags=list(entry.get("tags", [])),
        )
        nodes[node.name] = node
    return nodes


# ---------------------------------------------------------------------------
# Containerlab auto-discovery
# ---------------------------------------------------------------------------

def _find_clab_file() -> Path | None:
    """Return the containerlab topology file to use.

    Checks (in order):
      1. NETMCP_CLAB_TOPOLOGY env var (explicit path)
      2. First *.clab.yml found in a containerlab/ directory, searching upward
    """
    explicit = os.environ.get("NETMCP_CLAB_TOPOLOGY")
    if explicit:
        p = Path(explicit)
        if p.exists():
            return p
        print(f"netmcp: WARNING — NETMCP_CLAB_TOPOLOGY={explicit!r} does not exist, ignoring.")

    for parent in [Path.cwd()] + list(Path.cwd().parents):
        clab_dir = parent / "containerlab"
        if clab_dir.is_dir():
            matches = sorted(clab_dir.glob("*.clab.yml"))
            if matches:
                return matches[0]
    return None


def _load_from_clab(path: Path) -> dict[str, NodeInfo]:
    """Parse a containerlab topology YAML into {name: NodeInfo}."""
    with open(path) as f:
        topo = yaml.safe_load(f)

    topo_name = topo["name"]
    nodes: dict[str, NodeInfo] = {}

    for node_name, node_cfg in topo.get("topology", {}).get("nodes", {}).items():
        kind = node_cfg.get("kind", "")
        nos_type = CLAB_KIND_TO_NOS.get(kind)
        if nos_type is None:
            continue  # skip linux clients and other non-router kinds
        fqdn = f"clab-{topo_name}-{node_name}"
        transport = _NOS_TRANSPORT_DEFAULTS.get(nos_type, "gnmi")
        nodes[node_name] = NodeInfo(
            name=node_name,
            fqdn=fqdn,
            nos_type=nos_type,
            transport=transport,
        )
    return nodes


# ---------------------------------------------------------------------------
# Public loader
# ---------------------------------------------------------------------------

def load_nodes() -> dict[str, NodeInfo]:
    """Load nodes from the best available inventory source.

    Returns an empty dict (with a warning) rather than raising, so a
    NETMCP_NO_INVENTORY=1 environment variable can suppress errors in CI.
    """
    if os.environ.get("NETMCP_NO_INVENTORY"):
        return {}

    yml = _find_netmcp_yml()
    if yml:
        nodes = _load_from_yml(yml)
        if nodes is not None:
            _log_startup(nodes, source=str(yml))
            return nodes

    clab = _find_clab_file()
    if clab:
        nodes = _load_from_clab(clab)
        _log_startup(nodes, source=str(clab))
        return nodes

    raise FileNotFoundError(
        "netmcp: no inventory found. Create a netmcp.yml file or run from within "
        "a directory that contains a containerlab/*.clab.yml topology file."
    )


def _log_startup(nodes: dict[str, NodeInfo], source: str) -> None:
    width = max((len(n) for n in nodes), default=4)
    print(f"netmcp: loaded {len(nodes)} node(s) from {source}", flush=True)
    for name, info in nodes.items():
        print(f"  {name:<{width}}  ({info.nos_type})  {info.fqdn}", flush=True)


# Module-level export — resolved once at import time.
NODES: dict[str, NodeInfo] = load_nodes()
