# coordinationhub/schemas/dlq.py

Dead Letter Queue MCP tool schemas for CoordinationHub. Pure data declaration (no logic) for the single unified task-failure tool.

## Key Functions / Classes
- `TOOL_SCHEMAS_DLQ: dict[str, dict]` — the only symbol; declares one tool:
  - `task_failures` — unified dead-letter queue operations dispatched on `action`:
    - `retry` — resurrect a task from the DLQ (requires `task_id`)
    - `list_dead_letter` — list DLQ tasks (`limit` default 50)
    - `history` — get failure history for a task (requires `task_id`)

## Design Notes
- Pure data declarations — no logic; re-exported by `coordinationhub.schemas` (module docstring).
- Only `action` is in `required`; `task_id` requirements for retry/history are stated in field descriptions rather than enforced with `oneOf`.
- Pairs with `tasks.py`: `update_task_status` with `status='failed'` plus an `error` is what records a task into the dead letter queue that this tool then manages.

## Relationships
Imports: none within the package (only `from __future__ import annotations`).
Imported by: `coordinationhub/schemas/__init__.py` (merged into the aggregated `TOOL_SCHEMAS` and re-exported in `__all__`).
