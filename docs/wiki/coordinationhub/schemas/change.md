# coordinationhub/schemas/change.py

Change Awareness MCP tool schemas for CoordinationHub. Pure data declarations (no logic) for recording and polling document-change events between agents.

## Key Functions / Classes
- `TOOL_SCHEMAS_CHANGE: dict[str, dict]` — the only symbol; declares two tools:
  - `notify_change` — record a change event for other agents to poll; requires `document_path`, `change_type` (e.g. 'created'/'modified'/'deleted'), `agent_id`
  - `get_notifications` — poll for notifications since a timestamp; supports long-polling (`timeout_s` > 0 with `poll_interval_s` default 2.0), `exclude_agent`, `limit` default 100, and inline pruning via `prune_max_age_seconds` / `prune_max_entries`

## Design Notes
- Pure data declarations — no logic; re-exported by `coordinationhub.schemas` (module docstring).
- `get_notifications` doubles as a maintenance entry point: if either prune parameter is provided, old data is pruned before results are returned.
- `agent_id` is only meaningful for `get_notifications` when long-polling (`timeout_s > 0`) — it identifies the waiter.
- `notify_change` is meant to be called after every change to a shared document.

## Relationships
Imports: none within the package (only `from __future__ import annotations`).
Imported by: `coordinationhub/schemas/__init__.py` (merged into the aggregated `TOOL_SCHEMAS` and re-exported in `__all__`).
