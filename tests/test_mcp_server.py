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
    """Yield a started non-blocking server; stop it on teardown.

    T2.1: auth is DISABLED for this fixture so the bulk of the
    endpoint tests can continue to use bare _get / _post. Auth itself
    is covered by dedicated tests in ``TestAuthEnforcement`` below
    which builds its own auth-enabled server.
    """
    srv = CoordinationHubMCPServer(
        storage_dir=str(tmp_path),
        project_root=str(tmp_path),
        host="127.0.0.1",
        port=0,  # OS-assigned
        disable_auth=True,
    )
    srv.start(blocking=False)
    # Wait briefly for the server thread to start listening
    time.sleep(0.1)
    yield srv
    srv.stop()


@pytest.fixture
def auth_server(tmp_path):
    """Auth-enabled server for T2.1 auth tests."""
    srv = CoordinationHubMCPServer(
        storage_dir=str(tmp_path),
        project_root=str(tmp_path),
        host="127.0.0.1",
        port=0,
    )
    srv.start(blocking=False)
    time.sleep(0.1)
    yield srv
    srv.stop()


def _get(url: str, token: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post(url: str, data: dict, token: str | None = None) -> dict:
    body = json.dumps(data).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
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


class TestAuthEnforcement:
    """T2.1: auth-enabled server rejects unauthenticated requests.

    Uses the dedicated ``auth_server`` fixture so the bulk of the
    endpoint tests (which use the ``server`` fixture with auth disabled)
    don't need to thread tokens through.
    """

    def test_missing_token_returns_401(self, auth_server):
        url = f"{auth_server.get_url()}/api/dashboard-data"
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(url, timeout=5)
        assert exc_info.value.code == 401
        assert exc_info.value.headers.get("WWW-Authenticate", "").startswith("Bearer")

    def test_wrong_token_returns_401(self, auth_server):
        req = urllib.request.Request(
            f"{auth_server.get_url()}/api/dashboard-data",
            headers={"Authorization": "Bearer wrong-token"},
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req, timeout=5)
        assert exc_info.value.code == 401

    def test_correct_token_accepted(self, auth_server):
        result = _get(
            f"{auth_server.get_url()}/api/dashboard-data",
            token=auth_server.auth_token,
        )
        assert isinstance(result, dict)

    def test_health_endpoint_stays_open(self, auth_server):
        """Health is a liveness probe — must work without auth."""
        result = _get(f"{auth_server.get_url()}/health")
        assert result == {"status": "ok"}

    def test_dashboard_html_stays_open(self, auth_server):
        """/ serves the HTML bootstrap; browser reads the token from it."""
        with urllib.request.urlopen(f"{auth_server.get_url()}/", timeout=5) as resp:
            html = resp.read().decode("utf-8")
            assert "coordhub-token" in html
            assert auth_server.auth_token in html
            # CSP header set (T2.1 defense against reflected XSS)
            csp = resp.headers.get("Content-Security-Policy")
            assert csp and "default-src 'self'" in csp

    def test_post_call_requires_auth(self, auth_server):
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            _post(
                f"{auth_server.get_url()}/call",
                {"tool": "health", "arguments": {}},
            )
        assert exc_info.value.code == 401

    def test_cross_origin_request_rejected(self, auth_server):
        """DNS-rebinding defense: a request whose Origin header points
        to a different host is rejected with 403 before auth runs.
        """
        url = f"{auth_server.get_url()}/api/dashboard-data"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {auth_server.auth_token}",
                "Origin": "http://evil.example",
            },
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req, timeout=5)
        assert exc_info.value.code == 403

    def test_same_origin_request_accepted(self, auth_server):
        """A request with Origin matching the bound URL is accepted."""
        url = f"{auth_server.get_url()}/api/dashboard-data"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {auth_server.auth_token}",
                "Origin": auth_server.get_url(),
            },
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200


class TestPromptRedaction:
    """T2.1: prompts redacted before storage in agents.current_task."""

    def test_api_keys_redacted(self):
        from coordinationhub.hooks.base import _redact_prompt
        result = _redact_prompt("my key is sk-ant-api03-1234567890abcdefghij please use it")
        assert "sk-ant-api03-1234567890" not in result
        assert "[REDACTED_API_KEY]" in result

    def test_bearer_tokens_redacted(self):
        from coordinationhub.hooks.base import _redact_prompt
        result = _redact_prompt("send header Bearer abc123.xyz789 to the API")
        assert "Bearer [REDACTED]" in result

    def test_github_pat_redacted(self):
        from coordinationhub.hooks.base import _redact_prompt
        result = _redact_prompt("my token ghp_1234567890abcdefghij is public")
        assert "ghp_1234567890abcdefghij" not in result
        assert "[REDACTED_GH_PAT]" in result

    def test_email_redacted(self):
        from coordinationhub.hooks.base import _redact_prompt
        result = _redact_prompt("contact alice@example.com about this")
        assert "alice@example.com" not in result
        assert "[REDACTED_EMAIL]" in result

    def test_long_hex_redacted(self):
        from coordinationhub.hooks.base import _redact_prompt
        digest = "a" * 32
        result = _redact_prompt(f"hash is {digest}")
        assert digest not in result
        assert "[REDACTED_HEX]" in result

    def test_normal_text_passes_through(self):
        from coordinationhub.hooks.base import _redact_prompt
        text = "Please refactor the authentication flow in src/auth.py"
        assert _redact_prompt(text) == text


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
