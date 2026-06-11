# coordinationhub/plugins/dashboard/dashboard_js.py

Embedded client-side JavaScript blob for the dashboard: SSE/poll data loop and renderers for the agent tree (SVG with pan/zoom), task table, intents, handoffs, dependencies, and locks.

## Key Functions / Classes
- `DASHBOARD_JS` — raw string injected between `<script>...</script>` at template-assembly time. Inside it: `startSSE()` (EventSource on `/events` with 5 s retry), 5 s polling fallback, `onDashboardData()` fan-out to the per-panel render functions, and agent-tree pan/zoom state.

## Design Notes
- Content-only module: a single IIFE string, no Python logic.
- T7.36 (in-blob comment): SSE retry is 5 s to match the polling fallback so a transient server restart doesn't strand the user on slow-poll.
- Tree pan/zoom state is initialised before any render runs so the first SSE-delivered render never sees `undefined`.

## Relationships
Imports: none.
Imported by: `coordinationhub/plugins/dashboard/dashboard_html.py`.
