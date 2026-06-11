# coordinationhub/tasks.py

Task registry primitives for the shared work board: parents create/assign tasks for child agents, with status transitions, subtask hierarchy, dependency-aware availability, and completion summaries that enable compression chains (child writes summary, parent compresses upward).

## Key Functions / Classes
- `create_task(connect, task_id, parent_agent_id, description, depends_on, priority)` — insert a task; description truncated to `MAX_DESCRIPTION` (T6.14).
- `assign_task(connect, task_id, assigned_agent_id)` — assign/reassign; clears the prior assignee's `current_task` and upserts the new assignee's in the same transaction so no window has two claimants (T6.32).
- `update_task_status(connect, task_id, status, summary, blocked_by, error)` — validates against `_VALID_TASK_STATUSES` (`pending`, `in_progress`, `completed`, `blocked`, `failed`, `dead_letter`) and task existence (T1.13); on a real transition to `completed` satisfies dependent `agent_dependencies` rows, on `failed` records to the DLQ — both folded into the same transaction via `_LiveConn` (T6.38, T6.40).
- `_LiveConn` — context manager yielding an existing connection without opening/committing, so connect-callable primitives can piggy-back on an active transaction.
- `create_subtask(connect, task_id, parent_task_id, ...)` — subtask under an existing parent; rejects cycles and >`MAX_TASK_DEPTH` (100) chains via `_would_create_cycle` (T1.14).
- `get_task_tree(connect, root_task_id)` — whole tree in one `WITH RECURSIVE` CTE (T6.36) with depth cap and a Python-side visited set as a second cycle guard.
- `get_task` / `get_child_tasks` / `get_tasks_by_agent` / `get_all_tasks` / `get_subtasks` — straightforward queries; `depends_on` JSON decoded.
- `get_available_tasks(connect, agent_id=None)` — pending tasks whose `depends_on` are all completed and whose assignee has no unsatisfied `agent_dependencies` (T1.12); resolves deps against an in-memory task index (T6.3) and deliberately does NOT auto-satisfy (read-only).
- `suggest_task_assignments(connect)` — available tasks paired with idle agents (no pending/in_progress assignments).

## Design Notes
- Zero internal top-level dependencies beyond `db`/`limits`; `task_failures` is imported function-locally inside `update_task_status` to avoid widening the header for a conditional path (no cycle: task_failures imports nothing from tasks).
- `assign_task` matches the prior assignee's `current_task` by *description*, not task_id — agent_responsibilities stores descriptions; a task_id FK is deferred to the migration bundle (T6.32).
- Status vocabulary CHECK constraint at the DB level is deferred (v22 migration bundle); enforcement lives at the primitive boundary.
- The polling `wait_for_task` primitive was deleted (T6.42) in favour of event-bus-based `TaskMixin.wait_for_task` in core_tasks.py.
- `summary`/`error` truncated to `MAX_SUMMARY`/`MAX_ERROR` at the write boundary.

## Relationships
Imports: `coordinationhub.db` (ConnectFn), `coordinationhub.limits` (MAX_DESCRIPTION, MAX_ERROR, MAX_SUMMARY, truncate), `coordinationhub.task_failures` (function-local)
Imported by: `task_subsystem.py`
