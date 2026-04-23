"""Topology discovery and configuration for SR OS nodes."""

import os
from pathlib import Path

import yaml

GNMI_PORT = 57400
GNMI_USER = "admin"
# Topology YAML
_TOPOLOGY_FILE = Path("containerlab") / "nokia-evpn.clab.yml"


def _find_topology_file() -> Path:
    """Search upward from cwd for containerlab/nokia-evpn.clab.yml."""
    for parent in [Path.cwd()] + list(Path.cwd().parents):
        candidate = parent / _TOPOLOGY_FILE
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "Could not find containerlab/nokia-evpn.clab.yml. "
        "Run from within the sros-mcp repository."
    )


def _load_sros_nodes() -> dict[str, str]:
    """Parse the containerlab YAML and return {name: fqdn} for nokia_srsim nodes."""
    topo_file = _find_topology_file()
    with open(topo_file) as f:
        topo = yaml.safe_load(f)
    topo_name = topo["name"]
    nodes_dict = {}
    for node_name, node_cfg in topo.get("topology", {}).get("nodes", {}).items():
        if node_cfg.get("kind") != "nokia_srsim":
            continue
        fqdn = f"clab-{topo_name}-{node_name}"
        nodes_dict[node_name] = fqdn
    return nodes_dict


def get_password() -> str:
    """Return the SR OS gNMI password from env or default."""
    return os.environ.get("SROS_PASSWORD", "NokiaSros1!")


NODES: dict[str, str] = _load_sros_nodes()
# e.g. {"dcgw1": "clab-evpn-dcgw1", "dcgw2": "clab-evpn-dcgw2"}
