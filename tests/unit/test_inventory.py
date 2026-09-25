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
