# coordinationhub/plugins/dashboard/dashboard_html.py

HTML page template for the dashboard; assembles the final `DASHBOARD_HTML` constant at import time from head/body/tail fragments plus the sibling CSS and JS blobs.

## Key Functions / Classes
- `DASHBOARD_HTML` — `_HEAD + DASHBOARD_CSS + _BODY + DASHBOARD_JS + _TAIL`; the complete self-contained page served at `GET /`.
- `_HEAD` / `_BODY` / `_TAIL` — template fragments. `_BODY` defines the panels: Agent Tree (SVG, pan/zoom), Task Registry, Work Intent Board, Handoffs, Agent Dependencies, Active Locks.

## Design Notes
- Content-only module; the three-way split (html/css/js) exists purely to keep each file under the 500-LOC budget.
- The admin server (`mcp_server._serve_dashboard`) injects the bearer-token `<meta name="coordhub-token">` tag into this HTML at serve time (T2.1) — the template itself contains no token placeholder.
- Zero external dependencies: no CDN, no Mermaid/D3.

## Relationships
Imports: `.dashboard_css` (`DASHBOARD_CSS`), `.dashboard_js` (`DASHBOARD_JS`).
Imported by: `coordinationhub/plugins/dashboard/dashboard.py` (re-export at module bottom).
