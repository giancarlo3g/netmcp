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


# ----------------------------------------------------------------------
# EVPN (replies captured from mv-nexus, trimmed)
# ----------------------------------------------------------------------

from xml.etree import ElementTree as ET  # noqa: E402

from netmcp.nos.nxos.backend import (  # noqa: E402
    _evpn_filter,
    _from_nxos_id,
    _port_id,
    _to_nxos_id,
    _vlan_set,
    _vlan_spec,
)

_SYS = (
    '<data xmlns="urn:ietf:params:xml:ns:netconf:base:1.0">'
    '<System xmlns="http://cisco.com/ns/yang/cisco-nx-os-device">{}</System></data>'
)

BD_10 = (
    "<BD-list><fabEncap>vlan-10</fabEncap><BdState>active</BdState><accEncap>vxlan-1010</accEncap>"
    "<adminSt>active</adminSt><id>10</id><name>mac-vrf-10</name></BD-list>"
)
BD_1 = "<BD-list><fabEncap>vlan-1</fabEncap><BdState>active</BdState><adminSt>active</adminSt><id>1</id></BD-list>"


def _ep(*extra):
    return (
        "<eps-items><epId-items><Ep-list><epId>1</epId><adminSt>enabled</adminSt><hostReach>bgp</hostReach>"
        "<nws-items><vni-items><Nw-list><vni>1010</vni><IngRepl-items><proto>bgp</proto></IngRepl-items>"
        "<suppressARP>off</suppressARP></Nw-list></vni-items>"
        f"{''.join(extra)}</nws-items><sourceInterface>lo0</sourceInterface></Ep-list></epId-items></eps-items>"
    )


EVPN_1010 = (
    "<evpn-items><adminSt>enabled</adminSt><bdevi-items><BDEvi-list><encap>vxlan-1010</encap>"
    "<rd>rd:as2-nn2:10:6</rd><rttp-items>"
    "<RttP-list><type>export</type><ent-items><RttEntry-list><rtt>route-target:as2-nn2:65000:10</rtt>"
    "</RttEntry-list></ent-items></RttP-list>"
    "<RttP-list><type>import</type><ent-items><RttEntry-list><rtt>route-target:as2-nn2:65000:10</rtt>"
    "</RttEntry-list></ent-items></RttP-list>"
    "</rttp-items></BDEvi-list></bdevi-items></evpn-items>"
)


def _port(pid, mode, layer, trunk):
    return (
        f"<PhysIf-list><id>{pid}</id><mode>{mode}</mode><layer>{layer}</layer>"
        f"<trunkVlans>{trunk}</trunkVlans></PhysIf-list>"
    )


PORTS = (
    "<intf-items><phys-items>"
    + _port("eth1/1", "trunk", "Layer2", "10")
    + _port("eth1/3", "access", "Layer3", "1-4094")
    + _port("eth1/5", "trunk", "Layer2", "1-4094")
    + _port("eth1/6", "trunk", "Layer2", "5-12")
    + "</phys-items></intf-items>"
)

EVPN_CONFIG = _SYS.format(f"<bd-items><bd-items>{BD_10}{BD_1}</bd-items></bd-items>{_ep()}{EVPN_1010}{PORTS}")

EVPN_STATE = _SYS.format(
    "<bd-items><bd-items><BD-list><fabEncap>vlan-10</fabEncap><accEncap>vxlan-1010</accEncap>"
    "<adminSt>active</adminSt><member-items><VlanMemberIf-list><id>eth1/1</id><vlan>10</vlan>"
    "</VlanMemberIf-list></member-items><name>mac-vrf-10</name><operSt>up</operSt></BD-list></bd-items></bd-items>"
    + _ep(
        "<opervni-items><OperNw-list><vni>1010</vni><epId>1</epId><mode>CP</mode><state>Up</state>"
        "<type>L2</type><vlanBD>vlan-10</vlanBD></OperNw-list></opervni-items>"
    ).replace(
        "<sourceInterface>",
        "<operState>up</operState><peers-items><dy_peer-items>"
        "<DyPeer-list><ip>192.1.1.4</ip><firstVNI>1010</firstVNI><state>Up</state>"
        "<upStateTransitionTs>2026-09-28T20:20:59.379+00:00</upStateTransitionTs></DyPeer-list>"
        "<DyPeer-list><ip>192.1.1.3</ip><firstVNI>1010</firstVNI><state>Up</state>"
        "<upStateTransitionTs>2026-09-28T20:18:27.916+00:00</upStateTransitionTs></DyPeer-list>"
        "</dy_peer-items></peers-items><primaryIp>192.1.1.6</primaryIp><sourceInterface>",
    )
    + "<mac-items><table-items><vlan-items>"
    "<MacAddressEntry-list><vlan>vlan-10</vlan><macAddress>00:C1:AB:00:00:06</macAddress>"
    "<macInfo>standard</macInfo><port>eth1/1</port><static>false</static></MacAddressEntry-list>"
    "<MacAddressEntry-list><vlan>vlan-10</vlan><macAddress>00:C1:AB:00:00:03</macAddress>"
    "<macInfo>nve</macInfo><port>Nve</port><static>false</static></MacAddressEntry-list>"
    "<MacAddressEntry-list><vlan>vlan-0</vlan><macAddress>0C:41:15:00:1B:08</macAddress>"
    "<macInfo>standard</macInfo><port>supeth1</port><static>true</static></MacAddressEntry-list>"
    "</vlan-items></table-items></mac-items>"
)

EMPTY = '<data xmlns="urn:ietf:params:xml:ns:netconf:base:1.0"/>'


@pytest.fixture
def fake_evpn(monkeypatch):
    replies = {"get-config": EVPN_CONFIG, "get": EVPN_STATE}
    calls = []

    monkeypatch.setattr(nxos_backend, "netconf_get_config",
                        lambda node, f: calls.append(("get-config", f)) or _decode(replies["get-config"]))
    monkeypatch.setattr(nxos_backend, "netconf_get",
                        lambda node, f: calls.append(("get", f)) or _decode(replies["get"]))
    monkeypatch.setattr(nxos_backend, "netconf_edit_config",
                        lambda node, xml: calls.append(("edit-config", xml)))
    return replies, calls


def _edit(calls) -> ET.Element:
    edits = [c for c in calls if c[0] == "edit-config"]
    assert len(edits) == 1
    return ET.fromstring(edits[0][1])


NS = "{http://cisco.com/ns/yang/cisco-nx-os-device}"
NC_OP = "{urn:ietf:params:xml:ns:netconf:base:1.0}operation"


def test_evpn_instances(fake_evpn):
    _, calls = fake_evpn
    result = json.loads(NXOSBackend().get_evpn_instances(NODE))["nexus"]
    assert result == [{"name": "mac-vrf-10", "type": "vlan", "vni": 1010, "evi": 10}]
    assert calls == [("get-config", _evpn_filter())]


def test_evpn_instances_none(fake_evpn):
    replies, _ = fake_evpn
    replies["get-config"] = _SYS.format(f"<bd-items><bd-items>{BD_1}</bd-items></bd-items>")
    assert NXOSBackend().get_evpn_instances(NODE).startswith("No EVPN instances found")
    replies["get-config"] = EMPTY
    assert NXOSBackend().get_evpn_instances(NODE).startswith("No EVPN instances found")


@pytest.mark.parametrize("key", ["mac-vrf-10", "10"])
def test_evpn_instance_by_name_or_vlan(fake_evpn, key):
    result = json.loads(NXOSBackend().get_evpn_instance(NODE, key))["nexus"]
    assert result["vlan-id"] == 10 and result["vni"] == 1010
    assert result["route-distinguisher"] == "10:6"
    assert result["import-rt"] == ["65000:10"] and result["export-rt"] == ["65000:10"]
    assert result["nve-interface"] == "nve1" and result["replication"] == "bgp"
    assert result["trunk-interfaces"] == [
        {"name": "eth1/1", "trunk-vlans": "10"},
        {"name": "eth1/5", "trunk-vlans": "1-4094"},
        {"name": "eth1/6", "trunk-vlans": "5-12"},
    ]


def test_evpn_instance_not_found(fake_evpn):
    assert NXOSBackend().get_evpn_instance(NODE, "1").startswith("Service '1' not found")
    assert NXOSBackend().get_evpn_instance_state(NODE, "nope").startswith("Service 'nope' state not found")


def test_evpn_instance_state(fake_evpn):
    _, calls = fake_evpn
    result = json.loads(NXOSBackend().get_evpn_instance_state(NODE, "10"))["nexus"]
    assert "<fabEncap>vlan-10</fabEncap>" in calls[-1][1]
    assert result["vlan-state"] == {"admin": "active", "oper": "up"}
    assert result["vlan-members"] == ["eth1/1"]
    assert result["vni-state"] == {"state": "Up", "type": "L2", "mode": "CP", "vlanBD": "vlan-10"}
    assert result["nve"]["state"] == "up" and result["nve"]["source-ip"] == "192.1.1.6"
    assert [v["peer"] for v in result["vteps"]] == ["192.1.1.4", "192.1.1.3"]
    assert result["macs"] == [
        {"mac": "00:C1:AB:00:00:06", "interface": "eth1/1", "learned": "local", "static": False},
        {"mac": "00:C1:AB:00:00:03", "interface": "Nve", "learned": "remote", "static": False},
    ]


def test_id_conversions():
    assert _to_nxos_id("65000:10", "route-target") == "route-target:as2-nn2:65000:10"
    assert _to_nxos_id("target:65000:10", "route-target") == "route-target:as2-nn2:65000:10"
    assert _to_nxos_id("65000:100000", "route-target") == "route-target:as2-nn4:65000:100000"
    assert _to_nxos_id("4200000000:1", "rd") == "rd:as4-nn2:4200000000:1"
    assert _to_nxos_id("1.1.1.1:5", "rd") == "rd:ipv4-nn2:1.1.1.1:5"
    assert _to_nxos_id("auto", "rd") == "rd:unknown:0:0"
    with pytest.raises(ValueError):
        _to_nxos_id("1.1.1.1:70000", "rd")
    with pytest.raises(ValueError):
        _to_nxos_id("bogus", "route-target")
    assert _from_nxos_id("rd:as2-nn2:10:6") == "10:6"
    assert _from_nxos_id("rd:ipv4-nn2:1.1.1.1:5") == "1.1.1.1:5"
    assert _from_nxos_id("rd:unknown:0:0") == "auto"


def test_vlan_ranges_and_port_ids():
    assert _vlan_set("1-3,10") == {1, 2, 3, 10}
    assert _vlan_set("") == set() and _vlan_set("none") == set()
    assert _vlan_spec({1, 2, 3, 10, 11, 20}) == "1-3,10-11,20"
    assert _port_id("Ethernet1/1") == "eth1/1" and _port_id("eth1/2/3") == "eth1/2/3"
    assert _port_id("Vlan10") is None


def _provision(**kw):
    args = dict(service_name="test-20", service_id=0, vni=1020, evi=0, route_distinguisher="10:20",
                export_rt="65000:20", import_rt="target:65000:20", dry_run=False,
                interface_name="Ethernet1/1", vlan_id=20)
    args.update(kw)
    return NXOSBackend().provision_evpn_instance(NODE, **args)


def test_provision_requires_interface_and_vlan(fake_evpn):
    assert _provision(interface_name="").startswith("Error: NX-OS EVPN provisioning requires")
    assert _provision(vlan_id=0).startswith("Error: NX-OS EVPN provisioning requires")


def test_provision_validation(fake_evpn):
    _, calls = fake_evpn
    result = _provision(interface_name="Vlan20", export_rt="bad", vlan_id=4000)
    assert result.startswith("Validation error(s):")
    assert "interface_name" in result and "export_rt" in result and "vlan_id" in result
    assert calls == []


@pytest.mark.parametrize("kw, error", [
    ({"vlan_id": 10}, "VLAN 10 already exists"),
    ({"vni": 1010}, "VNI 1010 is already mapped"),
    ({"interface_name": "Ethernet1/3"}, "not a layer-2 trunk"),
    ({"interface_name": "Ethernet1/9"}, "interface Ethernet1/9 not found"),
])
def test_provision_prechecks(fake_evpn, kw, error):
    _, calls = fake_evpn
    assert error in _provision(**kw)
    assert not [c for c in calls if c[0] == "edit-config"]


def test_provision_requires_nve(fake_evpn):
    replies, _ = fake_evpn
    replies["get-config"] = _SYS.format(f"<bd-items><bd-items>{BD_1}</bd-items></bd-items>{PORTS}")
    assert "no NVE interface configured" in _provision()


def test_provision_dry_run(fake_evpn):
    _, calls = fake_evpn
    result = _provision(dry_run=True)
    assert result.startswith("[DRY RUN] Node: nexus")
    assert "<accEncap>vxlan-1020</accEncap>" in result
    assert "<rtt>route-target:as2-nn2:65000:20</rtt>" in result
    assert "<trunkVlans>10,20</trunkVlans>" in result
    assert not [c for c in calls if c[0] == "edit-config"]


def test_provision_applies_one_edit_config(fake_evpn):
    _, calls = fake_evpn
    assert _provision().startswith("OK: EVPN instance 'test-20'")
    root = _edit(calls)
    bd = root.find(f"{NS}bd-items/{NS}bd-items/{NS}BD-list")
    assert [bd.findtext(f"{NS}{k}") for k in ("fabEncap", "name", "accEncap")] == ["vlan-20", "test-20", "vxlan-1020"]
    nw = root.find(f"{NS}eps-items/{NS}epId-items/{NS}Ep-list/{NS}nws-items/{NS}vni-items/{NS}Nw-list")
    assert nw.findtext(f"{NS}vni") == "1020" and nw.findtext(f"{NS}IngRepl-items/{NS}proto") == "bgp"
    evi = root.find(f"{NS}evpn-items/{NS}bdevi-items/{NS}BDEvi-list")
    assert evi.findtext(f"{NS}rd") == "rd:as2-nn2:10:20"
    assert {r.findtext(f"{NS}type") for r in evi.iter(f"{NS}RttP-list")} == {"import", "export"}
    assert root.findtext(f"{NS}intf-items/{NS}phys-items/{NS}PhysIf-list/{NS}trunkVlans") == "10,20"


def test_provision_leaves_trunk_that_already_carries_vlan(fake_evpn):
    _, calls = fake_evpn
    assert _provision(interface_name="Ethernet1/5").startswith("OK:")
    assert _edit(calls).find(f"{NS}intf-items") is None


def test_provision_edit_config_failure(fake_evpn, monkeypatch):
    def boom(node, xml):
        raise RuntimeError("NETCONF edit-config failed on clab-test-nexus: rejected")
    monkeypatch.setattr(nxos_backend, "netconf_edit_config", boom)
    assert _provision().startswith("Error: provisioning failed on nexus; no changes were applied.")


def test_delete_not_found(fake_evpn):
    assert NXOSBackend().delete_evpn_instance(NODE, "99", False).startswith("Service '99' not found")


def test_delete_dry_run_and_apply(fake_evpn):
    _, calls = fake_evpn
    dry = NXOSBackend().delete_evpn_instance(NODE, "mac-vrf-10", True)
    assert 'nc:operation="delete"' in dry
    assert "VLAN 10 left in trunk allowed list of eth1/1" in dry
    assert not [c for c in calls if c[0] == "edit-config"]

    result = NXOSBackend().delete_evpn_instance(NODE, "mac-vrf-10", False)
    assert result.startswith("OK: EVPN instance 'mac-vrf-10' (VLAN=10, VNI=1010) deleted")
    root = _edit(calls)
    deleted = {el.tag.replace(NS, "") for el in root.iter() if el.get(NC_OP) == "delete"}
    assert deleted == {"BD-list", "Nw-list", "BDEvi-list"}
    # eth1/1 (only VLAN) and eth1/5 (all VLANs) are untouched; eth1/6 drops VLAN 10.
    ports = root.findall(f"{NS}intf-items/{NS}phys-items/{NS}PhysIf-list")
    assert [(p.findtext(f"{NS}id"), p.findtext(f"{NS}trunkVlans")) for p in ports] == [("eth1/6", "5-9,11-12")]
