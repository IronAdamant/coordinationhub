"""HTTP-based MCP server for CoordinationHub — zero external dependencies.

Exposes all CoordinationEngine tool methods over HTTP with JSON request/response.
Endpoints:
    GET  /tools   — list available tool schemas
    GET  /health  — health check
    POST /call    — invoke a tool by name with arguments
"""

from __future__ import annotations

import json
import logging
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
    """
    if tool_name not in TOOL_DISPATCH:
        raise ValueError(
            f"Unknown tool: {tool_name!r}. Available: {sorted(TOOL_DISPATCH)}"
        )
    method_name, allowed_args = TOOL_DISPATCH[tool_name]
    kwargs = {k: v for k, v in arguments.items() if k in allowed_args and v is not None}
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

    # -- GET endpoints ------------------------------------------------- #

    def do_GET(self):  # noqa: N802
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
        """GET / — serve the HTML dashboard."""
        body = DASHBOARD_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
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
        """GET /events — Server-Sent Events stream of dashboard data every 5s."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")  # disable nginx buffering
        self.end_headers()
        # Set a socket timeout so dead connections don't hang forever
        self.request.settimeout(10.0)
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

    def _handle_list_tools(self) -> None:
        """GET /tools — return all tool schemas."""
        tools = list(TOOL_SCHEMAS.values())
        self._send_json({"tools": tools})

    def _handle_health(self) -> None:
        """GET /health — simple health check."""
        self._send_json({"status": "ok"})

    # -- POST endpoints ------------------------------------------------ #

    def do_POST(self):  # noqa: N802
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
            self._send_error_json(404, str(exc))
        except TypeError as exc:
            self._send_error_json(400, f"Invalid arguments for tool {tool_name!r}: {exc}")
        except Exception as exc:
            logger.exception("Error executing tool %s", tool_name)
            self._send_error_json(500, f"Tool execution error: {exc}")


# ------------------------------------------------------------------ #
# Threaded HTTP server
# ------------------------------------------------------------------ #

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """HTTPServer that handles each request in a new thread."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], handler_class: type, engine: CoordinationEngine) -> None:
        self.engine = engine
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
    ) -> None:
        self._host = host
        self._port = port
        self._engine = CoordinationEngine(
            storage_dir=Path(storage_dir) if storage_dir else None,
            project_root=Path(project_root) if project_root else None,
            namespace=namespace,
        )
        self._httpd: ThreadedHTTPServer | None = None
        self._thread: threading.Thread | None = None

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

        self._httpd = ThreadedHTTPServer(
            (self._host, self._port), MCPRequestHandler, self._engine,
        )
        # Update port in case 0 was passed (OS-assigned)
        self._port = self._httpd.server_address[1]

        logger.info("CoordinationHub MCP server listening on %s", self.get_url())

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
