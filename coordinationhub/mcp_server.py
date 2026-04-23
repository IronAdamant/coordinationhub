"""HTTP-based MCP server for CoordinationHub — zero external dependencies.

Exposes all CoordinationEngine tool methods over HTTP with JSON request/response.
Endpoints:
    GET  /tools   — list available tool schemas (requires auth)
    GET  /health  — health check (open)
    POST /call    — invoke a tool by name with arguments (requires auth)
    GET  /events  — SSE dashboard stream (requires auth)
    GET  /api/dashboard-data — JSON snapshot (requires auth)
    GET  /        — HTML dashboard (open, token embedded in response)

T2.1: every endpoint except ``/health`` requires a ``Authorization: Bearer
<token>`` header. The token is generated at server startup and exposed on
``CoordinationHubMCPServer.auth_token``; the dashboard HTML embeds it in
a ``<meta name="coordhub-token" ...>`` tag so the browser can read it and
use it for same-origin fetches. Cross-origin requests are rejected via
``Origin`` / ``Host`` header checks (DNS-rebinding defense).
"""

from __future__ import annotations

import json
import logging
import secrets
import select
import socket
import threading
import time as _time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Any

MAX_BODY_BYTES = 1_000_000  # 1 MB — reject oversized requests

from .core import CoordinationEngine
from .dispatch import TOOL_DISPATCH
from .schemas import TOOL_SCHEMAS
from .plugins.dashboard.dashboard import get_dashboard_data, DASHBOARD_HTML

logger = logging.getLogger(__name__)


def dispatch_tool(engine: CoordinationEngine, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a tool call to the appropriate engine method.

    Shared by the HTTP and stdio MCP servers so dispatch logic is not
    duplicated. Raises ``ValueError`` for unknown tools, ``TypeError``
    for invalid arguments.

    T3.5: explicit ``None`` values used to be stripped along with
    missing keys, causing spurious "missing required argument" errors
    for tools whose signature genuinely accepts ``None``
    (e.g. ``report_subagent_spawned(subagent_type: str | None)``).
    Now preserves ``None`` and lets the callee decide.

    Unknown keys are logged at WARNING so callers notice typos (e.g.
    ``agent_ids`` vs ``agent_id``) instead of silently receiving
    "missing required argument" later.
    """
    if tool_name not in TOOL_DISPATCH:
        raise ValueError(
            f"Unknown tool: {tool_name!r}. Available: {sorted(TOOL_DISPATCH)}"
        )
    method_name, allowed_args = TOOL_DISPATCH[tool_name]
    unknown = set(arguments) - set(allowed_args)
    if unknown:
        logger.warning(
            "tool %r called with unknown argument(s) %s; allowed=%s",
            tool_name, sorted(unknown), sorted(allowed_args),
        )
    # T3.5: keep explicit None so the callee can distinguish "field
    # intentionally unset" from "field missing entirely".
    kwargs = {k: v for k, v in arguments.items() if k in allowed_args}
    return getattr(engine, method_name)(**kwargs)


# ------------------------------------------------------------------ #
# HTTP request handler
# ------------------------------------------------------------------ #

class MCPRequestHandler(BaseHTTPRequestHandler):
    """Handles MCP HTTP requests: tool listing, health check, tool calls."""

    # Suppress default stderr logging per request
    def log_message(self, format, *args):  # noqa: A002
        logger.debug(format, *args)

    def _send_json(self, data: dict[str, Any], status: int = 200) -> None:
        """Serialize *data* as JSON and send it with the given status code."""
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: int, message: str) -> None:
        """Send a JSON error response."""
        self._send_json({"error": message}, status=status)

    # -- Auth & origin checks (T2.1) ---------------------------------- #

    def _auth_ok(self) -> bool:
        """Return True iff request carries the expected bearer token.

        When ``self.server.auth_token`` is an empty string or None, auth
        is disabled (legacy / test mode). When set, any non-``/health``
        endpoint must present ``Authorization: Bearer <token>`` matching.
        """
        expected = getattr(self.server, "auth_token", None)
        if not expected:
            return True
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return False
        presented = header[len("Bearer ") :].strip()
        # constant-time compare via secrets.compare_digest
        return secrets.compare_digest(presented, expected)

    def _origin_ok(self) -> bool:
        """Reject cross-origin requests to defend against DNS rebinding.

        When the ``Origin`` header is missing (same-origin browser fetch
        or a command-line client) we accept. When present, it must match
        the server's bound host+port. ``Host`` is also checked: if it's
        a value not on the configured allow-list the request is denied.
        """
        expected = getattr(self.server, "allowed_origins", None)
        if not expected:
            return True
        origin = self.headers.get("Origin")
        if origin is not None and origin not in expected:
            return False
        host = self.headers.get("Host")
        if host is not None:
            allowed_hosts = getattr(self.server, "allowed_hosts", set())
            if allowed_hosts and host not in allowed_hosts:
                return False
        return True

    def _reject_unauthorized(self) -> None:
        # Include a WWW-Authenticate hint so curl users know what's expected.
        body = json.dumps({"error": "unauthorized"}).encode("utf-8")
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.send_header("WWW-Authenticate", 'Bearer realm="coordinationhub"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _reject_bad_origin(self) -> None:
        body = json.dumps({"error": "origin not allowed"}).encode("utf-8")
        self.send_response(403)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- GET endpoints ------------------------------------------------- #

    _OPEN_PATHS = frozenset({"/health", "/"})

    def do_GET(self):  # noqa: N802
        # T2.1: enforce origin + auth before dispatch. /health and / stay
        # open so readiness probes and browser bootstrap work; /  embeds
        # the token in the HTML response.
        if not self._origin_ok():
            return self._reject_bad_origin()
        if self.path not in self._OPEN_PATHS and not self._auth_ok():
            return self._reject_unauthorized()
        if self.path == "/":
            self._serve_dashboard()
        elif self.path == "/tools":
            self._handle_list_tools()
        elif self.path == "/health":
            self._handle_health()
        elif self.path == "/api/dashboard-data":
            self._serve_api_dashboard()
        elif self.path == "/events":
            self._serve_sse_events()
        else:
            self._send_error_json(404, f"Unknown endpoint: {self.path}")

    def _serve_dashboard(self) -> None:
        """GET / — serve the HTML dashboard.

        T2.1: inject the bearer token as a ``<meta>`` tag so the browser
        can read it (``document.querySelector('meta[name=coordhub-token]').content``)
        and include it in ``Authorization: Bearer`` headers for
        ``/api/dashboard-data`` and ``/events`` fetches. Also sets a
        strict Content-Security-Policy so injected `<script>` tags
        (e.g. via a reflected XSS through prompt text) can't call home.
        """
        token = getattr(self.server, "auth_token", "") or ""
        # escape the token for safe HTML injection (alphanumerics only by
        # construction, but be defensive).
        import html as _html
        meta = f'<meta name="coordhub-token" content="{_html.escape(token)}">'
        html_text = DASHBOARD_HTML
        if "<head>" in html_text:
            html_text = html_text.replace("<head>", f"<head>\n{meta}", 1)
        else:
            html_text = meta + html_text
        body = html_text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; connect-src 'self'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_api_dashboard(self) -> None:
        """GET /api/dashboard-data — aggregate all tables as JSON."""
        data = get_dashboard_data(self.server.engine._connect)
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_sse_events(self) -> None:
        """GET /events — Server-Sent Events stream of dashboard data every 5s.

        T2.6: enforces a per-remote-address cap on concurrent SSE
        connections. Without this a single misbehaving page could open
        unlimited streams (each holding a DB connection + thread). Cap
        is tracked on ``self.server._sse_counts`` guarded by
        ``self.server._sse_lock``.
        """
        # T2.6: per-IP cap on concurrent SSE streams.
        client_ip = self.client_address[0]
        max_per_ip = getattr(self.server, "sse_max_per_ip", 4)
        sse_lock = getattr(self.server, "_sse_lock", None)
        sse_counts = getattr(self.server, "_sse_counts", None)
        if sse_lock is not None and sse_counts is not None:
            with sse_lock:
                if sse_counts.get(client_ip, 0) >= max_per_ip:
                    body = json.dumps({"error": "too many SSE connections"}).encode("utf-8")
                    self.send_response(429)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                sse_counts[client_ip] = sse_counts.get(client_ip, 0) + 1
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")  # disable nginx buffering
        self.end_headers()
        # Set a socket timeout so dead connections don't hang forever
        self.request.settimeout(10.0)
        try:
            while True:
                try:
                    data = get_dashboard_data(self.server.engine._connect)
                    payload = json.dumps(data, default=str)
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except (socket.timeout, BrokenPipeError, ConnectionResetError):
                    break  # client disconnected or timed out
                except Exception:
                    break  # any other error — terminate the stream
                _time.sleep(5)
        finally:
            # T2.6: always decrement the per-IP counter so a slow client
            # disconnect doesn't leak a slot.
            if sse_lock is not None and sse_counts is not None:
                with sse_lock:
                    current = sse_counts.get(client_ip, 0) - 1
                    if current <= 0:
                        sse_counts.pop(client_ip, None)
                    else:
                        sse_counts[client_ip] = current

    def _handle_list_tools(self) -> None:
        """GET /tools — return all tool schemas."""
        tools = list(TOOL_SCHEMAS.values())
        self._send_json({"tools": tools})

    def _handle_health(self) -> None:
        """GET /health — simple health check."""
        self._send_json({"status": "ok"})

    # -- POST endpoints ------------------------------------------------ #

    def do_POST(self):  # noqa: N802
        # T2.1: POST endpoints never public.
        if not self._origin_ok():
            return self._reject_bad_origin()
        if not self._auth_ok():
            return self._reject_unauthorized()
        if self.path == "/call":
            self._handle_call()
        else:
            self._send_error_json(404, f"Unknown endpoint: {self.path}")

    def _read_json_body(self) -> dict[str, Any] | None:
        """Read and parse the JSON request body. Returns None on failure."""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            self._send_error_json(400, "Invalid Content-Length header")
            return None
        if content_length <= 0:
            self._send_error_json(400, "Empty request body")
            return None
        if content_length > MAX_BODY_BYTES:
            self._send_error_json(413, f"Request body exceeds {MAX_BODY_BYTES} bytes")
            return None
        raw = self.rfile.read(content_length)
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._send_error_json(400, f"Invalid JSON: {exc}")
            return None

    def _handle_call(self) -> None:
        """POST /call — invoke a tool.

        Expected body: {"tool": "<name>", "arguments": {<kwargs>}}
        """
        body = self._read_json_body()
        if body is None:
            return

        tool_name = body.get("tool")
        if not tool_name:
            self._send_error_json(400, "Missing 'tool' field in request body")
            return

        arguments = body.get("arguments", {})
        if not isinstance(arguments, dict):
            self._send_error_json(400, "'arguments' must be a JSON object")
            return

        try:
            result = dispatch_tool(self.server.engine, tool_name, arguments)
            self._send_json({"result": result})
        except ValueError as exc:
            # ValueError for "Unknown tool: X. Available: [...]" is intentional
            # information (caller needs to know what's available).
            self._send_error_json(404, str(exc))
        except TypeError as exc:
            # Argument-shape problem — tell the caller what's wrong with their
            # inputs but stay within the tool-name + TypeError text (no SQL
            # internals leak via this path).
            self._send_error_json(400, f"Invalid arguments for tool {tool_name!r}: {exc}")
        except Exception as exc:
            # T2.3: log the full exception with a correlation id; send only a
            # generic message + the id to the client so SQLite error text,
            # file paths, or stack fragments don't leak.
            import uuid as _uuid
            correlation_id = _uuid.uuid4().hex[:12]
            logger.exception(
                "tool %s failed [correlation_id=%s]", tool_name, correlation_id,
            )
            self._send_error_json(
                500,
                f"Internal tool execution error (correlation_id={correlation_id})",
            )


# ------------------------------------------------------------------ #
# Threaded HTTP server
# ------------------------------------------------------------------ #

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """HTTPServer that handles each request in a new thread.

    T2.1: carries the bearer ``auth_token`` and the ``allowed_origins`` /
    ``allowed_hosts`` sets used by the handler's auth checks. Both may be
    set to empty/None to disable the check (legacy / test mode).
    """

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type,
        engine: CoordinationEngine,
        auth_token: str | None = None,
        allowed_origins: frozenset[str] | None = None,
        allowed_hosts: frozenset[str] | None = None,
        sse_max_per_ip: int = 4,
    ) -> None:
        self.engine = engine
        self.auth_token = auth_token or ""
        self.allowed_origins = allowed_origins or frozenset()
        self.allowed_hosts = allowed_hosts or frozenset()
        # T2.6: per-IP SSE connection counters (dict[str, int]) guarded
        # by a lock so the handler can increment/decrement safely from
        # multiple daemon threads.
        self.sse_max_per_ip = sse_max_per_ip
        self._sse_lock = threading.Lock()
        self._sse_counts: dict[str, int] = {}
        super().__init__(server_address, handler_class)


# ------------------------------------------------------------------ #
# High-level wrapper
# ------------------------------------------------------------------ #

class CoordinationHubMCPServer:
    """Convenience wrapper around ThreadedHTTPServer + CoordinationEngine.

    Usage::

        server = CoordinationHubMCPServer()
        server.start()          # blocks until Ctrl-C
        # or:
        server.start(blocking=False)  # runs in background thread
        print(server.get_url())
        server.stop()
    """

    def __init__(
        self,
        storage_dir: str | None = None,
        project_root: str | None = None,
        namespace: str = "hub",
        host: str = "127.0.0.1",
        port: int = 9877,
        auth_token: str | None = None,
        disable_auth: bool = False,
    ) -> None:
        self._host = host
        self._port = port
        self._engine = CoordinationEngine(
            storage_dir=Path(storage_dir) if storage_dir else None,
            project_root=Path(project_root) if project_root else None,
            namespace=namespace,
        )
        # T2.1: generate a random bearer token at startup unless the
        # caller explicitly opts out of auth (disable_auth=True) or
        # supplies their own (auth_token="..."). Token is 32 hex chars
        # — cryptographically random via secrets.token_hex.
        if disable_auth:
            self._auth_token = ""
        else:
            self._auth_token = auth_token if auth_token is not None else secrets.token_hex(16)
        self._httpd: ThreadedHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def auth_token(self) -> str:
        """Return the bearer token required by clients, or ''."""
        return self._auth_token

    # -- Public API ---------------------------------------------------- #

    def start(self, blocking: bool = True) -> None:
        """Start the HTTP server.

        Args:
            blocking: If True (default), serve forever on the calling thread.
                      If False, spawn a daemon thread and return immediately.

        The server does NOT register itself as an agent. It is coordination
        middleware, not a swarm participant — registering a self-agent only
        served to keep its own ``last_heartbeat`` fresh, which nothing
        consumed. The previous behaviour also leaked a ghost ``hub.<PID>.0``
        row in the agents table on every SIGKILL/abrupt shutdown.
        """
        self._engine.start()

        # Compute the allowed Origin/Host pair up-front so the handler
        # can reject DNS-rebinding attacks before dispatching. We know
        # the host/port after the HTTPServer is constructed, so compute
        # them lazily inside build_allowed below.
        self._httpd = ThreadedHTTPServer(
            (self._host, self._port), MCPRequestHandler, self._engine,
            auth_token=self._auth_token,
            # Origins/hosts are set below once the port is final.
        )
        # Update port in case 0 was passed (OS-assigned)
        self._port = self._httpd.server_address[1]

        # T2.1: Origin set. The browser sends Origin "http://127.0.0.1:PORT",
        # so allow exactly that. Host header value is "{host}:{port}".
        host_port = f"{self._host}:{self._port}"
        self._httpd.allowed_origins = frozenset({
            f"http://{host_port}",
            f"http://localhost:{self._port}" if self._host == "127.0.0.1" else None,
        } - {None})
        self._httpd.allowed_hosts = frozenset({
            host_port,
            f"localhost:{self._port}" if self._host == "127.0.0.1" else None,
        } - {None})

        if self._auth_token:
            logger.info(
                "CoordinationHub MCP server listening on %s (auth enabled)",
                self.get_url(),
            )
        else:
            logger.warning(
                "CoordinationHub MCP server listening on %s (AUTH DISABLED)",
                self.get_url(),
            )

        if blocking:
            try:
                self._httpd.serve_forever()
            except KeyboardInterrupt:
                self.stop()
        else:
            self._thread = threading.Thread(
                target=self._httpd.serve_forever,
                daemon=True,
                name="coordinationhub-mcp-server",
            )
            self._thread.start()

    def stop(self) -> None:
        """Shut down the server gracefully and close the engine."""
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        self._engine.close()
        self._engine = None

    def get_url(self) -> str:
        """Return the base URL the server is listening on."""
        return f"http://{self._host}:{self._port}"

    def get_port(self) -> int:
        """Return the port the server is listening on."""
        return self._port

    @property
    def engine(self) -> CoordinationEngine:
        """Expose the underlying CoordinationEngine instance."""
        return self._engine
