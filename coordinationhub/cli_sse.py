"""CLI commands for SSE dashboard server."""

from __future__ import annotations

from .cli_utils import engine_from_args as _engine_from_args, close as _close


def cmd_serve_sse(args):
    """Start the HTTP server with SSE dashboard at /events."""
    from coordinationhub.mcp_server import CoordinationHubMCPServer
    host = getattr(args, "host", "127.0.0.1")
    port = getattr(args, "port", 9878)

    server = CoordinationHubMCPServer(host=host, port=port)
    try:
        print(f"Starting SSE dashboard server on {host}:{port}")
        print(f"Dashboard: http://{host}:{port}/")
        print(f"SSE stream: http://{host}:{port}/events")
        server.start()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.stop()