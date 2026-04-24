"""Unit tests for the unified tool dispatcher (dispatch.py).

All tests run without a live lab — backends are mocked and inventory
loading is suppressed via NETMCP_NO_INVENTORY.
"""

import os
import pytest
from unittest.mock import MagicMock
from mcp.server.fastmcp import FastMCP

os.environ.setdefault("NETMCP_NO_INVENTORY", "1")

from netmcp.dispatch import register_unified_tools
from netmcp.inventory import NodeInfo
from netmcp.registry import NotImplementedBackend


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_node(name: str = "r1", nos_type: str = "sros") -> NodeInfo:
    return NodeInfo(name=name, fqdn=f"clab-test-{name}", nos_type=nos_type)


def _make_mock_backend(nos_type: str = "sros") -> MagicMock:
    """Return a MagicMock that looks like a NOSBackend."""
    backend = MagicMock()
    backend.nos_type = nos_type
    backend.get_system_info.return_value = '{"r1": {"version": "24.3"}}'
    backend.get_system_alarms.return_value = '{"r1": []}'
    backend.get_interfaces.return_value = '{"r1": []}'
    backend.get_interface_state.return_value = '{"r1": {}}'
    backend.set_interface_description.return_value = "OK: description set"
    backend.get_bgp_summary.return_value = '{"r1": {}}'
    backend.get_bgp_neighbors.return_value = '{"r1": []}'
    backend.get_bgp_neighbor.return_value = '{"r1": {}}'
    backend.get_bgp_config.return_value = '{"r1": {}}'
    backend.get_evpn_instances.return_value = '{"r1": []}'
    backend.get_isis_adjacencies.return_value = '{"r1": []}'
    backend.get_isis_database.return_value = '{"r1": {}}'
    backend.get_isis_config.return_value = '{"r1": {}}'
    backend.get_ospf_neighbors.return_value = '{"r1": []}'
    backend.get_ospf_config.return_value = '{"r1": {}}'
    backend.get_mpls_lsps.return_value = '{"r1": []}'
    backend.get_sr_sid_table.return_value = '{"r1": []}'
    backend.get_sr_config.return_value = '{"r1": {}}'
    backend.get_vrfs.return_value = '{"r1": []}'
    backend.get_vrf_routes.return_value = '{"r1": []}'
    backend.get_vrf_interfaces.return_value = '{"r1": []}'
    backend.get_log_events.return_value = '{"r1": []}'
    backend.get_log_config.return_value = '{"r1": {}}'
    return backend


@pytest.fixture()
def setup():
    """Returns (mcp, nodes, registry, mock_backend) ready for tool invocation."""
    mcp = FastMCP("test")
    node = _make_node("r1", "sros")
    nodes = {"r1": node}
    mock_backend = _make_mock_backend("sros")
    registry = {"sros": mock_backend}
    register_unified_tools(mcp, nodes, registry)
    tools = {name: tool for name, tool in mcp._tool_manager._tools.items()}
    return tools, node, mock_backend


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def call(tools, name, **kwargs):
    """Synchronously invoke a registered MCP tool by name."""
    import asyncio
    tool = tools[name]
    return asyncio.get_event_loop().run_until_complete(tool.run(kwargs))


# ---------------------------------------------------------------------------
# list_nodes
# ---------------------------------------------------------------------------

class TestListNodes:
    def test_returns_all_nodes(self, setup):
        tools, node, _ = setup
        result = call(tools, "list_nodes")
        assert "r1" in result
        assert "sros" in result
        assert node.fqdn in result

    def test_empty_inventory(self):
        mcp = FastMCP("test")
        register_unified_tools(mcp, {}, {})
        tools = {n: t for n, t in mcp._tool_manager._tools.items()}
        result = call(tools, "list_nodes")
        assert "No nodes" in result


# ---------------------------------------------------------------------------
# Unknown node
# ---------------------------------------------------------------------------

class TestUnknownNode:
    def test_unknown_node_returns_error(self, setup):
        tools, _, _ = setup
        result = call(tools, "get_system_info", node="does_not_exist")
        assert result.startswith("Error:")
        assert "does_not_exist" in result

    def test_unknown_nos_returns_error(self):
        mcp = FastMCP("test")
        node = _make_node("r1", "exotic_nos")
        nodes = {"r1": node}
        register_unified_tools(mcp, nodes, {})  # empty registry
        tools = {n: t for n, t in mcp._tool_manager._tools.items()}
        result = call(tools, "get_system_info", node="r1")
        assert result.startswith("Error:")
        assert "exotic_nos" in result


# ---------------------------------------------------------------------------
# Dispatch routing — each unified tool calls the right backend method
# ---------------------------------------------------------------------------

class TestDispatchRouting:
    def test_get_system_info(self, setup):
        tools, node, mock = setup
        call(tools, "get_system_info", node="r1")
        mock.get_system_info.assert_called_once_with(node)

    def test_get_system_alarms(self, setup):
        tools, node, mock = setup
        call(tools, "get_system_alarms", node="r1")
        mock.get_system_alarms.assert_called_once_with(node)

    def test_get_interfaces(self, setup):
        tools, node, mock = setup
        call(tools, "get_interfaces", node="r1")
        mock.get_interfaces.assert_called_once_with(node)

    def test_get_interface_state(self, setup):
        tools, node, mock = setup
        call(tools, "get_interface_state", node="r1", interface_name="to_spine1")
        mock.get_interface_state.assert_called_once_with(node, "to_spine1")

    def test_set_interface_description(self, setup):
        tools, node, mock = setup
        call(tools, "set_interface_description", node="r1", interface_name="to_spine1", description="uplink", dry_run=False)
        mock.set_interface_description.assert_called_once_with(node, "to_spine1", "uplink", False)

    def test_set_interface_description_dry_run(self, setup):
        tools, node, mock = setup
        call(tools, "set_interface_description", node="r1", interface_name="eth0", description="test", dry_run=True)
        mock.set_interface_description.assert_called_once_with(node, "eth0", "test", True)

    def test_get_bgp_summary(self, setup):
        tools, node, mock = setup
        call(tools, "get_bgp_summary", node="r1")
        mock.get_bgp_summary.assert_called_once_with(node)

    def test_get_bgp_neighbors(self, setup):
        tools, node, mock = setup
        call(tools, "get_bgp_neighbors", node="r1")
        mock.get_bgp_neighbors.assert_called_once_with(node)

    def test_get_bgp_neighbor(self, setup):
        tools, node, mock = setup
        call(tools, "get_bgp_neighbor", node="r1", peer_ip="10.0.0.1")
        mock.get_bgp_neighbor.assert_called_once_with(node, "10.0.0.1")

    def test_get_bgp_config(self, setup):
        tools, node, mock = setup
        call(tools, "get_bgp_config", node="r1")
        mock.get_bgp_config.assert_called_once_with(node)

    def test_get_evpn_instances(self, setup):
        tools, node, mock = setup
        call(tools, "get_evpn_instances", node="r1")
        mock.get_evpn_instances.assert_called_once_with(node)

    def test_get_isis_adjacencies(self, setup):
        tools, node, mock = setup
        call(tools, "get_isis_adjacencies", node="r1")
        mock.get_isis_adjacencies.assert_called_once_with(node)

    def test_get_isis_database(self, setup):
        tools, node, mock = setup
        call(tools, "get_isis_database", node="r1")
        mock.get_isis_database.assert_called_once_with(node)

    def test_get_isis_config(self, setup):
        tools, node, mock = setup
        call(tools, "get_isis_config", node="r1")
        mock.get_isis_config.assert_called_once_with(node)

    def test_get_ospf_neighbors(self, setup):
        tools, node, mock = setup
        call(tools, "get_ospf_neighbors", node="r1")
        mock.get_ospf_neighbors.assert_called_once_with(node)

    def test_get_ospf_config(self, setup):
        tools, node, mock = setup
        call(tools, "get_ospf_config", node="r1")
        mock.get_ospf_config.assert_called_once_with(node)

    def test_get_mpls_lsps(self, setup):
        tools, node, mock = setup
        call(tools, "get_mpls_lsps", node="r1")
        mock.get_mpls_lsps.assert_called_once_with(node)

    def test_get_sr_sid_table(self, setup):
        tools, node, mock = setup
        call(tools, "get_sr_sid_table", node="r1")
        mock.get_sr_sid_table.assert_called_once_with(node)

    def test_get_sr_config(self, setup):
        tools, node, mock = setup
        call(tools, "get_sr_config", node="r1")
        mock.get_sr_config.assert_called_once_with(node)

    def test_get_vrfs(self, setup):
        tools, node, mock = setup
        call(tools, "get_vrfs", node="r1")
        mock.get_vrfs.assert_called_once_with(node)

    def test_get_vrf_routes(self, setup):
        tools, node, mock = setup
        call(tools, "get_vrf_routes", node="r1", vrf_name="PROD")
        mock.get_vrf_routes.assert_called_once_with(node, "PROD")

    def test_get_vrf_interfaces(self, setup):
        tools, node, mock = setup
        call(tools, "get_vrf_interfaces", node="r1", vrf_name="PROD")
        mock.get_vrf_interfaces.assert_called_once_with(node, "PROD")

    def test_get_log_events_defaults(self, setup):
        tools, node, mock = setup
        call(tools, "get_log_events", node="r1")
        mock.get_log_events.assert_called_once_with(node, 50, "")

    def test_get_log_events_with_args(self, setup):
        tools, node, mock = setup
        call(tools, "get_log_events", node="r1", count=10, severity="major")
        mock.get_log_events.assert_called_once_with(node, 10, "major")

    def test_get_log_config(self, setup):
        tools, node, mock = setup
        call(tools, "get_log_config", node="r1")
        mock.get_log_config.assert_called_once_with(node)


# ---------------------------------------------------------------------------
# NotImplementedBackend returns clean error strings
# ---------------------------------------------------------------------------

class TestNotImplementedBackend:
    def test_all_methods_return_error_strings(self):
        backend = NotImplementedBackend("exotic")
        node = _make_node("r1", "exotic")
        for method_name in [
            "get_system_info", "get_system_alarms",
            "get_interfaces", "get_interface_state",
            "get_bgp_summary", "get_bgp_neighbors", "get_bgp_neighbor", "get_bgp_config",
            "get_isis_adjacencies", "get_isis_database", "get_isis_config",
            "get_ospf_neighbors", "get_ospf_config",
            "get_mpls_lsps", "get_sr_sid_table", "get_sr_config",
            "get_vrfs", "get_vrf_routes", "get_vrf_interfaces",
            "get_evpn_instances",
            "get_log_events", "get_log_config",
        ]:
            method = getattr(backend, method_name)
            # call with minimal positional args beyond node
            if method_name in ("get_interface_state",):
                result = method(node, "eth0")
            elif method_name in ("get_bgp_neighbor",):
                result = method(node, "10.0.0.1")
            elif method_name in ("get_vrf_routes", "get_vrf_interfaces"):
                result = method(node, "default")
            elif method_name == "set_interface_description":
                result = method(node, "eth0", "desc", False)
            elif method_name == "get_log_events":
                result = method(node, 50, "")
            else:
                result = method(node)
            assert result.startswith("Error:"), f"{method_name} did not return an error string"
            assert "exotic" in result, f"{method_name} did not mention the NOS type"
