"""Unit tests for Juniper Junos BGP (nos/junos/backend.py and the Subscribe decoding in client.py).

gnmi_subscribe_once / gnmi_get_config are mocked with reply shapes captured from a
live cJunos Evolved 25.4R1 node (mv-ptx).
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
from netmcp.nos.junos.backend import BGP_CONFIG_PATH, BGP_PATH, ROUTING_OPTIONS_PATH, JunOSBackend

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
