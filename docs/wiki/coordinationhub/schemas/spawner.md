# coordinationhub/schemas/spawner.py

Spawner MCP tool schemas for CoordinationHub. Pure data declarations (no logic) for the spawn-intent handshake between a parent agent and the external spawning system, plus graceful child shutdown.

## Key Functions / Classes
- `TOOL_SCHEMAS_SPAWNER: dict[str, dict]` — the only symbol; declares seven tools:
  - `spawn_subagent` — parent registers intent to spawn (creates a pending spawn record correlated via `parent_agent_id`); `source` default `"external"` (e.g. 'stdio_adapter')
  - `report_subagent_spawned` — external system (any IDE/CLI, e.g. stdio_adapter) reports the spawn happened; consumes the pending record and links `child_agent_id`
  - `get_pending_spawns` — list spawn records for a parent (status: pending | registered | expired); `include_consumed` default False
  - `await_subagent_registration` — parent polls until a pending spawn is consumed or `timeout` (default 300 s); returns `timed_out:true` on failure
  - `request_subagent_deregistration` — sets `stop_requested_at` on the child; returns `requested` or `not_found`
  - `is_subagent_stop_requested` — child polls its stop flag; should call `deregister_agent` if set
  - `await_subagent_stopped` — parent polls until child stops or `timeout` (default 30 s); on timeout returns `escalate: True` (caller should force `deregister_agent`)

## Design Notes
- Pure data declarations — no logic; re-exported by `coordinationhub.schemas` (module docstring).
- Spawn protocol: `spawn_subagent` (intent) → external spawn → `report_subagent_spawned` (consume) → `await_subagent_registration` (parent confirms). Graceful stop mirrors it: request → child self-checks → await, with explicit escalation to forced deregistration.
- T2.4 anti-impersonation on `report_subagent_spawned`: optional `caller_agent_id` must equal `parent_agent_id`, rejecting sibling agents claiming another parent's child to hijack the spawner.registered event.
- Working-tree note (2026-06-11, uncommitted): tool descriptions now reference generic external systems ("Any IDE/CLI (stdio_adapter, etc.)"); the previous kimi_cli/cursor adapter examples were removed along with `coordinationhub/hooks/kimi_cli.py` and `hooks/cursor.py`. Documented as it stands in the working tree.

## Relationships
Imports: none within the package (only `from __future__ import annotations`).
Imported by: `coordinationhub/schemas/__init__.py` (merged into the aggregated `TOOL_SCHEMAS` and re-exported in `__all__`).
