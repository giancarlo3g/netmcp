"""Unit tests for NVIDIA Cumulus BGP (nos/cumulus/backend.py and the NVUE client).

nvue_get is mocked with NVUE REST replies captured from mv-nvidia (Cumulus VX 5.16.1).
Large replies are trimmed; every key and value kept is as captured.
"""

import json
import os

import httpx
import pytest

os.environ.setdefault("NETMCP_NO_INVENTORY", "1")

from netmcp.inventory import NodeInfo
from netmcp.nos.cumulus import backend as cumulus_backend
from netmcp.nos.cumulus import client as cumulus_client
from netmcp.nos.cumulus.backend import (
    GLOBAL_BGP_PATH,
    NEIGHBOR_PATH,
    VRF_BGP_PATH,
    CumulusBackend,
)

NODE = NodeInfo(name="nvidia", fqdn="mv-nvidia", nos_type="cumulus", transport="rest", username="cumulus")

# router/bgp?rev=applied
ROUTER_BGP = {
    "autonomous-system": 65000,
    "convergence-wait": {"establish-wait-time": 0, "time": 0},
    "graceful-restart": {
        "mode": "helper-only",
        "path-selection-deferral-time": 120,
        "restart-time": 120,
        "stale-routes-time": 360,
    },
    "graceful-shutdown": "disabled",
    "policy-update-timer": 5,
    "queue-limit": {"input": 10000, "output": 10000},
    "router-id": "192.1.1.7",
    "state": "enabled",
    "wait-for-install": "disabled",
}


def _op_neighbor(rx_prefix: int, rx_total: int) -> dict:
    return {
        "address-family": {"l2vpn-evpn": {"rx-prefix": rx_prefix, "tx-prefix": 2}},
        "last-reset-reason": "Waiting for peer OPEN",
        "last-reset-time": 73298000,
        "message-stats": {"rx-total": rx_total, "tx-total": 197546},
        "remote-as": 65000,
        "session-state": "established",
        "uptime": 591683000,
    }


# vrf/default/router/bgp/neighbor?rev=operational (complete)
NEIGHBORS = {"192.1.2.1": _op_neighbor(10, 199060), "192.1.2.2": _op_neighbor(0, 219001)}

# vrf/default/router/bgp/neighbor/192.1.2.1?rev=operational (trimmed)
NEIGHBOR_192_1_2_1 = {
    "address-family": {"l2vpn-evpn": {
        "capabilities": {"rx-mpbgp": "on", "tx-mpbgp": "on"},
        "rx-prefix": 10,
        "tx-prefix": 2,
        "update-group": 1,
    }},
    "bgp-version": 4,
    "connections-established": 1,
    "last-reset-code": 32,
    "last-reset-reason": "Waiting for peer OPEN",
    "local-as": {"asn": 65000, "prepend": "enabled", "replace": "disabled"},
    "local-ip": "192.1.1.7",
    "remote-as": 65000,
    "remote-ip": "192.1.2.1",
    "remote-port": 179,
    "remote-router-id": "192.1.2.1",
    "session-state": "established",
    "timers": {"connection-retry": 10, "hold": 9, "keepalive": 3, "route-advertisement": "none"},
    "uptime": 591683000,
}

# vrf/default/router/bgp/neighbor/10.0.0.99?rev=operational: HTTP 404 with this body
NOT_FOUND_BODY = {
    "detail": "The requested item does not exist.",
    "status": 404,
    "title": "The requested item does not exist.",
    "type": "/errors/ItemDoesNotExist",
}

# vrf/default/router/bgp?rev=applied (trimmed)
VRF_BGP = {
    "address-family": {
        "ipv4-unicast": {"state": "enabled"},
        "ipv6-unicast": {"state": "disabled"},
        "l2vpn-evpn": {"state": "enabled"},
    },
    "autonomous-system": "auto",
    "neighbor": {
        "192.1.2.1": {
            "address-family": {
                "ipv4-unicast": {"state": "disabled"},
                "l2vpn-evpn": {"route-reflector-client": "disabled", "state": "enabled"},
            },
            "description": "none",
            "password": "$nvsec$d1a028e8c7f97db92876c2a30fcc403f",
            "peer-group": "none",
            "remote-as": "internal",
            "state": "enabled",
            "type": "numbered",
            "update-source": "lo",
        },
    },
    "router-id": "auto",
    "state": "enabled",
}


@pytest.fixture
def nvue(monkeypatch):
    """Mock nvue_get with a {(path, rev): reply} table; records the calls."""
    replies: dict = {}
    calls: list = []

    def fake(node, path, rev="operational"):
        calls.append((path, rev))
        reply = replies.get((path, rev))
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(cumulus_backend, "nvue_get", fake)
    fake.replies, fake.calls = replies, calls
    return fake


def _result(out: str):
    return json.loads(out)["nvidia"]


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------

def test_bgp_summary(nvue):
    nvue.replies[(GLOBAL_BGP_PATH, "applied")] = ROUTER_BGP
    nvue.replies[(NEIGHBOR_PATH, "operational")] = NEIGHBORS
    res = _result(CumulusBackend().get_bgp_summary(NODE))
    assert res["as"] == 65000
    assert res["router-id"] == "192.1.1.7"
    assert res["peers"] == {"total": 2, "established": 2}
    first = res["neighbors"][0]
    assert first["peer"] == "192.1.2.1"
    assert first["state"] == "ESTABLISHED"
    assert first["afi-safis"] == {"L2VPN_EVPN": {"received": 10, "sent": 2}}


def test_bgp_summary_without_global_is_error(nvue):
    nvue.replies[(NEIGHBOR_PATH, "operational")] = NEIGHBORS
    assert CumulusBackend().get_bgp_summary(NODE).startswith("Error:")


def test_bgp_neighbors(nvue):
    nvue.replies[(NEIGHBOR_PATH, "operational")] = NEIGHBORS
    peers = _result(CumulusBackend().get_bgp_neighbors(NODE))
    assert peers[1] == {
        "peer": "192.1.2.2",
        "peer-as": 65000,
        "state": "ESTABLISHED",
        "uptime": 591683000,
        "last-reset-reason": "Waiting for peer OPEN",
        "afi-safis": {"L2VPN_EVPN": {"received": 0, "sent": 2}},
    }


def test_bgp_neighbors_empty(nvue):
    assert CumulusBackend().get_bgp_neighbors(NODE).startswith("No BGP neighbors found")


def test_bgp_neighbor(nvue):
    nvue.replies[(f"{NEIGHBOR_PATH}/192.1.2.1", "operational")] = NEIGHBOR_192_1_2_1
    assert _result(CumulusBackend().get_bgp_neighbor(NODE, "192.1.2.1")) == NEIGHBOR_192_1_2_1


def test_bgp_neighbor_unknown(nvue):
    assert CumulusBackend().get_bgp_neighbor(NODE, "10.0.0.99").startswith("Error:")


def test_bgp_neighbor_invalid_ip_makes_no_request(nvue):
    out = CumulusBackend().get_bgp_neighbor(NODE, "1.2.3.4/../../system")
    assert out.startswith("Error:") and "not a valid IP" in out
    assert nvue.calls == []


def test_bgp_config(nvue):
    nvue.replies[(GLOBAL_BGP_PATH, "applied")] = ROUTER_BGP
    nvue.replies[(VRF_BGP_PATH, "applied")] = VRF_BGP
    res = _result(CumulusBackend().get_bgp_config(NODE))
    assert res["router"]["bgp"]["autonomous-system"] == 65000
    vrf = res["vrf"]["default"]["router"]["bgp"]
    assert vrf["neighbor"]["192.1.2.1"]["remote-as"] == "internal"
    assert vrf["neighbor"]["192.1.2.1"]["update-source"] == "lo"
    assert vrf["neighbor"]["192.1.2.1"]["password"] == "<redacted>"
    assert "$nvsec$" not in json.dumps(res)


def test_bgp_config_missing_is_error(nvue):
    assert CumulusBackend().get_bgp_config(NODE).startswith("Error:")


@pytest.mark.parametrize("method,args", [
    ("get_bgp_summary", ()),
    ("get_bgp_neighbors", ()),
    ("get_bgp_neighbor", ("192.1.2.1",)),
    ("get_bgp_config", ()),
])
def test_transport_errors_become_error_strings(nvue, method, args):
    err = RuntimeError("authentication failed (HTTP 401)")
    for key in [(GLOBAL_BGP_PATH, "applied"), (VRF_BGP_PATH, "applied"),
                (NEIGHBOR_PATH, "operational"), (f"{NEIGHBOR_PATH}/192.1.2.1", "operational")]:
        nvue.replies[key] = err
    out = getattr(CumulusBackend(), method)(NODE, *args)
    assert out.startswith("Error:") and "authentication failed" in out


def test_unimplemented_domain():
    assert CumulusBackend().get_isis_adjacencies(NODE) == (
        "Error: get_isis_adjacencies is not implemented for NOS 'cumulus'."
    )


# ---------------------------------------------------------------------------
# NVUE client
# ---------------------------------------------------------------------------

@pytest.fixture
def http(monkeypatch):
    """Route the client's httpx.Client through a MockTransport; records requests and kwargs."""
    state = {"status": 200, "body": b"{}", "requests": [], "kwargs": {}}

    def handler(request):
        state["requests"].append(request)
        return httpx.Response(state["status"], content=state["body"])

    real_client = httpx.Client

    def make_client(**kwargs):
        state["kwargs"] = kwargs
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(cumulus_client.httpx, "Client", make_client)
    monkeypatch.setenv("NETMCP_NVIDIA_PASSWORD", "Clab123!")
    return state


def test_client_get_ok(http):
    http["body"] = json.dumps(ROUTER_BGP).encode()
    assert cumulus_client.nvue_get(NODE, "router/bgp", rev="applied") == ROUTER_BGP
    req = http["requests"][0]
    assert req.method == "GET"
    assert str(req.url) == "https://mv-nvidia:8765/nvue_v1/router/bgp?rev=applied"
    assert http["kwargs"]["trust_env"] is False
    assert http["kwargs"]["verify"] is False
    assert http["kwargs"]["auth"] == ("cumulus", "Clab123!")


def test_client_404_is_none(http):
    http["status"], http["body"] = 404, json.dumps(NOT_FOUND_BODY).encode()
    assert cumulus_client.nvue_get(NODE, f"{NEIGHBOR_PATH}/10.0.0.99") is None


def test_client_401_html_body(http):
    # Captured from mv-nvidia with a wrong password: nginx HTML, not JSON.
    http["status"] = 401
    http["body"] = (b"<html>\n<head><title>401 Authorization Required</title></head>\n<body>\n"
                    b"<center><h1>401 Authorization Required</h1></center>\n"
                    b"<hr><center>nginx</center>\n</body>\n</html>\n")
    with pytest.raises(RuntimeError, match="authentication failed"):
        cumulus_client.nvue_get(NODE, "router/bgp", rev="applied")


def test_client_server_error(http):
    http["status"] = 500
    with pytest.raises(RuntimeError, match="HTTP 500"):
        cumulus_client.nvue_get(NODE, "router/bgp")


def test_client_rejects_unknown_revision(http):
    with pytest.raises(ValueError):
        cumulus_client.nvue_get(NODE, "router/bgp", rev="startup")
    assert http["requests"] == []


# ---------------------------------------------------------------------------
# EVPN (reply shapes captured from mv-nvidia, Cumulus VX 5.16.1, trimmed)
# ---------------------------------------------------------------------------

from netmcp.nos.cumulus.backend import BRIDGE_PATH, EVPN_PATH, INTERFACES_PATH, NVE_PATH

# bridge/domain/br_default?rev=applied
BRIDGE = {
    "type": "vlan-aware",
    "untagged": "none",
    "vlan": {"10": {
        "ptp": {"state": "disabled"},
        "vni": {"1010": {"flooding": {"state": "auto"}, "mac-learning": "auto"}},
    }},
    "vlan-vni-offset": 0,
}
# evpn?rev=applied
EVPN = {
    "state": "enabled",
    "vni": {"1010": {
        "rd": "10:7",
        "route-advertise": {"default-gateway": "auto", "svi-ip": "auto"},
        "route-target": {"both": {"65000:10": {}}, "export": {}, "import": {}},
    }},
}
# nve?rev=applied -> vxlan
NVE = {"state": "enabled", "source": {"address": "192.1.1.7"}, "port": 4789,
       "flooding": {"head-end-replication": {"evpn": {}}, "state": "enabled"}}


def _port(vlans=None, member=True):
    iface = {"type": "swp", "link": {"mtu": 9216}}
    if member:
        iface["bridge"] = {"domain": {"br_default": {"untagged": "none", "vlan": {str(v): {} for v in vlans or []}}}}
    return iface


# interface?rev=applied (swp1 carries VLAN 10; swp2/swp3 are routed uplinks)
INTERFACES = {"eth0": {"type": "eth"}, "lo": {"type": "loopback"},
              "swp1": _port([10]), "swp2": _port(member=False), "swp3": _port(member=False)}
# evpn/vni/1010?rev=operational
VNI_OPER = {
    "bridge-domain": "br_default", "host-count": 0, "local-vtep": "192.1.1.7", "mac-count": 1,
    "remote-vtep": {f"192.1.1.{i}": {"flood": "HER"} for i in range(1, 7)},
    "remote-vtep-count": 6, "route-target": {"both": {"65000:10": {}}},
    "tenant-vrf": "default", "vxlan-interface": "vxlan48",
}
# evpn/vni/1010/mac?rev=operational
VNI_MACS = {"00:c1:ab:00:00:03": {"detection-count": 0, "duplicate": "off", "local-mobility-seq": 0,
                                  "remote-mobility-seq": 0, "remote-vtep": "192.1.1.3", "type": "remote"}}


@pytest.fixture
def lab(nvue):
    nvue.replies[(BRIDGE_PATH, "applied")] = BRIDGE
    nvue.replies[(EVPN_PATH, "applied")] = EVPN
    nvue.replies[(NVE_PATH, "applied")] = NVE
    nvue.replies[(INTERFACES_PATH, "applied")] = INTERFACES
    nvue.replies[(f"{EVPN_PATH}/vni/1010", "operational")] = VNI_OPER
    nvue.replies[(f"{EVPN_PATH}/vni/1010/mac", "operational")] = VNI_MACS
    return nvue


@pytest.fixture
def applied(monkeypatch):
    """Mock nvue_apply; records the trees and can be told to fail."""
    trees = []

    def fake(node, tree):
        if fake.error:
            raise RuntimeError(fake.error)
        trees.append(tree)
        return "applied_and_saved"

    fake.error, fake.trees = None, trees
    monkeypatch.setattr(cumulus_backend, "nvue_apply", fake)
    return fake


def test_evpn_instances(lab):
    assert _result(CumulusBackend().get_evpn_instances(NODE)) == [
        {"name": "vlan10", "type": "vlan", "vni": 1010, "evi": 10},
    ]


def test_evpn_instances_empty(nvue):
    nvue.replies[(BRIDGE_PATH, "applied")] = {"type": "vlan-aware", "vlan": {}}
    assert CumulusBackend().get_evpn_instances(NODE).startswith("No EVPN instances found")


@pytest.mark.parametrize("name", ["vlan10", "VLAN10", "10", "1010"])
def test_evpn_instance_lookup(lab, name):
    res = _result(CumulusBackend().get_evpn_instance(NODE, name))
    assert res["name"] == "vlan10"
    assert (res["vlan-id"], res["vni"], res["route-distinguisher"]) == (10, 1010, "10:7")
    assert res["import-rt"] == res["export-rt"] == ["65000:10"]
    assert res["access-interfaces"] == ["swp1"]
    assert res["bridge"] == "br_default"


def test_evpn_instance_not_found(lab):
    assert "not found" in CumulusBackend().get_evpn_instance(NODE, "vlan99")


def test_evpn_instance_state(lab):
    res = _result(CumulusBackend().get_evpn_instance_state(NODE, "vlan10"))
    assert res["vni-state"]["local-vtep"] == "192.1.1.7"
    assert res["vni-state"]["remote-vtep-count"] == 6
    assert {v["peer"] for v in res["vteps"]} == {f"192.1.1.{i}" for i in range(1, 7)}
    assert res["macs"] == [{"mac": "00:c1:ab:00:00:03", "learned": "remote", "vtep": "192.1.1.3"}]


PROVISION = dict(service_name="ignored", service_id=0, vni=1020, evi=0, route_distinguisher="20:7",
                 export_rt="65000:20", import_rt="target:65000:20", interface_name="swp1", vlan_id=20)

# The tree applied on mv-nvidia in the Phase 0 write spike (revision 6)
PROVISION_TREE = {
    "bridge": {"domain": {"br_default": {"vlan": {"20": {"vni": {"1020": {}}}}}}},
    "evpn": {"vni": {"1020": {"rd": "20:7", "route-target": {"both": {"65000:20": {}}}}}},
    "interface": {"swp1": {"bridge": {"domain": {"br_default": {"vlan": {"20": {}}}}}}},
}


def test_provision(lab, applied):
    out = CumulusBackend().provision_evpn_instance(NODE, dry_run=False, **PROVISION)
    assert out.startswith("OK: EVPN instance 'vlan20'")
    assert applied.trees == [PROVISION_TREE]


def test_provision_dry_run(lab, applied):
    out = CumulusBackend().provision_evpn_instance(NODE, dry_run=True, **PROVISION)
    assert out.startswith("[DRY RUN]") and '"1020"' in out
    assert applied.trees == []


def test_provision_separate_rts_and_auto_rd(lab, applied):
    args = {**PROVISION, "route_distinguisher": "auto", "export_rt": "65001:20"}
    CumulusBackend().provision_evpn_instance(NODE, dry_run=False, **args)
    evpn = applied.trees[0]["evpn"]["vni"]["1020"]
    assert evpn == {"rd": "auto", "route-target": {"import": {"65000:20": {}}, "export": {"65001:20": {}}}}


@pytest.mark.parametrize("change,error", [
    ({"vlan_id": 3800}, "vlan_id"),
    ({"vni": 0}, "vni"),
    ({"interface_name": "eth0"}, "interface_name"),
    ({"export_rt": "auto"}, "export_rt"),
    ({"route_distinguisher": "bogus"}, "route_distinguisher"),
])
def test_provision_validation(lab, applied, change, error):
    out = CumulusBackend().provision_evpn_instance(NODE, dry_run=False, **{**PROVISION, **change})
    assert out.startswith("Validation error(s)") and error in out
    assert applied.trees == []


@pytest.mark.parametrize("change,error", [
    ({"vlan_id": 10}, "VLAN 10 already exists"),
    ({"vni": 1010}, "VNI 1010 is already in use"),
    ({"interface_name": "swp9"}, "interface swp9 not found"),
    ({"interface_name": "swp2"}, "not a member of bridge br_default"),
])
def test_provision_prechecks(lab, applied, change, error):
    out = CumulusBackend().provision_evpn_instance(NODE, dry_run=False, **{**PROVISION, **change})
    assert out.startswith("Error:") and error in out
    assert applied.trees == []


def test_provision_requires_port_and_vlan(lab, applied):
    out = CumulusBackend().provision_evpn_instance(NODE, dry_run=False, **{**PROVISION, "vlan_id": 0})
    assert out.startswith("Error:") and "interface_name" in out


def test_provision_prerequisites(lab, applied):
    lab.replies[(EVPN_PATH, "applied")] = {**EVPN, "state": "disabled"}
    lab.replies[(NVE_PATH, "applied")] = {"state": "disabled"}
    out = CumulusBackend().provision_evpn_instance(NODE, dry_run=False, **PROVISION)
    assert "EVPN is not enabled" in out and "NVE VXLAN is not enabled" in out
    assert applied.trees == []


def test_provision_apply_failure(lab, applied):
    # Issue text captured from mv-nvidia (Phase 0 W7b)
    applied.error = ("NVUE apply invalid: VNI 1010 configured for bridge br_default already in use. "
                     "Verify the configuration of the `vlan-vni-offset` parameter on the conflicting bridges; "
                     "This could result in VNI duplication.")
    out = CumulusBackend().provision_evpn_instance(NODE, dry_run=False, **PROVISION)
    assert out.startswith("Error: provisioning failed on nvidia; no changes were applied.")
    assert "already in use" in out


def _with_vlan20(lab, swp1_vlans=(10, 20)):
    lab.replies[(BRIDGE_PATH, "applied")] = {**BRIDGE, "vlan": {
        **BRIDGE["vlan"], "20": {"vni": {"1020": {"flooding": {"state": "auto"}, "mac-learning": "auto"}}},
    }}
    lab.replies[(EVPN_PATH, "applied")] = {**EVPN, "vni": {
        **EVPN["vni"], "1020": {"rd": "20:7", "route-target": {"both": {"65000:20": {}}, "export": {}, "import": {}}},
    }}
    lab.replies[(INTERFACES_PATH, "applied")] = {**INTERFACES, "swp1": _port(swp1_vlans)}


# The tree applied on mv-nvidia in the Phase 0 write spike (revision 7)
DELETE_TREE = {
    "bridge": {"domain": {"br_default": {"vlan": {"20": None}}}},
    "evpn": {"vni": {"1020": None}},
    "interface": {"swp1": {"bridge": {"domain": {"br_default": {"vlan": {"20": None}}}}}},
}


@pytest.mark.parametrize("name", ["vlan20", "1020"])
def test_delete(lab, applied, name):
    _with_vlan20(lab)
    out = CumulusBackend().delete_evpn_instance(NODE, name, dry_run=False)
    assert out.startswith("OK: EVPN instance 'vlan20' (VLAN=20, VNI=1020) deleted")
    assert applied.trees == [DELETE_TREE]


def test_delete_keeps_only_vlan_on_port(lab, applied):
    _with_vlan20(lab, swp1_vlans=(20,))
    out = CumulusBackend().delete_evpn_instance(NODE, "vlan20", dry_run=True)
    assert "left on swp1 (only VLAN on the port)" in out
    assert '"interface"' not in out
    assert applied.trees == []


def test_delete_not_found(lab, applied):
    assert "not found" in CumulusBackend().delete_evpn_instance(NODE, "vlan99", dry_run=False)
    assert applied.trees == []


# ---------------------------------------------------------------------------
# nvue_apply (revision workflow; states and errors as captured on mv-nvidia)
# ---------------------------------------------------------------------------

@pytest.fixture
def revision_api(monkeypatch):
    """Simulate NVUE's revision endpoints; `states` is the sequence the poll sees."""
    api = {"states": ["apply", "readying", "reloading", "applied_and_saved"], "patch_status": 200,
           "requests": [], "issue": {}}

    def handler(request):
        api["requests"].append((request.method, request.url.path, dict(request.url.params)))
        path = request.url.path
        if request.method == "POST" and path.endswith("/revision"):
            return httpx.Response(200, json={"6": {"state": "pending", "transition": {"issue": {}, "progress": ""}}})
        if request.method == "PATCH" and path.endswith("/nvue_v1/"):
            if api["patch_status"] != 200:
                return httpx.Response(api["patch_status"], json={
                    "detail": "Error: 0 is less than the minimum of 1", "status": 400, "title": "Bad Request"})
            return httpx.Response(200, json={})
        if request.method == "PATCH" and path.endswith("/revision/6"):
            return httpx.Response(200, json={"state": "apply"})
        if request.method == "GET" and path.endswith("/revision/6"):
            state = api["states"].pop(0) if len(api["states"]) > 1 else api["states"][0]
            return httpx.Response(200, json={"state": state, "transition": {"issue": api["issue"], "progress": ""}})
        if request.method == "DELETE" and path.endswith("/revision/6"):
            return httpx.Response(204)
        return httpx.Response(404)

    real_client = httpx.Client
    monkeypatch.setattr(cumulus_client.httpx, "Client",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(cumulus_client.time, "sleep", lambda s: None)
    monkeypatch.setenv("NETMCP_NVIDIA_PASSWORD", "Clab123!")
    return api


def _methods(api):
    return [(m, p.split("/nvue_v1/")[1]) for m, p, _ in api["requests"]]


def test_apply_success(revision_api):
    assert cumulus_client.nvue_apply(NODE, PROVISION_TREE) == "applied_and_saved"
    assert _methods(revision_api)[:3] == [("POST", "revision"), ("PATCH", ""), ("PATCH", "revision/6")]
    assert revision_api["requests"][1][2] == {"rev": "6"}
    assert ("DELETE", "revision/6") not in _methods(revision_api)


def test_apply_patch_rejected_deletes_revision(revision_api):
    revision_api["patch_status"] = 400
    with pytest.raises(RuntimeError, match="less than the minimum of 1"):
        cumulus_client.nvue_apply(NODE, PROVISION_TREE)
    assert _methods(revision_api)[-1] == ("DELETE", "revision/6")


def test_apply_invalid_deletes_revision(revision_api):
    revision_api["states"] = ["apply", "invalid"]
    revision_api["issue"] = {"00000": {
        "code": "duplicate_vni", "severity": "error",
        "message": "VNI 1010 configured for bridge br_default already in use.",
    }}
    with pytest.raises(RuntimeError, match="invalid: VNI 1010 configured"):
        cumulus_client.nvue_apply(NODE, PROVISION_TREE)
    assert _methods(revision_api)[-1] == ("DELETE", "revision/6")


def test_apply_timeout_deletes_revision(revision_api):
    revision_api["states"] = ["reloading"]
    with pytest.raises(RuntimeError, match="did not finish"):
        cumulus_client.nvue_apply(NODE, PROVISION_TREE, timeout=0)
    assert _methods(revision_api)[-1] == ("DELETE", "revision/6")
