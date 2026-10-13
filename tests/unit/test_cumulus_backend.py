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
    assert CumulusBackend().get_evpn_instances(NODE) == (
        "Error: get_evpn_instances is not implemented for NOS 'cumulus'."
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
