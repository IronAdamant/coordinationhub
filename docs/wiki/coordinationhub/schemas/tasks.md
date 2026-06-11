# coordinationhub/schemas/tasks.py

Task Registry MCP tool schemas for CoordinationHub. Pure data declarations (no logic) for the shared task registry: creation, assignment, status, hierarchy (subtasks), and availability queries.

## Key Functions / Classes
- `TOOL_SCHEMAS_TASKS: dict[str, dict]` — the only symbol; declares seven tools:
  - `create_task` — new task assigned by `parent_agent_id`; `description` capped at `MAX_DESCRIPTION`; optional `depends_on` list and `priority` (higher first, default 0)
  - `assign_task` — assign a task to an agent
  - `update_task_status` — status enum `pending | in_progress | completed | blocked | failed`; `summary` (≤ `MAX_SUMMARY`) feeds upward compression chains; `error` (≤ `MAX_ERROR`) on failure records to the dead letter queue
  - `query_tasks` — unified query: `task` | `child` | `by_agent` | `all` | `subtasks` | `tree`, each with its own ID parameter
  - `create_subtask` — nested task under `parent_task_id`; inherits parent context
  - `wait_for_task` — poll until terminal state (completed/failed) or `timeout_s` (default 60, `poll_interval_s` default 2)
  - `get_available_tasks` — tasks that are `pending`, unclaimed, and whose `depends_on` are all `completed`; optional `agent_id` filter

## Design Notes
- Pure data declarations — no logic; re-exported by `coordinationhub.schemas` (module docstring).
- T6.11 / T7.44 (docstring): every string field documents its constraint inline (`maxLength` where a cap exists in `coordinationhub.limits`); engine-applied implicit defaults are documented in `description` rather than declared as `default: None` on a non-null type, which validators would read as "null is valid", masking bugs.
- One of only two schema modules (with `intent.py`) importing from `..limits` (`MAX_DESCRIPTION`, `MAX_SUMMARY`, `MAX_ERROR` — all env-overridable, default 10,000).
- Failure path links to `dlq.py`: `status='failed'` + `error` is the DLQ entry point that `task_failures` then manages.

## Relationships
Imports: `coordinationhub.limits` (`MAX_DESCRIPTION`, `MAX_SUMMARY`, `MAX_ERROR`, via `from ..limits import ...`).
Imported by: `coordinationhub/schemas/__init__.py` (merged into the aggregated `TOOL_SCHEMAS` and re-exported in `__all__`).
