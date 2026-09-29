"""Unit tests for Cisco NX-OS BGP reply parsing (nos/nxos/backend.py, nos/nxos/client.py).

netconf_get / netconf_get_config are mocked with <data> replies captured from
mv-nexus (N9Kv 10.6.3F, Cisco-NX-OS-device model), trimmed to the leaves used,
and decoded through the client's own XML decoder.
"""

import json
import os

import pytest

os.environ.setdefault("NETMCP_NO_INVENTORY", "1")

from netmcp.inventory import NodeInfo
from netmcp.nos.nxos import backend as nxos_backend
from netmcp.nos.nxos.backend import NXOSBackend, _afi_safi_name, _bgp_filter
from netmcp.nos.nxos.client import _decode

NODE = NodeInfo(name="nexus", fqdn="clab-test-nexus", nos_type="nxos", transport="netconf")

_DATA = (
    '<data xmlns="urn:ietf:params:xml:ns:netconf:base:1.0">'
    '<System xmlns="http://cisco.com/ns/yang/cisco-nx-os-device"><bgp-items><inst-items>{}'
    "</inst-items></bgp-items></System></data>"
)


def _peer(addr, oper_st, tbl_st, accepted, sent, flap):
    return (
        f"<Peer-list><addr>{addr}</addr><adminSt>enabled</adminSt>"
        f"<ent-items><PeerEntry-list><addr>{addr}</addr>"
        f"<af-items><PeerAfEntry-list><type>l2vpn-evpn</type><acceptedPaths>{accepted}</acceptedPaths>"
        f"<pfxSent>{sent}</pfxSent><tblSt>{tbl_st}</tblSt></PeerAfEntry-list></af-items>"
        f"<connEst>1</connEst><lastFlapTs>{flap}</lastFlapTs><operAsn>65000</operAsn>"
        f"<operSt>{oper_st}</operSt><rtrId>{addr}</rtrId><type>ibgp</type>"
        "</PeerEntry-list></ent-items><peerImp>iBGP-evpn</peerImp></Peer-list>"
    )


TEMPLATE = (
    "<peercont-items><PeerCont-list><name>iBGP-evpn</name><asn>65000</asn><srcIf>lo0</srcIf>"
    "<af-items><PeerAf-list><type>l2vpn-evpn</type><sendComExt>enabled</sendComExt></PeerAf-list></af-items>"
    "</PeerCont-list></peercont-items>"
)

PEER_1 = _peer("192.1.2.1", "established", "up", 8, 2, "2026-09-28T20:17:59.610+00:00")
PEER_2 = _peer("192.1.2.2", "established", "up", 0, 2, "2026-09-28T20:18:01.138+00:00")


def _dom(*peers):
    return _DATA.format(
        "<asn>65000</asn><dom-items><Dom-list><name>default</name>"
        "<numEstPeers>2</numEstPeers><numPeers>2</numPeers><operRtrId>192.1.1.6</operRtrId>"
        f"<peer-items>{''.join(peers)}</peer-items>{TEMPLATE}<rtrId>192.1.1.6</rtrId>"
        "</Dom-list></dom-items>"
    )


# A filtered-out peer comes back as the key-only skeleton.
EMPTY_PEER = _DATA.format("<dom-items><Dom-list><name>default</name></Dom-list></dom-items>")


@pytest.fixture
def fake_netconf(monkeypatch):
    calls = []
    replies = {"get": _dom(PEER_2, PEER_1), "get-config": _DATA.format("<asn>65000</asn>")}

    def fake_get(node, filter_xml):
        calls.append(("get", filter_xml))
        if "<addr>" in filter_xml:
            return _decode(_dom(PEER_1) if "192.1.2.1" in filter_xml else EMPTY_PEER)
        return _decode(replies["get"])

    def fake_get_config(node, filter_xml):
        calls.append(("get-config", filter_xml))
        return _decode(replies["get-config"]) if replies["get-config"] else None

    monkeypatch.setattr(nxos_backend, "netconf_get", fake_get)
    monkeypatch.setattr(nxos_backend, "netconf_get_config", fake_get_config)
    return replies, calls


def test_decode_lists_and_empty():
    data = _decode(_dom(PEER_1))
    dom = data["System"]["bgp-items"]["inst-items"]["dom-items"]["Dom-list"]
    assert isinstance(dom, list) and dom[0]["name"] == "default"
    assert isinstance(dom[0]["peer-items"]["Peer-list"], list)
    assert _decode('<data xmlns="urn:ietf:params:xml:ns:netconf:base:1.0"/>') is None


def test_bgp_summary(fake_netconf):
    result = json.loads(NXOSBackend().get_bgp_summary(NODE))["nexus"]
    assert result["as"] == 65000
    assert result["router-id"] == "192.1.1.6"
    assert result["peers"] == {"total": 2, "established": 2}
    assert result["neighbors"][1] == {
        "peer": "192.1.2.1",
        "peer-as": 65000,
        "state": "ESTABLISHED",
        "afi-safis": {"L2VPN_EVPN": {"received": 8, "sent": 2}},
    }


def test_bgp_summary_counts_non_established(fake_netconf):
    replies, _ = fake_netconf
    replies["get"] = _dom(PEER_1, _peer("192.1.2.2", "idle", "down", 0, 0, "2026-09-28T20:18:01.138+00:00"))
    result = json.loads(NXOSBackend().get_bgp_summary(NODE))["nexus"]
    assert result["peers"] == {"total": 2, "established": 1}
    down = result["neighbors"][1]
    assert down["state"] == "IDLE"
    assert down["afi-safis"] == {}


def test_bgp_summary_error_without_bgp(fake_netconf):
    replies, _ = fake_netconf
    replies["get"] = EMPTY_PEER.replace("<Dom-list><name>default</name></Dom-list>", "")
    assert NXOSBackend().get_bgp_summary(NODE).startswith("Error: could not retrieve BGP summary")


def test_bgp_neighbors_compact_view(fake_netconf):
    peers = json.loads(NXOSBackend().get_bgp_neighbors(NODE))["nexus"]
    assert [p["peer"] for p in peers] == ["192.1.2.2", "192.1.2.1"]
    assert peers[0]["peer-group"] == "iBGP-evpn"
    assert peers[0]["established-transitions"] == 1
    assert peers[0]["last-established"] == "2026-09-28T20:18:01.138+00:00"


def test_bgp_neighbors_peer_as_from_template(fake_netconf):
    replies, _ = fake_netconf
    replies["get"] = _dom(PEER_1.replace("<operAsn>65000</operAsn>", ""))
    assert json.loads(NXOSBackend().get_bgp_neighbors(NODE))["nexus"][0]["peer-as"] == 65000


def test_bgp_neighbors_none(fake_netconf):
    replies, _ = fake_netconf
    replies["get"] = _dom()
    assert NXOSBackend().get_bgp_neighbors(NODE).startswith("No BGP neighbors found")


def test_bgp_neighbor_raw_and_not_found(fake_netconf):
    raw = json.loads(NXOSBackend().get_bgp_neighbor(NODE, "192.1.2.1"))["nexus"]
    assert raw["addr"] == "192.1.2.1"
    assert raw["ent-items"]["PeerEntry-list"][0]["operSt"] == "established"
    assert NXOSBackend().get_bgp_neighbor(NODE, "9.9.9.9").startswith("Error: could not retrieve BGP neighbor")


def test_bgp_config_reads_running(fake_netconf):
    replies, calls = fake_netconf
    assert json.loads(NXOSBackend().get_bgp_config(NODE))["nexus"] == {"asn": "65000"}
    assert calls[-1] == ("get-config", _bgp_filter(vrf=None))
    replies["get-config"] = None
    assert NXOSBackend().get_bgp_config(NODE).startswith("Error: could not retrieve BGP config")


def test_bgp_filter_scopes():
    assert "<Dom-list><name>default</name></Dom-list>" in _bgp_filter()
    assert "<asn/>" in _bgp_filter()
    assert "<Peer-list><addr>10.0.0.1</addr></Peer-list>" in _bgp_filter("10.0.0.1")
    assert _bgp_filter(vrf=None).endswith("<bgp-items/></System>")


def test_afi_safi_name_mapping():
    assert _afi_safi_name("l2vpn-evpn") == "L2VPN_EVPN"
    assert _afi_safi_name("ipv4-ucast") == "IPV4_UNICAST"
    assert _afi_safi_name("ipv4-mvpn") == "IPV4_MVPN"
