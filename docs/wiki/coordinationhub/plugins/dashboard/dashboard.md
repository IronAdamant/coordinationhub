# coordinationhub/plugins/dashboard/dashboard.py

Data layer for the zero-dependency web dashboard: aggregates all coordination tables into one JSON-able dict and re-exports the assembled `DASHBOARD_HTML` page (template/CSS/JS live in sibling modules).

## Key Functions / Classes
- `get_dashboard_data(connect)` — returns `{"agents", "tasks", "work_intents", "handoffs", "dependencies", "locks"}` aggregated from `agents` (joined with `agent_responsibilities`, non-stopped only), `tasks`, `work_intent`, `handoffs` (latest 100), `agent_dependencies`, and `document_locks` (joined with `file_ownership`).
- `DASHBOARD_HTML` — re-export from `.dashboard_html` so callers can keep `from .dashboard import DASHBOARD_HTML`.
- `_serve_dashboard(handler)` / `_serve_api_dashboard(handler, engine)` — minimal HTTP helpers (the live admin server in `mcp_server.py` has its own token-injecting versions).
- `ConnectFn` — local type alias for the caller-supplied connection factory.

## Design Notes
- T6.7: every query uses an explicit column list instead of `SELECT *` — adding a table column can't silently leak over the API. `tasks.prompt` is deliberately NOT projected; prompts were dropped from the dashboard payload.
- Sensitive free text (`agents.current_task`) is redacted at write time by `hooks.base._redact_prompt` (T2.1); the explicit projection is the second layer.
- Pure SVG/no-CDN dashboard; this module itself is pure stdlib.
- The `DASHBOARD_HTML` import sits at the bottom of the file (noqa E402) purely to keep the Python logic under the 500-LOC budget while preserving the import path.

## Relationships
Imports: `.dashboard_html` (`DASHBOARD_HTML`).
Imported by: `coordinationhub/mcp_server.py` (`get_dashboard_data`, `DASHBOARD_HTML`), `coordinationhub/plugins/dashboard/__init__.py` (re-exports).
