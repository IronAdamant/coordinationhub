"""CLI commands for SSE dashboard server."""

from __future__ import annotations

import webbrowser
import threading


def cmd_serve_sse(args):
    """Start the HTTP server with SSE dashboard at /events and open browser."""
    from coordinationhub.mcp_server import CoordinationHubMCPServer
    host = getattr(args, "host", "127.0.0.1")
    port = getattr(args, "port", 9898)
    no_browser = getattr(args, "no_browser", False)

    server = CoordinationHubMCPServer(host=host, port=port)

    # Open browser in background thread so it doesn't block server start
    if not no_browser:
        def _open_browser():
            dashboard_url = f"http://{host}:{port}/"
            try:
                webbrowser.open(dashboard_url)
            except Exception:
                pass  # fail silently if no browser available

        threading.Timer(1.0, _open_browser).start()

    print(f"Starting SSE dashboard server on {host}:{port}")
    print(f"Dashboard: http://{host}:{port}/")
    print(f"SSE stream: http://{host}:{port}/events")
    print("Press Ctrl+C to stop")
    try:
        server.start()  # blocking — runs until KeyboardInterrupt
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        server.stop()