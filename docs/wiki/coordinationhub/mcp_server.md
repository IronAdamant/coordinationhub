# coordinationhub/mcp_server.py

HTTP REST admin / dashboard server. Despite the historical filename, this is NOT the MCP transport (T3.6) — that is `mcp_stdio.py`. This module serves the operator dashboard, a REST tool endpoint for scripts, health checks, and an SSE event stream.

## Key Functions / Classes
- `CoordinationHubAdminServer` — high-level wrapper: owns a `CoordinationEngine`, generates a bearer token (`secrets.token_hex(16)` unless `disable_auth` or caller-supplied), `start(blocking=)` / `stop()` / `get_url()` / `get_port()`; `.engine` raises `RuntimeError` after `stop()` (T7.43).
- `CoordinationHubMCPServer` — deprecated alias for `CoordinationHubAdminServer`.
- `MCPRequestHandler` — endpoints: `GET /` (dashboard HTML, open, embeds token in a `<meta name="coordhub-token">` tag), `GET /health` (open), `GET /tools` (schemas + `tools_version`, T6.13), `GET /api/dashboard-data`, `GET /events` (SSE), `POST /call` (tool dispatch).
- `MCPRequestHandler._serve_sse_events` — event-driven SSE (T3.8): subscribes to the engine event bus, coalesces bursts, 5 s keepalive comment, `Last-Event-ID` replay from `coordination_events`, per-IP connection cap (T2.6, default 4), max lifetime cap (T6.35, default 600 s).
- `ThreadedHTTPServer` — per-request threads; carries `auth_token`, `allowed_origins`/`allowed_hosts`, SSE counters, and an in-flight handler counter (T3.7) so `stop()` can drain handlers before closing the engine.
- `dispatch_tool` — re-export alias from `.dispatch` (T7.42: moved out; kept here so existing imports still work).

## Design Notes
- T2.1 auth: every endpoint except `/health` and `/` requires `Authorization: Bearer <token>` (constant-time compare); cross-origin requests rejected via Origin/Host checks (DNS-rebinding defense). The dashboard page carries a strict CSP.
- T2.3 error hygiene: unexpected tool failures return a generic 500 with a `correlation_id`; full traceback only goes to the server log (no SQLite text/path leaks).
- `MAX_BODY_BYTES` = 1 MB cap on POST bodies (413 beyond).
- The server deliberately does NOT register itself as an agent — it is coordination middleware, not a swarm participant (the old self-registration leaked ghost `hub.<PID>.0` rows on abrupt shutdown).
- `stop()` waits up to 5 s for in-flight handlers, logs a warning if the drain times out, then closes the engine anyway.

## Relationships
Imports: `.core` (`CoordinationEngine`), `.schemas` (`TOOL_SCHEMAS`, `TOOLS_VERSION`), `.plugins.dashboard.dashboard` (`get_dashboard_data`, `DASHBOARD_HTML`), `.dispatch` (`dispatch_tool` re-export).
Imported by: `coordinationhub/__init__.py` (exports `CoordinationHubMCPServer`), `coordinationhub/mcp_stdio.py` (`dispatch_tool`), `coordinationhub/cli_agents.py`, `coordinationhub/cli_sse.py`.
