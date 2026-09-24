"""Unit tests for Juniper Junos BGP and EVPN (nos/junos/backend.py and the Subscribe decoding in client.py).

gnmi_subscribe_once / gnmi_get_config / gnmi_set_batch are mocked with reply
shapes captured from a live cJunos Evolved 25.4R1 node (mv-ptx).
"""

import json
import os

import pytest
from pygnmi.create_gnmi_path import gnmi_path_generator
from pygnmi.spec.v080.gnmi_pb2 import (
    Notification,
    ScalarArray,
    SubscribeResponse,
    TypedValue,
    Update,
)

os.environ.setdefault("NETMCP_NO_INVENTORY", "1")

from netmcp.inventory import NodeInfo
from netmcp.nos.junos import backend as junos_backend
from netmcp.nos.junos import client as junos_client
from netmcp.nos.junos.backend import (
    BGP_CONFIG_PATH,
    BGP_PATH,
    INTERFACES_PATH,
    ROUTING_INSTANCES_PATH,
    ROUTING_OPTIONS_PATH,
    JunOSBackend,
)

NODE = NodeInfo(name="ptx", fqdn="mv-ptx", nos_type="junos")


# ---------------------------------------------------------------------------
# BGP (reply shapes captured from mv-ptx)
# ---------------------------------------------------------------------------

def _bgp_neighbor(addr: str, received: int, session_state: str = "ESTABLISHED") -> dict:
    return {
        "neighbor-address": addr,
        "state": {
            "peer-as": 65000,
            "local-as": 65000,
            "peer-type": "INTERNAL",
            "session-state": session_state,
            "last-established": 1790266197000000000,
            "established-transitions": 1,
            "supported-capabilities": ["MPBGP", "ROUTE_REFRESH", "ASN32", "GRACEFUL_RESTART"],
            "peer-group": "iBGP-evpn",
            "neighbor-address": addr,
            "enabled": True,
        },
        "afi-safis": {"afi-safi": [{
            "afi-safi-name": "L2VPN_EVPN",
            "state": {
                "afi-safi-name": "L2VPN_EVPN",
                "enabled": True,
                "active": True,
                "prefixes": {"received": received, "received-pre-policy": received, "sent": 1,
                             "installed": received, "accepted": received, "rejected": 0},
            },
        }]},
        "timers": {"state": {"hold-time": 90, "keepalive-interval": 30, "negotiated-hold-time": 90}},
        "transport": {"state": {"local-address": "192.1.1.3", "remote-address": addr, "remote-port": 179}},
    }


BGP_GLOBAL = {
    "state": {"as": 65000, "router-id": "192.1.1.3", "total-paths": 8, "total-prefixes": 8},
    "graceful-restart": {"state": {"enabled": False, "helper-only": True}},
}

BGP_NEIGHBORS = {"neighbor": [
    _bgp_neighbor("192.1.2.1", received=4),
    _bgp_neighbor("192.1.2.2", received=0),
]}

CONFIG = {
    "configuration/protocols/bgp": {"group": [{
        "name": "iBGP-evpn",
        "type": "internal",
        "local-address": "192.1.1.3",
        "family": {"evpn": {"signaling": {}}},
        "neighbor": [{"name": "192.1.2.1"}, {"name": "192.1.2.2"}],
    }]},
    "configuration/routing-options": {"router-id": "192.1.1.3", "autonomous-system": {"as-number": "65000"}},
}


@pytest.fixture
def fake_gnmi(monkeypatch):
    state = {
        f"{BGP_PATH}/global": BGP_GLOBAL,
        f"{BGP_PATH}/neighbors": BGP_NEIGHBORS,
        f"{BGP_PATH}/neighbors/neighbor[neighbor-address=192.1.2.1]": _bgp_neighbor("192.1.2.1", 4),
    }
    config = dict(CONFIG)
    calls = []

    def fake_subscribe(node, path):
        calls.append(("subscribe", path))
        return state.get(path)

    def fake_get_config(node, paths):
        calls.append(("get", tuple(paths)))
        return config

    monkeypatch.setattr(junos_backend, "gnmi_subscribe_once", fake_subscribe)
    monkeypatch.setattr(junos_backend, "gnmi_get_config", fake_get_config)
    return state, config, calls


def test_bgp_summary(fake_gnmi):
    result = json.loads(JunOSBackend().get_bgp_summary(NODE))["ptx"]
    assert result["as"] == 65000
    assert result["router-id"] == "192.1.1.3"
    assert result["total-paths"] == 8  # Junos reports the totals EOS omits
    assert result["peers"] == {"total": 2, "established": 2}
    assert result["neighbors"][0] == {
        "peer": "192.1.2.1", "peer-as": 65000, "state": "ESTABLISHED",
        "afi-safis": {"L2VPN_EVPN": {"received": 4, "sent": 1, "installed": 4}},
    }


def test_bgp_summary_counts_non_established(fake_gnmi):
    fake_gnmi[0][f"{BGP_PATH}/neighbors"] = {"neighbor": [
        _bgp_neighbor("192.1.2.1", 4),
        _bgp_neighbor("192.1.2.2", 0, session_state="ACTIVE"),
    ]}
    result = json.loads(JunOSBackend().get_bgp_summary(NODE))["ptx"]
    assert result["peers"] == {"total": 2, "established": 1}


def test_bgp_summary_error_without_global(fake_gnmi):
    del fake_gnmi[0][f"{BGP_PATH}/global"]
    assert JunOSBackend().get_bgp_summary(NODE).startswith("Error: could not retrieve BGP summary")


def test_bgp_neighbors_compact_view(fake_gnmi):
    peers = json.loads(JunOSBackend().get_bgp_neighbors(NODE))["ptx"]
    assert [p["peer"] for p in peers] == ["192.1.2.1", "192.1.2.2"]
    assert peers[0]["peer-group"] == "iBGP-evpn"
    assert peers[0]["afi-safis"] == {"L2VPN_EVPN": {"received": 4, "sent": 1, "installed": 4}}


def test_bgp_neighbors_none(fake_gnmi):
    del fake_gnmi[0][f"{BGP_PATH}/neighbors"]
    assert "No BGP neighbors found" in JunOSBackend().get_bgp_neighbors(NODE)


def test_bgp_neighbor_raw_and_not_found(fake_gnmi):
    raw = json.loads(JunOSBackend().get_bgp_neighbor(NODE, "192.1.2.1"))["ptx"]
    assert raw["state"]["session-state"] == "ESTABLISHED"
    assert raw["timers"]["state"]["negotiated-hold-time"] == 90
    assert JunOSBackend().get_bgp_neighbor(NODE, "10.9.9.9").startswith("Error: could not retrieve BGP neighbor")


def test_bgp_config_reads_native_config(fake_gnmi):
    _, _, calls = fake_gnmi
    result = json.loads(JunOSBackend().get_bgp_config(NODE))["ptx"]
    assert calls == [("get", (BGP_CONFIG_PATH, ROUTING_OPTIONS_PATH))]
    assert BGP_CONFIG_PATH.startswith("juniper:/")  # native YANG origin, not cli:
    assert result["routing-options"]["autonomous-system"]["as-number"] == "65000"
    assert result["protocols"]["bgp"]["group"][0]["neighbor"] == [{"name": "192.1.2.1"}, {"name": "192.1.2.2"}]


def test_bgp_config_error_when_missing(fake_gnmi):
    fake_gnmi[1].clear()
    assert JunOSBackend().get_bgp_config(NODE).startswith("Error: could not retrieve BGP config")


# ---------------------------------------------------------------------------
# EVPN (reply shapes captured from mv-ptx; mv-ptx-gw has no routing-instances)
# ---------------------------------------------------------------------------

EVPN_VXLAN10 = {
    "name": "EVPN-VXLAN10",
    "instance-type": "mac-vrf",
    "protocols": {"evpn": {"encapsulation": "vxlan", "extended-vni-list": ["1010"]}},
    "vtep-source-interface": {"interface-name": "lo0.0"},
    "service-type": "vlan-based",
    "interface": [{"name": "et-0/0/2.10"}],
    "route-distinguisher": {"rd-type": "10:3"},
    "vrf-target": {"community": "target:65000:10"},
    "vlans": {"vlan": [{
        "name": "vlan10",
        "vlan-id": 10,
        "interface": [{"name": "et-0/0/2.10"}],
        "vxlan": {"vni": 1010, "ingress-node-replication": [None]},
    }]},
}

INTERFACES = {"interface": [
    {"name": "et-0/0/0", "vlan-tagging": [None], "mtu": 9216, "unit": [{
        "name": 1, "vlan-id": 1,
        "family": {"inet": {"mtu": 9194, "address": [{"name": "101.1.3.0/31"}]}},
    }]},
    {"name": "et-0/0/2", "flexible-vlan-tagging": [None], "encapsulation": "flexible-ethernet-services",
     "unit": [{"name": 10, "encapsulation": "vlan-bridge", "vlan-id": 10}]},
    {"name": "lo0", "unit": [{"name": 0, "family": {"inet": {"address": [{"name": "192.1.1.3/32"}]}}}]},
]}

NI = "/network-instances/network-instance[name=EVPN-VXLAN10]"
# Subscribe on the instance root; the BGP loc-rib and fdb subtrees are left out here
EVPN_STATE = {NI: {
    "name": "EVPN-VXLAN10",
    "state": {"name": "EVPN-VXLAN10", "type": "evpn", "description": "",
              "router-id": "zero-len", "route-distinguisher": "10:3"},
    "vlan": [{"vlan-name": "vlan10+10", "vlan-id": 10, "status": "ACTIVE", "vni": 1010,
              "l3-interface": "", "num-local-mac-entries": 2, "ethernet-tag-id": 1010,
              "member": {"interface": "vtep-54.32771"}}],
    "jnx-evpn": {
        "vxlan-tunnel-end-point": [
            {"remote-ip-address": "192.1.1.1", "source-ip-address": "192.1.1.3", "status": "IF-UP",
             "mode": "RNVE", "nexthop-index": 10020, "source-interface": "lo0.0", "vnids": [{"vnid": 1010}]},
            {"remote-ip-address": "192.1.1.2", "source-ip-address": "192.1.1.3", "status": "IF-UP",
             "mode": "RNVE", "nexthop-index": 10021, "source-interface": "lo0.0", "vnids": [{"vnid": 1010}]},
        ],
        "num-peers": 2,
        "num-remote-macs": 0,
        "interfaces": [
            {"name": ".local..54", "esi": "00:00:00:00:00:00:00:00:00:00", "mode": "single-homed", "status": "Up"},
            {"name": "et-0/0/2.10", "esi": "00:00:00:00:00:00:00:00:00:00", "mode": "single-homed", "status": "Up"},
        ],
        "peer": [{"peer-address": "192.1.1.1", "num-multicast-routes": 1, "num-mac-routes": 0}],
    },
    "mac-table-info": {"learning": True, "aging-time": 300, "table-size": 1007616, "num-local-entries": 2},
    "inter-instance-policies": {"import-export-policy": {"state": {
        "export-route-target": ["route-target:65000:10 "],
        "import-route-target": ["route-target:65000:10 "],
    }}},
}}


@pytest.fixture
def fake_evpn(monkeypatch):
    config = {
        "configuration/routing-instances": {"instance": [EVPN_VXLAN10]},
        "configuration/interfaces": INTERFACES,
    }
    sets = []

    def fake_get_config(node, paths):
        # Junos fails the whole Get with NOT_FOUND (reported as {}) if a path has no config
        keys = [p.removeprefix("juniper:/") for p in paths]
        if not all(k in config for k in keys):
            return {}
        return {k: config[k] for k in keys}

    def fake_subscribe(node, path):
        return EVPN_STATE.get(path)

    def fake_set(node, updates=None, deletes=None):
        sets.append({"updates": updates or [], "deletes": deletes or []})
        return {}

    monkeypatch.setattr(junos_backend, "gnmi_get_config", fake_get_config)
    monkeypatch.setattr(junos_backend, "gnmi_subscribe_once", fake_subscribe)
    monkeypatch.setattr(junos_backend, "gnmi_set_batch", fake_set)
    return config, sets


PROVISION = dict(
    service_name="EVPN-VXLAN20", service_id=0, vni=1020, evi=0,
    route_distinguisher="10:20", export_rt="target:65000:20", import_rt="65000:20",
    dry_run=False, interface_name="et-0/0/2", vlan_id=20,
)


def test_evpn_instances_normalised(fake_evpn):
    result = json.loads(JunOSBackend().get_evpn_instances(NODE))["ptx"]
    assert result == [{"name": "EVPN-VXLAN10", "type": "mac-vrf", "vni": 1010, "evi": 10}]


def test_evpn_instances_skips_non_mac_vrf_and_empty_spine(fake_evpn):
    config, _ = fake_evpn
    config["configuration/routing-instances"]["instance"].append({"name": "VRF1", "instance-type": "vrf"})
    assert [i["name"] for i in json.loads(JunOSBackend().get_evpn_instances(NODE))["ptx"]] == ["EVPN-VXLAN10"]
    del config["configuration/routing-instances"]  # mv-ptx-gw: Get returns NOT_FOUND
    assert JunOSBackend().get_evpn_instances(NODE).startswith("No EVPN instances found on ptx")


@pytest.mark.parametrize("name", ["EVPN-VXLAN10", "vlan10", "10"])
def test_evpn_instance_lookup_by_name_vlan_name_or_id(fake_evpn, name):
    result = json.loads(JunOSBackend().get_evpn_instance(NODE, name))["ptx"]
    assert result["name"] == "EVPN-VXLAN10"
    assert (result["vlan-id"], result["vni"]) == (10, 1010)
    assert result["routing-instance"]["vrf-target"] == {"community": "target:65000:10"}
    assert result["interface-units"] == [
        {"name": "et-0/0/2.10", "config": {"name": 10, "encapsulation": "vlan-bridge", "vlan-id": 10}},
    ]


def test_evpn_instance_not_found(fake_evpn):
    assert "not found" in JunOSBackend().get_evpn_instance(NODE, "EVPN-NOPE")
    assert "not found" in JunOSBackend().get_evpn_instance_state(NODE, "EVPN-NOPE")


def test_evpn_instance_state(fake_evpn):
    result = json.loads(JunOSBackend().get_evpn_instance_state(NODE, "10"))["ptx"]
    assert result["route-distinguisher"] == "10:3"
    assert result["route-targets"] == {"import": ["target:65000:10"], "export": ["target:65000:10"]}
    assert result["vlans"] == [{"vlan-id": 10, "vni": 1010, "status": "ACTIVE", "local-macs": 2}]
    assert result["vteps"] == [
        {"remote": "192.1.1.1", "source": "192.1.1.3", "status": "IF-UP", "vnis": [1010]},
        {"remote": "192.1.1.2", "source": "192.1.1.3", "status": "IF-UP", "vnis": [1010]},
    ]
    assert result["interfaces"] == [
        {"name": "et-0/0/2.10", "status": "Up", "mode": "single-homed", "esi": "00:00:00:00:00:00:00:00:00:00"},
    ]
    assert result["local-macs"] == 2


def test_provision_writes_unit_and_mac_vrf_in_one_set(fake_evpn):
    _, sets = fake_evpn
    out = JunOSBackend().provision_evpn_instance(NODE, **PROVISION)
    assert out.startswith("OK: EVPN instance 'EVPN-VXLAN20'"), out
    assert len(sets) == 1 and sets[0]["deletes"] == []
    (unit_path, unit), (ri_path, ri) = sets[0]["updates"]
    assert unit_path == f"{INTERFACES_PATH}/interface[name=et-0/0/2]/unit[name=20]"
    assert unit == {"name": 20, "encapsulation": "vlan-bridge", "vlan-id": 20}
    assert ri_path == f"{ROUTING_INSTANCES_PATH}/instance[name=EVPN-VXLAN20]"
    assert ri_path.startswith("juniper:/")  # native YANG origin, not cli:
    assert ri["instance-type"] == "mac-vrf" and ri["service-type"] == "vlan-based"
    assert ri["vtep-source-interface"] == {"interface-name": "lo0.0"}
    assert ri["vrf-target"] == {"community": "target:65000:20"}  # bare import_rt normalised
    assert ri["protocols"]["evpn"] == {"encapsulation": "vxlan", "extended-vni-list": ["1020"]}
    assert ri["interface"] == [{"name": "et-0/0/2.20"}]
    assert ri["vlans"]["vlan"] == [{
        "name": "vlan20", "vlan-id": 20, "interface": [{"name": "et-0/0/2.20"}],
        "vxlan": {"vni": 1020, "ingress-node-replication": [None]},
    }]


def test_provision_explicit_unit(fake_evpn):
    _, sets = fake_evpn
    JunOSBackend().provision_evpn_instance(NODE, **{**PROVISION, "interface_name": "et-0/0/2.200"})
    (unit_path, unit), (_, ri) = sets[0]["updates"]
    assert unit_path.endswith("unit[name=200]")
    assert unit == {"name": 200, "encapsulation": "vlan-bridge", "vlan-id": 20}
    assert ri["interface"] == [{"name": "et-0/0/2.200"}]


def test_provision_dry_run_sends_nothing(fake_evpn):
    _, sets = fake_evpn
    out = JunOSBackend().provision_evpn_instance(NODE, **{**PROVISION, "dry_run": True})
    assert out.count("[DRY RUN] Node: ptx") == 2
    assert "instance[name=EVPN-VXLAN20]" in out
    assert sets == []


@pytest.mark.parametrize("override, error", [
    ({"interface_name": "", "vlan_id": 0}, "requires 'interface_name'"),
    ({"export_rt": "target:65000:21"}, "must be equal"),
    ({"interface_name": "lo0"}, "not a valid access port"),
    ({"vni": 0}, "vni"),
    ({"export_rt": "bogus", "import_rt": "bogus"}, "not a valid route-target"),
    ({"service_name": "EVPN-VXLAN10"}, "already exists"),
    ({"vni": 1010}, "VNI 1010 is already used by 'EVPN-VXLAN10'"),
    ({"interface_name": "et-0/0/0"}, "cannot carry bridged units"),
    ({"interface_name": "et-0/0/9"}, "et-0/0/9 is not configured"),
    ({"interface_name": "et-0/0/2.10"}, "unit et-0/0/2.10 already exists"),
])
def test_provision_rejects(fake_evpn, override, error):
    _, sets = fake_evpn
    out = JunOSBackend().provision_evpn_instance(NODE, **{**PROVISION, **override})
    assert error in out, out
    assert sets == []


def test_provision_reports_device_error(fake_evpn, monkeypatch):
    def failing_set(node, updates=None, deletes=None):
        raise RuntimeError("gNMI SET failed: invalid value")
    monkeypatch.setattr(junos_backend, "gnmi_set_batch", failing_set)
    out = JunOSBackend().provision_evpn_instance(NODE, **PROVISION)
    assert out.startswith("Error: provisioning failed on ptx; no changes were applied.")
    assert "invalid value" in out


def test_delete_removes_instance_and_units(fake_evpn):
    _, sets = fake_evpn
    out = JunOSBackend().delete_evpn_instance(NODE, "vlan10", dry_run=False)
    assert out == "OK: EVPN instance 'EVPN-VXLAN10' and unit(s) et-0/0/2.10 deleted from ptx."
    assert sets == [{"updates": [], "deletes": [
        f"{ROUTING_INSTANCES_PATH}/instance[name=EVPN-VXLAN10]",
        f"{INTERFACES_PATH}/interface[name=et-0/0/2]/unit[name=10]",
    ]}]


def test_delete_dry_run_and_not_found(fake_evpn):
    _, sets = fake_evpn
    out = JunOSBackend().delete_evpn_instance(NODE, "EVPN-VXLAN10", dry_run=True)
    assert out.count("Operation: DELETE") == 2
    assert "et-0/0/2]/unit[name=10]" in out
    assert "not found" in JunOSBackend().delete_evpn_instance(NODE, "EVPN-NOPE", dry_run=False)
    assert sets == []


# ---------------------------------------------------------------------------
# Subscribe ONCE decoding (client.py)
# ---------------------------------------------------------------------------

def _update(path: str, **val) -> Update:
    return Update(path=gnmi_path_generator(path), val=TypedValue(**val))


def _response(prefix: str, *updates: Update) -> SubscribeResponse:
    return SubscribeResponse(update=Notification(prefix=gnmi_path_generator(prefix), update=updates))


def test_updates_to_tree_builds_keyed_lists():
    nbr = f"{BGP_PATH}/neighbors/neighbor[neighbor-address=192.1.2.1]"
    messages = [
        _response(nbr,
                  _update("neighbor-address", string_val="192.1.2.1"),
                  _update("state/session-state", string_val="ESTABLISHED"),
                  _update("state/peer-as", uint_val=65000),
                  _update("state/supported-capabilities",
                          leaflist_val=ScalarArray(element=[TypedValue(string_val="MPBGP"),
                                                            TypedValue(string_val="ASN32")]))),
        _response(f"{nbr}/afi-safis/afi-safi[afi-safi-name=L2VPN_EVPN]",
                  _update("state/active", bool_val=True),
                  _update("state/prefixes/received", uint_val=4)),
        SubscribeResponse(sync_response=True),
    ]
    tree = junos_client._updates_to_tree(messages)
    got = junos_client._descend(tree, gnmi_path_generator(f"{BGP_PATH}/neighbors").elem)
    assert got == {"neighbor": [{
        "neighbor-address": "192.1.2.1",
        "state": {"session-state": "ESTABLISHED", "peer-as": 65000, "supported-capabilities": ["MPBGP", "ASN32"]},
        "afi-safis": {"afi-safi": [{"afi-safi-name": "L2VPN_EVPN",
                                    "state": {"active": True, "prefixes": {"received": 4}}}]},
    }]}


def test_descend_returns_none_for_missing_key():
    tree = junos_client._updates_to_tree([
        _response(f"{BGP_PATH}/neighbors/neighbor[neighbor-address=192.1.2.1]",
                  _update("state/session-state", string_val="ESTABLISHED")),
    ])
    missing = gnmi_path_generator(f"{BGP_PATH}/neighbors/neighbor[neighbor-address=10.9.9.9]").elem
    assert junos_client._descend(tree, missing) is None

