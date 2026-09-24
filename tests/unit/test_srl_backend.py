"""Unit tests for SR Linux EVPN reply parsing (nos/srl/backend.py).

gnmi_get is mocked with reply shapes captured from a live SR Linux node.
"""

import json
import os

import pytest

os.environ.setdefault("NETMCP_NO_INVENTORY", "1")

from netmcp.inventory import NodeInfo
from netmcp.nos.srl import backend as srl_backend
from netmcp.nos.srl.backend import SRLBackend

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
