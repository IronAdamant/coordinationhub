# coordinationhub/plugins/dashboard/__init__.py

Dashboard plugin entry point: re-exports `DASHBOARD_HTML` and `get_dashboard_data` from `.dashboard` and provides the (no-op) plugin registration hooks.

## Key Functions / Classes
- Re-exports: `DASHBOARD_HTML`, `get_dashboard_data`.
- `register_tools(dispatch_table)` — no-op: the dashboard exposes HTTP endpoints (served by `mcp_server.py`), not MCP tools.
- `register_cli(subparsers)` — no-op.

## Design Notes
- Loaded dynamically by `registry._load_plugin("dashboard")`; the empty hooks exist to satisfy the plugin contract.
- The actual serving (auth, CSP, SSE) lives in `mcp_server.py`; this package only supplies content and data aggregation.

## Relationships
Imports: `.dashboard`.
Imported by: `coordinationhub/plugins/registry.py` (dynamic `__import__`); consumers import the submodule directly (`mcp_server.py` uses `coordinationhub.plugins.dashboard.dashboard`).
