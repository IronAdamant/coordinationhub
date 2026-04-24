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

    disable_auth = getattr(args, "no_auth", False)
    auth_token = getattr(args, "auth_token", None)
    server = CoordinationHubMCPServer(
        storage_dir=getattr(args, "storage_dir", None),
        project_root=getattr(args, "project_root", None),
        namespace=getattr(args, "namespace", "hub"),
        host=host,
        port=port,
        auth_token=auth_token,
        disable_auth=disable_auth,
    )
    if server.auth_token:
        print(f"Auth token: {server.auth_token}")
        print(f"  Use: Authorization: Bearer {server.auth_token}")
    else:
        print("Auth: DISABLED (local-trust mode)")

    # Open browser in background thread so it doesn't block server start.
    # T7.12: poll /health before opening so the browser doesn't hit a
    # "connection refused" race if server startup runs slow. Give up
    # after ~5 seconds — the user can always refresh.
    if not no_browser:
        def _open_browser():
            import time as _time
            import urllib.error as _url_error
            import urllib.request as _url_request
            dashboard_url = f"http://{host}:{port}/"
            health_url = f"http://{host}:{port}/health"
            deadline = _time.time() + 5.0
            while _time.time() < deadline:
                try:
                    with _url_request.urlopen(health_url, timeout=0.3) as resp:
                        if resp.status == 200:
                            break
                except (_url_error.URLError, ConnectionError, OSError):
                    _time.sleep(0.1)
            try:
                webbrowser.open(dashboard_url)
            except Exception:
                pass  # fail silently if no browser available

        threading.Thread(target=_open_browser, daemon=True).start()

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