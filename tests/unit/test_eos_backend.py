"""Unit tests for Arista EOS EVPN (nos/eos/backend.py).

gnmi_get / gnmi_set_batch are mocked with reply shapes captured from a live
cEOS 4.34.2F node.
"""

import json
import os

import pytest

os.environ.setdefault("NETMCP_NO_INVENTORY", "1")

from netmcp.inventory import NodeInfo
from netmcp.nos.eos import backend as eos_backend
from netmcp.nos.eos.backend import BGP_PATH, EVPN_PATH, VLANS_PATH, VXLAN_PATH, EOSBackend

NODE = NodeInfo(name="ceos", fqdn="mv-ceos", nos_type="eos", gnmi_port=6030)

VLANS = {
    "openconfig-network-instance:vlan": [
        {"vlan-id": 1, "config": {"vlan-id": 1, "name": "default"}},
        {
            "vlan-id": 10,
            "config": {"vlan-id": 10, "name": "mac-vrf-10"},
            "state": {"vlan-id": 10, "name": "mac-vrf-10"},
            "members": {"member": [
                {"state": {"interface": "Ethernet1"}},
                {"state": {"interface": "Vxlan1"}},
            ]},
        },
    ]
}

VXLAN = {
    "arista-exp-eos-vxlan:arista-vxlan": {
        "state": {"src-ip-intf": "Loopback0", "udp-port": 4789},
        "vlan-to-vnis": {"vlan-to-vni": [{"vlan": 10, "config": {"vlan": 10, "vni": 1010}, "state": {"vlan": 10, "vni": 1010}}]},
    }
}

EVPN = {
    "arista-exp-eos-evpn:evpn-instance": [
        {
            "name": "10",
            "config": {
                "name": "10",
                "redistribute": ["LEARNED", "ROUTER_MAC", "HOST_ROUTE"],
                "route-distinguisher": "10:2",
            },
            "route-target": {"config": {"export": ["65000:10"], "import": ["65000:10"]}},
            "vlans": {"vlan": [{"vlan-id": 10, "config": {"vlan-id": 10}}]},
        }
    ]
}

INTERFACES = {
    "openconfig-interfaces:interface": [
        {"name": "Ethernet1", "openconfig-if-ethernet:ethernet": {
            "openconfig-vlan:switched-vlan": {"config": {"interface-mode": "TRUNK"}}}},
        {"name": "Ethernet4", "openconfig-if-ethernet:ethernet": {
            "openconfig-vlan:switched-vlan": {"config": {"interface-mode": "TRUNK", "trunk-vlans": [10, 30]}}}},
        {"name": "Ethernet5", "openconfig-if-ethernet:ethernet": {
            "openconfig-vlan:switched-vlan": {"config": {"interface-mode": "TRUNK", "trunk-vlans": [10]}}}},
        {"name": "Ethernet2", "openconfig-if-ethernet:ethernet": {"config": {"mtu": 9194}}},
    ]
}


def _sv(iface: str) -> str:
    return f"/interfaces/interface[name={iface}]/ethernet/switched-vlan/config"


@pytest.fixture
def fake_gnmi(monkeypatch):
    replies = {
        VLANS_PATH: VLANS,
        VXLAN_PATH: VXLAN,
        EVPN_PATH: EVPN,
        "/interfaces": INTERFACES,
        _sv("Ethernet1"): {"openconfig-vlan:interface-mode": "TRUNK"},
        _sv("Ethernet4"): {"openconfig-vlan:interface-mode": "TRUNK", "openconfig-vlan:trunk-vlans": [30]},
    }
    sets = []
    monkeypatch.setattr(eos_backend, "gnmi_get", lambda node, path, datatype="all": replies.get(path))
    monkeypatch.setattr(
        eos_backend, "gnmi_set_batch",
        lambda node, updates=None, deletes=None: sets.append({"updates": updates or [], "deletes": deletes or []}) or {},
    )
    return replies, sets


def _provision(**overrides):
    args = dict(
        service_name="mac-vrf-20", service_id=20, vni=1020, evi=20,
        route_distinguisher="20:2", export_rt="target:65000:20", import_rt="target:65000:20",
        dry_run=False, interface_name="Ethernet1", vlan_id=20,
    )
    args.update(overrides)
    return EOSBackend().provision_evpn_instance(NODE, **args)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def test_get_evpn_instances(fake_gnmi):
    result = json.loads(EOSBackend().get_evpn_instances(NODE))
    assert result == {"ceos": [{"name": "mac-vrf-10", "type": "vlan", "vni": 1010, "evi": 10}]}


def test_get_evpn_instances_none(fake_gnmi):
    del fake_gnmi[0][EVPN_PATH]
    assert "No EVPN instances found" in EOSBackend().get_evpn_instances(NODE)


@pytest.mark.parametrize("name", ["mac-vrf-10", "10"])
def test_get_evpn_instance_by_vlan_name_or_id(fake_gnmi, name):
    result = json.loads(EOSBackend().get_evpn_instance(NODE, name))["ceos"]
    assert result["vlan-id"] == 10
    assert result["vni"] == 1010
    assert result["evpn-instance"]["config"]["route-distinguisher"] == "10:2"
    assert [t["name"] for t in result["trunk-interfaces"]] == ["Ethernet4", "Ethernet5"]


def test_get_evpn_instance_not_found(fake_gnmi):
    assert "not found" in EOSBackend().get_evpn_instance(NODE, "nope")


def test_get_evpn_instance_state(fake_gnmi):
    result = json.loads(EOSBackend().get_evpn_instance_state(NODE, "mac-vrf-10"))["ceos"]
    assert result["vxlan-state"]["src-ip-intf"] == "Loopback0"
    assert result["vlan-members"]["member"][0]["state"]["interface"] == "Ethernet1"


# ---------------------------------------------------------------------------
# Provision
# ---------------------------------------------------------------------------

def test_provision_requires_interface_and_vlan(fake_gnmi):
    assert "requires 'interface_name'" in _provision(interface_name="")
    assert "requires 'interface_name'" in _provision(vlan_id=0)


def test_provision_rejects_non_ethernet(fake_gnmi):
    assert "Validation error" in _provision(interface_name="Management0")


def test_provision_rejects_routed_port(fake_gnmi):
    assert "not a trunk switchport" in _provision(interface_name="Ethernet2")


def test_provision_rejects_existing_vlan(fake_gnmi):
    assert "already exists" in _provision(vlan_id=10)


def test_provision_all_vlans_trunk_skips_trunk_update(fake_gnmi):
    _, sets = fake_gnmi
    assert _provision().startswith("OK:")
    paths = [p for p, _ in sets[0]["updates"]]
    assert paths == [
        f"{VLANS_PATH}/vlan[vlan-id=20]",
        f"{VXLAN_PATH}/vlan-to-vnis/vlan-to-vni[vlan=20]",
        f"{EVPN_PATH}/evpn-instance[name=20]",
    ]
    assert sets[0]["updates"][1][1] == {"vlan": 20, "config": {"vlan": 20, "vni": 1020}}
    evpn_value = sets[0]["updates"][2][1]
    assert evpn_value["route-target"]["config"] == {"import": ["65000:20"], "export": ["65000:20"]}


def test_provision_explicit_trunk_appends_vlan(fake_gnmi):
    _, sets = fake_gnmi
    assert _provision(interface_name="Ethernet4").startswith("OK:")
    assert sets[0]["updates"][-1] == (_sv("Ethernet4"), {"trunk-vlans": [30, 20]})


def test_provision_dry_run_sends_nothing(fake_gnmi):
    _, sets = fake_gnmi
    out = _provision(dry_run=True)
    assert out.count("[DRY RUN]") == 3
    assert sets == []


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def test_delete_batch(fake_gnmi):
    _, sets = fake_gnmi
    out = EOSBackend().delete_evpn_instance(NODE, "mac-vrf-10", dry_run=False)
    assert out.startswith("OK:")
    assert "Ethernet5" in out  # only VLAN in its list, left alone
    assert sets[0]["deletes"] == [
        f"{EVPN_PATH}/evpn-instance[name=10]",
        f"{VXLAN_PATH}/vlan-to-vnis/vlan-to-vni[vlan=10]",
        f"{VLANS_PATH}/vlan[vlan-id=10]",
        f"{_sv('Ethernet4')}/trunk-vlans",
    ]
    assert sets[0]["updates"] == [(_sv("Ethernet4"), {"trunk-vlans": [30]})]


def test_delete_not_found(fake_gnmi):
    assert "not found" in EOSBackend().delete_evpn_instance(NODE, "nope", dry_run=False)


# ---------------------------------------------------------------------------
# BGP (reply shapes captured from mv-ceos)
# ---------------------------------------------------------------------------

def _afi(name: str, active: bool, received: int = 0, sent: int = 0) -> dict:
    return {
        "afi-safi-name": name,
        "config": {"afi-safi-name": name},
        "state": {
            "active": active,
            "afi-safi-name": name,
            "prefixes": {"installed": received, "received": received, "sent": sent},
        },
    }


def _bgp_neighbor(addr: str, received: int) -> dict:
    return {
        "neighbor-address": addr,
        "config": {"neighbor-address": addr, "peer-group": "iBGP-evpn"},
        "state": {
            "neighbor-address": addr,
            "peer-as": 65000,
            "peer-group": "iBGP-evpn",
            "session-state": "ESTABLISHED",
            "established-transitions": "1",
            "last-established": "1790102035963950634",
        },
        "afi-safis": {"afi-safi": [
            _afi("openconfig-bgp-types:IPV4_UNICAST", False),
            _afi("openconfig-bgp-types:L2VPN_EVPN", True, received=received, sent=1),
        ]},
    }


BGP_GLOBAL = {"openconfig-network-instance:global": {
    "config": {"as": 65000, "router-id": "192.1.1.2"},
    "state": {"as": 65000, "router-id": "192.1.1.2"},
}}

BGP_NEIGHBORS = {"openconfig-network-instance:neighbor": [
    _bgp_neighbor("192.1.2.1", received=4),
    _bgp_neighbor("192.1.2.2", received=0),
]}

BGP_CONFIG = {
    "openconfig-network-instance:global": {"config": {"as": 65000, "router-id": "192.1.1.2"}},
    "openconfig-network-instance:peer-groups": {"peer-group": [{
        "peer-group-name": "iBGP-evpn",
        "config": {"peer-as": 65000, "peer-group-name": "iBGP-evpn", "send-community-type": ["EXTENDED"]},
        "transport": {"config": {"local-address": "Loopback0"}},
    }]},
}


@pytest.fixture
def fake_bgp(monkeypatch):
    replies = {
        f"{BGP_PATH}/global": BGP_GLOBAL,
        f"{BGP_PATH}/neighbors": BGP_NEIGHBORS,
        f"{BGP_PATH}/neighbors/neighbor[neighbor-address=192.1.2.1]": _bgp_neighbor("192.1.2.1", 4),
        BGP_PATH: BGP_CONFIG,
    }
    calls = []

    def fake_get(node, path, datatype="all"):
        calls.append((path, datatype))
        return replies.get(path)

    monkeypatch.setattr(eos_backend, "gnmi_get", fake_get)
    return replies, calls


def test_bgp_summary(fake_bgp):
    result = json.loads(EOSBackend().get_bgp_summary(NODE))["ceos"]
    assert result["as"] == 65000
    assert result["router-id"] == "192.1.1.2"
    assert result["peers"] == {"total": 2, "established": 2}
    assert "total-paths" not in result  # EOS does not report it
    assert result["neighbors"][0] == {
        "peer": "192.1.2.1", "peer-as": 65000, "state": "ESTABLISHED",
        "afi-safis": {"L2VPN_EVPN": {"received": 4, "sent": 1, "installed": 4}},
    }


def test_bgp_summary_counts_non_established(fake_bgp):
    fake_bgp[0][f"{BGP_PATH}/neighbors"]["openconfig-network-instance:neighbor"][1]["state"]["session-state"] = "ACTIVE"
    result = json.loads(EOSBackend().get_bgp_summary(NODE))["ceos"]
    assert result["peers"] == {"total": 2, "established": 1}


def test_bgp_summary_error_without_global(fake_bgp):
    del fake_bgp[0][f"{BGP_PATH}/global"]
    assert EOSBackend().get_bgp_summary(NODE).startswith("Error: could not retrieve BGP summary")


def test_bgp_neighbors_compact_view(fake_bgp):
    peers = json.loads(EOSBackend().get_bgp_neighbors(NODE))["ceos"]
    assert [p["peer"] for p in peers] == ["192.1.2.1", "192.1.2.2"]
    assert peers[0]["peer-group"] == "iBGP-evpn"
    assert list(peers[0]["afi-safis"]) == ["L2VPN_EVPN"]  # inactive IPV4_UNICAST dropped, prefix stripped


def test_bgp_neighbors_none(fake_bgp):
    del fake_bgp[0][f"{BGP_PATH}/neighbors"]
    assert "No BGP neighbors found" in EOSBackend().get_bgp_neighbors(NODE)


def test_bgp_neighbor_raw_and_not_found(fake_bgp):
    raw = json.loads(EOSBackend().get_bgp_neighbor(NODE, "192.1.2.1"))["ceos"]
    assert raw["state"]["session-state"] == "ESTABLISHED"
    assert EOSBackend().get_bgp_neighbor(NODE, "10.9.9.9").startswith("Error: could not retrieve BGP neighbor")


def test_bgp_config_requests_config_only(fake_bgp):
    _, calls = fake_bgp
    result = json.loads(EOSBackend().get_bgp_config(NODE))["ceos"]
    assert calls == [(BGP_PATH, "config")]
    assert "openconfig-network-instance:peer-groups" in result
