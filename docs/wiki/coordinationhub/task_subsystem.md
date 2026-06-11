# coordinationhub/task_subsystem.py

Shared task registry with parent-child hierarchy, dependency tracking, dead-letter queue (DLQ) operations, and terminal-state waiting. Eighth T6.22 extraction from `core_tasks.TaskMixin` (largest surface so far, 11+ public methods); exposed as `engine._task`.

## Key Functions / Classes
- `Task` — subsystem class; three-dep ctor (`connect_fn`, `publish_event_fn`, `hybrid_wait_fn`).
- `Task.create_task(task_id, parent_agent_id, description, depends_on, priority)` — publishes `task.created`.
- `Task.assign_task(task_id, assigned_agent_id)` — publishes `task.assigned`.
- `Task.update_task_status(task_id, status, summary, blocked_by, error)` — status transition; publishes `task.completed` / `task.failed` only when the status actually changed.
- `Task.query_tasks(query_type, ...)` — unified dispatch: `task | child | by_agent | all | subtasks | tree`.
- `Task.create_subtask(task_id, parent_task_id, parent_agent_id, description, depends_on, priority)`.
- `Task.task_failures(action, task_id, limit)` — DLQ dispatch: `retry | list_dead_letter | history`, routed through `retry_task` / `get_dead_letter_tasks` / `get_task_failure_history`.
- `Task.wait_for_task(task_id, timeout_s, ...)` — fast-path terminal check then `_hybrid_wait` on `task.completed` / `task.failed`.
- `Task.get_available_tasks(agent_id)` — unclaimed tasks whose dependencies are satisfied.

## Design Notes
- T6.38: dependency-satisfy and DLQ-record side effects are folded into the `tasks.update_task_status` primitive's transaction — this layer only fans events out after commit and cannot crash between status write and side effect; it does NOT call the Dependency subsystem directly.
- T6.39: `error` is forwarded on every transition (not only `status=='failed'`), so blocked/in_progress diagnostics persist on the task row. T6.40: events fire only when `prior_status != status`.
- T1.13 authz preserved in the primitive: `_VALID_TASK_STATUSES` in `tasks` rejects unknown transitions; this layer just propagates the error payload without duplicating the check.
- T6.37: `query_tasks` deliberately keeps its dispatch-by-string shape; splitting into separate methods is deferred.

## Relationships
Imports: `tasks` (registry primitives), `task_failures` (DLQ primitives)
Imported by: `core.py` (constructed by `CoordinationEngine`)
