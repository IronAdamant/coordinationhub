# coordinationhub/plugins/dashboard/dashboard_css.py

Embedded CSS blob for the CoordinationHub dashboard (dark theme, grid panels, agent-tree pan/zoom controls, task board, lock/handoff lists). Extracted from `dashboard_html` so each piece stays under the project's 500-LOC module budget.

## Key Functions / Classes
- `DASHBOARD_CSS` — raw string injected between `<style>...</style>` at template-assembly time.

## Design Notes
- Content-only module: no logic, no functions. Styling changes here require no Python changes elsewhere.
- Assembled into `DASHBOARD_HTML` at import time by `dashboard_html.py`.

## Relationships
Imports: none.
Imported by: `coordinationhub/plugins/dashboard/dashboard_html.py`.
