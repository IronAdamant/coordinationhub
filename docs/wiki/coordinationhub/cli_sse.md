# coordinationhub/cli_sse.py

CLI command for the SSE dashboard server: `serve-sse` starts the HTTP server with the live dashboard at `/` and the Server-Sent-Events stream at `/events`, opening a browser unless told not to.

## Key Functions / Classes
- `cmd_serve_sse(args)` — constructs a `CoordinationHubMCPServer` (default 127.0.0.1:9898) with the shared storage/project/namespace settings and bearer-auth options, prints the auth token (or local-trust notice), optionally opens the browser, then blocks in `server.start()` until KeyboardInterrupt; `server.stop()` runs in `finally`.

## Design Notes
- T7.12: browser opening runs in a daemon thread that polls `GET /health` (0.3 s timeout, 0.1 s interval) for up to ~5 s before calling `webbrowser.open`, so the browser doesn't hit a "connection refused" race if server startup is slow; gives up silently after the deadline (user can refresh).
- `--no-browser` (dest auto-derived by argparse, T7.8 in cli_parser) suppresses the browser thread entirely — used by `cmd_auto_start_dashboard`'s detached subprocess.
- Auth mirrors `cmd_serve`: token auto-generated unless `--auth-token` pins one; `--no-auth` disables (local-trust only).
- Does not use the `@_command` decorator: it owns the server lifecycle directly rather than performing a single engine call.
- `webbrowser.open` failures are swallowed (headless environments).

## Relationships
Imports: `coordinationhub.mcp_server` (`CoordinationHubMCPServer`, lazily inside the handler); stdlib `webbrowser`, `threading`.
Imported by: `coordinationhub/cli_commands.py` (re-export hub).
