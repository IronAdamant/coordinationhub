# coordinationhub/dependencies.py

Cross-agent dependency declaration and satisfaction tracking: agent A declares it needs agent B to finish task X before starting Y, then checks/waits for satisfaction.

## Key Functions / Classes
- `declare_dependency(connect, dependent_agent_id, depends_on_agent_id, depends_on_task_id=None, condition="task_completed")` — insert an `agent_dependencies` row; returns `dep_id`.
- `check_dependencies(connect, agent_id)` — return unsatisfied dependencies; auto-satisfies (mutates!) rows whose condition is already met (`task_completed`, `agent_stopped`, `agent_registered`).
- `satisfy_dependency(connect, dep_id)` — mark one dependency satisfied manually.
- `get_blockers(connect, agent_id)` — alias for `check_dependencies`.
- `get_all_dependencies(connect, dependent_agent_id=None)` — list, optionally filtered.
- `satisfy_dependencies_for_task(connect, task_id)` — batch-satisfy every dependency keyed on a task; called automatically by `TaskMixin.update_task_status` on task completion.
- `wait_for_dependency(connect, dep_id, timeout_s=60, poll_interval_s=2)` — blocking poll loop until satisfied or timeout; returns `{"satisfied", "dep_id", "timed_out"}` (plus `reason: "not_found"` for unknown ids).

## Design Notes
- Zero internal dependencies — receives `connect()` from the caller.
- Supported `condition` values: `task_completed` (checks `tasks.status == 'completed'`), `agent_stopped`, `agent_registered` (check `agents.status`). Other strings are never auto-satisfied.
- `check_dependencies` has write side effects (sets `satisfied=1, satisfied_at`); read-only paths (e.g. `tasks.get_available_tasks`) deliberately inline the check without the mutation.
- `wait_for_dependency` sleeps in-process; the final sleep is clamped to remaining timeout.

## Relationships
Imports: `coordinationhub.db` (ConnectFn)
Imported by: `dependency_subsystem.py`
