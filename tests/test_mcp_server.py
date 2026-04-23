"""Tests for the HTTP MCP server."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import urllib.error
import urllib.request

import pytest

from coordinationhub.mcp_server import (
    CoordinationHubMCPServer,
    MAX_BODY_BYTES,
)


@pytest.fixture
def server(tmp_path):
    """Yield a started non-blocking server; stop it on teardown."""
    srv = CoordinationHubMCPServer(
        storage_dir=str(tmp_path),
        project_root=str(tmp_path),
        host="127.0.0.1",
        port=0,  # OS-assigned
    )
    srv.start(blocking=False)
    # Wait briefly for the server thread to start listening
    time.sleep(0.1)
    yield srv
    srv.stop()


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post(url: str, data: dict) -> dict:
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


class TestHealth:
    def test_health_returns_ok(self, server):
        result = _get(f"{server.get_url()}/health")
        assert result == {"status": "ok"}


class TestListTools:
    def test_list_tools_returns_tools_array(self, server):
        result = _get(f"{server.get_url()}/tools")
        assert "tools" in result
        assert isinstance(result["tools"], list)
        assert len(result["tools"]) > 0


class TestCall:
    def test_call_unknown_tool_returns_404(self, server):
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            _post(
                f"{server.get_url()}/call",
                {"tool": "no_such_tool", "arguments": {}},
            )
        assert exc_info.value.code == 404

    def test_call_missing_tool_field_returns_400(self, server):
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            _post(f"{server.get_url()}/call", {"arguments": {}})
        assert exc_info.value.code == 400

    def test_call_invalid_json_returns_400(self, server):
        body = b"not json"
        req = urllib.request.Request(
            f"{server.get_url()}/call",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req, timeout=5)
        assert exc_info.value.code == 400

    def test_call_register_agent_round_trip(self, server):
        result = _post(
            f"{server.get_url()}/call",
            {
                "tool": "register_agent",
                "arguments": {
                    "agent_id": "test.agent.1",
                    "parent_id": None,
                },
            },
        )
        assert "result" in result
        assert result["result"]["agent_id"] == "test.agent.1"

    def test_call_acquire_and_release_lock(self, server):
        _post(
            f"{server.get_url()}/call",
            {"tool": "register_agent", "arguments": {"agent_id": "lock.agent"}},
        )
        acquire = _post(
            f"{server.get_url()}/call",
            {
                "tool": "acquire_lock",
                "arguments": {
                    "document_path": "/tmp/test.txt",
                    "agent_id": "lock.agent",
                    "lock_type": "exclusive",
                    "ttl": 60,
                },
            },
        )
        assert acquire["result"]["acquired"] is True

        release = _post(
            f"{server.get_url()}/call",
            {
                "tool": "release_lock",
                "arguments": {
                    "document_path": "/tmp/test.txt",
                    "agent_id": "lock.agent",
                },
            },
        )
        assert release["result"]["released"] is True

    def test_call_empty_request_body_returns_400(self, server):
        req = urllib.request.Request(
            f"{server.get_url()}/call",
            data=b"",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req, timeout=5)
        assert exc_info.value.code == 400

    def test_call_oversized_body_returns_413(self, server):
        # Send a small body but claim a huge Content-Length so the server
        # rejects with 413 before reading any payload — avoids BrokenPipe.
        body = b'{}'
        req = urllib.request.Request(
            f"{server.get_url()}/call",
            data=body,
            headers={"Content-Type": "application/json", "Content-Length": str(MAX_BODY_BYTES + 1)},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req, timeout=5)
        assert exc_info.value.code == 413

    def test_internal_error_does_not_leak_exception_text(self, server, monkeypatch):
        """T2.3: unexpected exceptions are wrapped in a generic 500 with a
        correlation id. Pre-fix the raw ``str(exc)`` leaked SQLite error
        strings, file paths, and stack fragments to the HTTP client.
        """
        from coordinationhub import mcp_server as _mcp

        sentinel = "SECRET_SQLITE_PATH_/var/private/db"

        def _boom(engine, tool_name, arguments):
            raise RuntimeError(sentinel)

        monkeypatch.setattr(_mcp, "dispatch_tool", _boom)

        with pytest.raises(urllib.error.HTTPError) as exc_info:
            _post(
                f"{server.get_url()}/call",
                {"tool": "register_agent", "arguments": {"agent_id": "a"}},
            )
        assert exc_info.value.code == 500
        body = exc_info.value.read().decode("utf-8")
        assert sentinel not in body, (
            f"raw exception text leaked to client: {body}"
        )
        assert "correlation_id" in body
        assert "Internal tool execution error" in body


class TestDashboard:
    def test_dashboard_html_served_at_root(self, server):
        with urllib.request.urlopen(f"{server.get_url()}/", timeout=5) as resp:
            html = resp.read().decode("utf-8")
        assert "<html" in html.lower() or "<!doctype" in html.lower()

    def test_api_dashboard_data_returns_json(self, server):
        result = _get(f"{server.get_url()}/api/dashboard-data")
        assert isinstance(result, dict)


class TestSSE:
    def test_sse_stream_returns_event_stream_header(self, server):
        with urllib.request.urlopen(f"{server.get_url()}/events", timeout=5) as resp:
            assert resp.headers.get("Content-Type") == "text/event-stream"
            # Read a few bytes to confirm data is flowing
            chunk = resp.read(20)
            assert chunk.startswith(b"data: ")


class TestServerLifecycle:
    def test_server_port_assignment(self, server):
        assert server.get_port() > 0
        assert server.get_url() == f"http://127.0.0.1:{server.get_port()}"

    def test_server_stop_releases_port(self, tmp_path):
        srv = CoordinationHubMCPServer(
            storage_dir=str(tmp_path),
            host="127.0.0.1",
            port=0,
        )
        srv.start(blocking=False)
        time.sleep(0.1)
        port = srv.get_port()
        srv.stop()
        # After stop, a new server should be able to bind the same port
        srv2 = CoordinationHubMCPServer(
            storage_dir=str(tmp_path),
            host="127.0.0.1",
            port=port,
        )
        srv2.start(blocking=False)
        time.sleep(0.1)
        assert srv2.get_port() == port
        srv2.stop()
