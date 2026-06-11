# coordinationhub/task_failures.py

Task failure tracking and dead letter queue: failures increment an attempt counter, and after `max_retries` attempts the task moves to `dead_letter` status. DLQ tasks can be retried by resetting them to pending.

## Key Functions / Classes
- `record_task_failure(connect, task_id, error=None, max_retries=3)` — record a failure; SELECT + UPDATE/INSERT run inside one `BEGIN IMMEDIATE` so concurrent recordings can't race the attempt counter (T1.7); rows in `retried` status are ignored when computing the next attempt so a DLQ retry resets the budget (T1.8).
- `get_dead_letter_tasks(connect, limit=50)` — list DLQ rows; each carries an `orphan` boolean (no matching `tasks` row) so callers don't retry records with nothing to run (T6.41).
- `retry_from_dead_letter(connect, task_id)` — mark prior attempts `retried` and reset the task to `pending`; if the task row is missing, the DLQ entry is flipped to `orphan` and `{"retried": False, "reason": "task_row_missing"}` returned (T6.41).
- `get_task_failure_history(connect, task_id)` — all failure records for a task, attempt-ascending.

## Design Notes
- Zero internal dependencies — receives `connect()` from the caller.
- Stored `max_retries` on an existing row is authoritative; the call-site default only applies on first insert (T1.7).
- A first failure with `max_retries <= 1` goes straight to `dead_letter`.
- `BEGIN IMMEDIATE` is attempted but tolerated to fail (`began = False`) when the caller's connection shim already opened a transaction (test_db_safety dual-shape pattern) — `tasks.update_task_status` exploits this via `_LiveConn` to fold DLQ recording into its own transaction (T6.38).
- Pre-T1.8 bug to remember: a `retried` row matched `ORDER BY attempt DESC LIMIT 1`, so the next failure could immediately re-enter dead_letter (retry budget effectively zero).

## Relationships
Imports: `coordinationhub.db` (ConnectFn)
Imported by: `tasks.py` (function-local, in `update_task_status`), `task_subsystem.py`
