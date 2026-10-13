"""Inventory: netmcp.yml generation from `containerlab inspect` and discovery errors."""

import pytest

from netmcp import inventory
from netmcp.inventory import clab_inspect_to_yml


def _c(name, kind, group="", lab="mv"):
    # Shape of one entry in `containerlab inspect --all --format json` (containerlab 0.78).
    return {
        "lab_name": lab, "name": name, "kind": kind, "group": group,
        "state": "running", "ipv4_address": "172.20.20.13/24",
    }


INSPECT = {
    "mv": [
        _c("mv-ceos", "arista_ceos", "leaf"),
        _c("mv-client-1", "linux", "server"),
        _c("mv-ptx-gw", "juniper_cjunosevolved", "spine"),
        _c("mv-sros", "nokia_srsim"),
    ],
}


def test_clab_inspect_maps_kinds_and_skips_linux():
    nodes = clab_inspect_to_yml(INSPECT)["inventory"]["nodes"]
    assert nodes == [
        {"name": "ceos", "fqdn": "mv-ceos", "nos": "eos", "tags": ["leaf"]},
        {"name": "ptx-gw", "fqdn": "mv-ptx-gw", "nos": "junos", "tags": ["spine"]},
        {"name": "sros", "fqdn": "mv-sros", "nos": "sros"},
    ]


@pytest.mark.parametrize("container, node", [
    ("clab-evpn-leaf1", "leaf1"),  # default prefix
    ("evpn-leaf1", "leaf1"),       # prefix: __lab-name
    ("leaf1", "leaf1"),            # prefix: ""
    ("lab-evpn-evpn-gw", "evpn-gw"),  # custom prefix, lab name inside node name
])
def test_clab_short_name(container, node):
    assert inventory._clab_short_name(container, "evpn") == node


def test_clab_inspect_selects_lab():
    both = {**INSPECT, "evpn": [_c("clab-evpn-leaf1", "nokia_srlinux", lab="evpn")]}
    assert clab_inspect_to_yml(both, "evpn")["inventory"]["nodes"] == [
        {"name": "leaf1", "fqdn": "clab-evpn-leaf1", "nos": "srl"},
    ]
    with pytest.raises(ValueError, match="2 labs running"):
        clab_inspect_to_yml(both)
    with pytest.raises(ValueError, match="not running"):
        clab_inspect_to_yml(both, "nope")


def test_generated_inventory_loads(tmp_path):
    import yaml
    path = tmp_path / "netmcp.yml"
    path.write_text(yaml.safe_dump(clab_inspect_to_yml(INSPECT)))
    nodes = inventory._load_from_yml(path)
    assert nodes["ptx-gw"].gnmi_port == 32767
    assert nodes["ceos"].gnmi_port == 6030
    assert nodes["sros"].fqdn == "mv-sros"


def test_missing_clab_topology_env_is_an_error(monkeypatch, tmp_path):
    monkeypatch.setenv("NETMCP_CLAB_TOPOLOGY", str(tmp_path / "missing.clab.yml"))
    with pytest.raises(FileNotFoundError, match="NETMCP_CLAB_TOPOLOGY"):
        inventory._find_clab_file()


def test_cumulus_defaults(tmp_path):
    import yaml
    inspect = {"mv": [_c("mv-nvidia", "nvidia_cumulusvx", "leaf")]}
    doc = clab_inspect_to_yml(inspect)
    assert doc["inventory"]["nodes"] == [
        {"name": "nvidia", "fqdn": "mv-nvidia", "nos": "cumulus", "tags": ["leaf"]},
    ]
    doc["inventory"]["nodes"].append(
        {"name": "cvx2", "fqdn": "cvx2", "nos": "cumulus", "username": "netops"}
    )
    path = tmp_path / "netmcp.yml"
    path.write_text(yaml.safe_dump(doc))
    nodes = inventory._load_from_yml(path)
    assert (nodes["nvidia"].transport, nodes["nvidia"].username) == ("rest", "cumulus")
    assert nodes["cvx2"].username == "netops"


def test_cumulus_clab_topology(tmp_path):
    import yaml
    path = tmp_path / "mv.clab.yml"
    path.write_text(yaml.safe_dump({
        "name": "mv", "prefix": "__lab-name",
        "topology": {"nodes": {"nvidia": {"kind": "nvidia_cumulusvx"}}},
    }))
    node = inventory._load_from_clab(path)["nvidia"]
    assert (node.fqdn, node.nos_type, node.transport, node.username) == (
        "mv-nvidia", "cumulus", "rest", "cumulus",
    )
