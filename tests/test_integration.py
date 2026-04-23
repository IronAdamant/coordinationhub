"""Integration tests for the HTTP MCP server transport layer.

Spins up a real HTTP server on a random port, fires actual HTTP requests,
and validates end-to-end tool dispatch — catching dispatch mis-wiring,
JSON serialization bugs, and DB lifecycle issues that unit tests can't.
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
from http.client import HTTPConnection
from typing import Any
from urllib.request import urlopen
from urllib.error import HTTPError

import pytest

from coordinationhub.core import CoordinationEngine
from coordinationhub.mcp_server import CoordinationHubMCPServer


class _Client:
    """Minimal HTTP client for the MCP server."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.conn = HTTPConnection(host, port, timeout=10)

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps({"tool": tool, "arguments": arguments}).encode()
        self.conn.request("POST", "/call", body=body, headers={"Content-Type": "application/json"})
        resp = self.conn.getresponse()
        data = json.loads(resp.read().decode())
        # Server wraps result in {"result": ...}
        return data.get("result", data)

    def get_tools(self) -> list[dict[str, Any]]:
        self.conn.request("GET", "/tools")
        resp = self.conn.getresponse()
        data = json.loads(resp.read().decode())
        return data.get("tools", data) if isinstance(data, dict) else data

    def get_health(self) -> int:
        with urlopen(f"http://{self.host}:{self.port}/health") as r:
            return r.code

    def close(self):
        self.conn.close()


class _TestServer:
    """Bundles engine + server + client for a test."""

    def __init__(self, storage_dir: str):
        self.storage_dir = storage_dir
        self.engine = CoordinationEngine(storage_dir=storage_dir)
        self.engine.start()
        self.agent_id = self.engine.generate_agent_id()
        self.engine.register_agent(self.agent_id)
        self.server = CoordinationHubMCPServer(
            storage_dir=storage_dir,
            project_root=None,
            namespace="hub",
            host="127.0.0.1",
            port=0,  # auto-assign
            disable_auth=True,  # T2.1: this test uses raw HTTP without tokens
        )
        self.server.start(blocking=False)
        self.port = self.server.get_port()
        self.client = _Client("127.0.0.1", self.port)

    def close(self):
        self.client.close()
        self.server.stop()
        self.engine.close()


@pytest.fixture
def test_server(tmp_path):
    """Spin up a real HTTP MCP server for integration testing."""
    srv = _TestServer(str(tmp_path))
    yield srv
    srv.close()


class TestHTTPTransport:
    """End-to-end HTTP transport tests."""

    def test_health_endpoint(self, test_server):
        """GET /health returns 200."""
        assert test_server.client.get_health() == 200

    def test_get_tools_returns_all_tools(self, test_server):
        """GET /tools lists every tool in TOOL_SCHEMAS."""
        from coordinationhub.dispatch import TOOL_DISPATCH
        tools = test_server.client.get_tools()
        assert len(tools) == len(TOOL_DISPATCH)
        # Each tool dict has a "description" and "parameters" key; tool name is the dict key
        for t in tools:
            assert "description" in t
            assert "parameters" in t

    def test_register_agent_via_http(self, test_server):
        """POST /call with register_agent returns a valid context bundle."""
        new_id = "hub.test.integration.1"
        result = test_server.client.call("register_agent", {"agent_id": new_id})
        assert "agent_id" in result
        assert result["agent_id"] == new_id
        assert "worktree_root" in result

    def test_unknown_tool_returns_error(self, test_server):
        """Unknown tool name returns an error, not a traceback."""
        result = test_server.client.call("not_a_real_tool", {})
        # Server returns {"error": "Unknown tool: ..."} or similar
        assert "error" in result or "Unknown tool" in str(result)

    def test_heartbeat_via_http(self, test_server):
        """heartbeat updates last_heartbeat without error."""
        result = test_server.client.call("heartbeat", {"agent_id": test_server.agent_id})
        assert result.get("updated") is True

    def test_acquire_lock_via_http(self, test_server):
        """acquire_lock returns acquired=True for a free document."""
        result = test_server.client.call("acquire_lock", {
            "document_path": "test_file.txt",
            "agent_id": test_server.agent_id,
            "lock_type": "exclusive",
        })
        assert result.get("acquired") is True
        assert result.get("locked_by") == test_server.agent_id

    def test_server_does_not_register_self_as_agent(self, test_server):
        """The HTTP server is coordination middleware, not a swarm participant.

        Regression: before v0.7.4, ``CoordinationHubMCPServer.start`` called
        ``register_agent`` for itself and ran a heartbeat thread to keep the
        row alive. Nothing consumed the server-agent, and a SIGKILL of the
        server left a ghost ``hub.<PID>.0`` row that polluted the agent tree
        until the 600s stale-timeout reap.
        """
        result = test_server.client.call("list_agents", {"active_only": False})
        agents = result.get("agents", [])
        ids = {a["agent_id"] for a in agents}
        # Only the explicitly-registered test agent should be present
        assert ids == {test_server.agent_id}, (
            f"Server registered an unexpected self-agent. Got: {ids}"
        )

    def test_release_lock_via_http(self, test_server):
        """release_lock returns released=True after acquiring."""
        test_server.client.call("acquire_lock", {
            "document_path": "test_file.txt",
            "agent_id": test_server.agent_id,
        })
        result = test_server.client.call("release_lock", {
            "document_path": "test_file.txt",
            "agent_id": test_server.agent_id,
        })
        assert result.get("released") is True

    def test_get_lock_status_after_acquire(self, test_server):
        """get_lock_status shows locked=True for an acquired lock."""
        test_server.client.call("acquire_lock", {
            "document_path": "test_file.txt",
            "agent_id": test_server.agent_id,
        })
        result = test_server.client.call("get_lock_status", {
            "document_path": "test_file.txt",
        })
        assert result.get("locked") is True
        assert result.get("locked_by") == test_server.agent_id

    def test_notify_change_via_http(self, test_server):
        """notify_change records the change and get_notifications retrieves it."""
        test_server.client.call("notify_change", {
            "document_path": "src/app.py",
            "change_type": "modified",
            "agent_id": test_server.agent_id,
        })
        result = test_server.client.call("get_notifications", {
            "exclude_agent": test_server.agent_id,
        })
        assert isinstance(result.get("notifications"), list)

    def test_status_via_http(self, test_server):
        """status returns registered_agents, active_agents, active_locks, tools count."""
        from coordinationhub.dispatch import TOOL_DISPATCH
        result = test_server.client.call("status", {})
        assert "registered_agents" in result
        assert "active_agents" in result
        assert "active_locks" in result
        assert result.get("tools") == len(TOOL_DISPATCH)

    def test_list_agents_via_http(self, test_server):
        """list_agents returns our registered agent."""
        result = test_server.client.call("list_agents", {"active_only": False})
        agents = result.get("agents", [])
        ids = {a["agent_id"] for a in agents}
        assert test_server.agent_id in ids

    def test_get_agent_relations_lineage_via_http(self, test_server):
        """get_agent_relations with mode='lineage' returns ancestors and descendants."""
        result = test_server.client.call("get_agent_relations", {
            "agent_id": test_server.agent_id,
        })
        assert "ancestors" in result
        assert "descendants" in result

    def test_update_agent_status_via_http(self, test_server):
        """update_agent_status stores current_task and get_agent_status retrieves it."""
        test_server.client.call("update_agent_status", {
            "agent_id": test_server.agent_id,
            "current_task": "Working on integration tests",
        })
        result = test_server.client.call("get_agent_status", {
            "agent_id": test_server.agent_id,
        })
        assert result.get("current_task") == "Working on integration tests"

    def test_run_assessment_missing_suite_returns_error(self, test_server):
        """run_assessment with a missing file returns an error."""
        result = test_server.client.call("run_assessment", {
            "suite_path": "/nonexistent/path/assessment.json",
            "format": "json",
        })
        assert "error" in result

    def test_tool_unknown_kwargs_dropped(self, test_server):
        """Extra/unknown kwargs are silently dropped, not propagated as errors."""
        result = test_server.client.call("register_agent", {
            "agent_id": "hub.test.integration.2",
            "parent_id": None,
            "this_is_not_a_real_parameter": "should_be_ignored",
        })
        assert result.get("agent_id") == "hub.test.integration.2"
