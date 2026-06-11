# coordinationhub/cli_tasks.py

CLI commands for the shared task registry: task/subtask creation, assignment, status updates, unified queries, the dead-letter queue, failure history, and task waiting.

## Key Functions / Classes
- `_truncate(text, max_len)` — T7.10 shared helper so the 60/80-char truncations render consistently with a trailing `...` (and tolerate `None`).
- `cmd_create_task(engine, args)` — creates a task with optional `--depends-on` task IDs and `--priority`.
- `cmd_assign_task` / `cmd_update_task_status` — assignment and status transitions (`pending/in_progress/completed/blocked/failed`) with optional summary, blocked-by, and error (error routes to the dead-letter queue).
- `cmd_query_tasks(engine, args)` — unified query (`task`, `child`, `by_agent`, `all`, `subtasks`, `tree`) with the matching ID filter flags (replica-capable).
- `cmd_create_subtask(engine, args)` — creates a subtask under a parent task.
- `cmd_retry_task` / `cmd_dead_letter_queue` / `cmd_task_failure_history` — all routed through `engine.task_failures(action=...)` (`retry`, `list_dead_letter`, `history`).
- `cmd_wait_for_task(engine, args)` — polls until terminal state or timeout (default 60 s / 2 s interval).
- `cmd_get_available_tasks(engine, args)` — tasks with all dependencies satisfied and unclaimed (replica-capable).

## Design Notes
- T3.22 in `cmd_update_task_status`: guard against `args.summary` being `None` (argparse sets unpassed flags to `None`; `len(None)` raised previously).
- Read-only commands (`query-tasks`, `dead-letter-queue`, `task-failure-history`, `get-available-tasks`) use `@_command(replica=True)`; mutating ones use the writer engine.
- Failure-handling subcommands are thin facades over the single `engine.task_failures` dispatcher rather than separate engine methods.

## Relationships
Imports: `.cli_utils` (`print_json`, `command`).
Imported by: `coordinationhub/cli_commands.py` (re-export hub); tests `tests/test_cli.py` / `tests/test_cli_integration.py`.
