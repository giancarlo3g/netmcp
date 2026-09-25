"""Unit tests for SR Linux BGP and EVPN reply parsing (nos/srl/backend.py).

gnmi_get is mocked with reply shapes captured from a live SR Linux node.
"""

import json
import os

import pytest

os.environ.setdefault("NETMCP_NO_INVENTORY", "1")

from netmcp.inventory import NodeInfo
from netmcp.nos.srl import backend as srl_backend
from netmcp.nos.srl.backend import BGP_PATH, SRLBackend

NODE = NodeInfo(name="srl", fqdn="clab-test-srl", nos_type="srl")

NETWORK_INSTANCES = {
    "srl_nokia-network-instance:network-instance": [
        {"name": "default", "type": "srl_nokia-network-instance:default"},
        {
            "name": "mac-vrf-10",
            "type": "srl_nokia-network-instance:mac-vrf",
            "vxlan-interface": [{"name": "vxlan0.10"}],
            "protocols": {
                "bgp-evpn": {
                    "srl_nokia-bgp-evpn:bgp-instance": [
                        {"id": 1, "evi": 10, "vxlan-interface": "vxlan0.10"}
                    ]
                },
            },
        },
        {"name": "mgmt", "type": "srl_nokia-network-instance:ip-vrf"},
    ]
}

TUNNEL_INTERFACES = {
    "srl_nokia-tunnel-interfaces:tunnel-interface": [
        {
            "name": "vxlan0",
            "vxlan-interface": [
                {"index": 10, "type": "srl_nokia-interfaces:bridged", "ingress": {"vni": 1010}}
            ],
        }
    ]
}


@pytest.fixture
def fake_gnmi(monkeypatch):
    replies = {"/network-instance": NETWORK_INSTANCES, "/tunnel-interface": TUNNEL_INTERFACES}
    monkeypatch.setattr(srl_backend, "gnmi_get", lambda node, path: replies.get(path))
    return replies


def test_get_evpn_instances_parses_wrapped_reply(fake_gnmi):
    result = json.loads(SRLBackend().get_evpn_instances(NODE))
    assert result == {"srl": [{"name": "mac-vrf-10", "type": "mac-vrf", "vni": 1010, "evi": 10}]}


def test_get_evpn_instances_no_mac_vrf(fake_gnmi):
    fake_gnmi["/network-instance"] = {
        "srl_nokia-network-instance:network-instance": [
            {"name": "default", "type": "srl_nokia-network-instance:default"}
        ]
    }
    assert "No MAC-VRF instances found" in SRLBackend().get_evpn_instances(NODE)


def test_get_evpn_instances_missing_tunnel_leaves_vni_none(fake_gnmi):
    del fake_gnmi["/tunnel-interface"]
    result = json.loads(SRLBackend().get_evpn_instances(NODE))
    assert result["srl"][0]["vni"] is None
    assert result["srl"][0]["evi"] == 10


# ---------------------------------------------------------------------------
# BGP (reply shapes captured from mv-srl)
# ---------------------------------------------------------------------------

def _afi(name: str, up: bool, received=None, active=None, sent=None) -> dict:
    return {
        "afi-safi-name": f"srl_nokia-common:{name}",
        "admin-state": "enable" if up else "disable",
        "oper-state": "up" if up else "down",
        "received-routes": received,
        "active-routes": active,
        "sent-routes": sent,
        "rejected-routes": 0 if up else None,
    }


def _bgp_neighbor(addr: str, received: int) -> dict:
    return {
        "peer-address": addr,
        "admin-state": "enable",
        "peer-type": "ibgp",
        "peer-as": 65000,
        "peer-group": "iBGP-evpn",
        "peer-router-id": addr,
        "session-state": "established",
        "last-state": "active",
        "last-established": "2026-09-24T19:33:57.400Z",
        "established-transitions": "1",
        "timers": {"hold-time": 90, "negotiated-hold-time": 90},
        "afi-safi": [
            _afi("evpn", True, received=received, active=2, sent=1),
            _afi("ipv4-unicast", False),
            _afi("route-target", False),
        ],
    }


def _bgp() -> dict:
    return {
        "admin-state": "enable",
        "oper-state": "up",
        "autonomous-system": 65000,
        "router-id": "192.1.1.1",
        "afi-safi": [{"afi-safi-name": "srl_nokia-common:evpn", "admin-state": "enable",
                      "received-routes": "3", "active-routes": "2"}],
        "group": [{"group-name": "iBGP-evpn", "peer-as": 65000}],
        "neighbor": [_bgp_neighbor("192.1.2.1", 3), _bgp_neighbor("192.1.2.2", 0)],
        "statistics": {"total-paths": "6", "total-prefixes": "3", "total-peers": 2, "up-peers": 2},
    }


BGP_CONFIG = {
    "admin-state": "enable",
    "autonomous-system": 65000,
    "router-id": "192.1.1.1",
    "group": [{"group-name": "iBGP-evpn", "peer-as": 65000}],
    "neighbor": [{"peer-address": "192.1.2.1", "peer-group": "iBGP-evpn"}],
}


@pytest.fixture
def fake_bgp(monkeypatch):
    replies = {
        BGP_PATH: _bgp(),
        f"{BGP_PATH}/neighbor": {"srl_nokia-bgp:neighbor": _bgp()["neighbor"]},
        f"{BGP_PATH}/neighbor[peer-address=192.1.2.1]": _bgp_neighbor("192.1.2.1", 3),
    }
    calls = []

    def fake_get(node, path, datatype="all"):
        calls.append((path, datatype))
        if datatype == "config":
            return BGP_CONFIG if path == BGP_PATH else None
        return replies.get(path)

    monkeypatch.setattr(srl_backend, "gnmi_get", fake_get)
    return replies, calls


def test_bgp_summary(fake_bgp):
    result = json.loads(SRLBackend().get_bgp_summary(NODE))["srl"]
    assert result["as"] == 65000
    assert result["router-id"] == "192.1.1.1"
    assert result["total-paths"] == 6  # string counters converted
    assert result["total-prefixes"] == 3
    assert result["peers"] == {"total": 2, "established": 2}
    assert result["neighbors"][0] == {
        "peer": "192.1.2.1", "peer-as": 65000, "state": "ESTABLISHED",
        "afi-safis": {"L2VPN_EVPN": {"received": 3, "sent": 1, "installed": 2}},
    }


def test_bgp_summary_accepts_module_wrapped_reply(fake_bgp):
    fake_bgp[0][BGP_PATH] = {"srl_nokia-bgp:bgp": _bgp()}
    result = json.loads(SRLBackend().get_bgp_summary(NODE))["srl"]
    assert result["as"] == 65000
    assert result["peers"]["total"] == 2


def test_bgp_summary_counts_non_established(fake_bgp):
    fake_bgp[0][BGP_PATH]["neighbor"][1]["session-state"] = "active"
    result = json.loads(SRLBackend().get_bgp_summary(NODE))["srl"]
    assert result["peers"] == {"total": 2, "established": 1}
    assert result["neighbors"][1]["state"] == "ACTIVE"


def test_bgp_summary_error_without_bgp(fake_bgp):
    del fake_bgp[0][BGP_PATH]
    assert SRLBackend().get_bgp_summary(NODE).startswith("Error: could not retrieve BGP summary")


def test_bgp_neighbors_compact_view(fake_bgp):
    peers = json.loads(SRLBackend().get_bgp_neighbors(NODE))["srl"]
    assert [p["peer"] for p in peers] == ["192.1.2.1", "192.1.2.2"]
    assert peers[0]["peer-group"] == "iBGP-evpn"
    assert peers[0]["established-transitions"] == 1
    assert list(peers[0]["afi-safis"]) == ["L2VPN_EVPN"]  # down AFI-SAFIs dropped


def test_bgp_neighbors_list_of_updates(fake_bgp):
    fake_bgp[0][f"{BGP_PATH}/neighbor"] = _bgp()["neighbor"]
    peers = json.loads(SRLBackend().get_bgp_neighbors(NODE))["srl"]
    assert [p["peer"] for p in peers] == ["192.1.2.1", "192.1.2.2"]


def test_bgp_neighbors_none(fake_bgp):
    del fake_bgp[0][f"{BGP_PATH}/neighbor"]
    assert "No BGP neighbors found" in SRLBackend().get_bgp_neighbors(NODE)


def test_afi_safi_name_mapping():
    assert srl_backend._afi_safi_name("srl_nokia-common:evpn") == "L2VPN_EVPN"
    assert srl_backend._afi_safi_name("srl_nokia-common:ipv4-unicast") == "IPV4_UNICAST"
    assert srl_backend._afi_safi_name("l3vpn-ipv4-unicast") == "L3VPN_IPV4_UNICAST"


def test_bgp_neighbor_raw_and_not_found(fake_bgp):
    raw = json.loads(SRLBackend().get_bgp_neighbor(NODE, "192.1.2.1"))["srl"]
    assert raw["session-state"] == "established"
    assert raw["timers"]["hold-time"] == 90
    assert SRLBackend().get_bgp_neighbor(NODE, "10.9.9.9").startswith("Error: could not retrieve BGP neighbor")


def test_bgp_config_requests_config_only(fake_bgp):
    _, calls = fake_bgp
    result = json.loads(SRLBackend().get_bgp_config(NODE))["srl"]
    assert calls == [(BGP_PATH, "config")]
    assert result["group"][0]["group-name"] == "iBGP-evpn"
    assert "statistics" not in result
