# coordinationhub/notifications.py

Change-notification storage and retrieval: agents record file-change events that other agents poll (or long-poll) for.

## Key Functions / Classes
- `notify_change(connect, document_path, change_type, agent_id, worktree_root=None)` — insert a `change_notifications` row; returns the inserted `notification_id` so event-bus subscribers can echo a monotonic cursor to waiting pollers (T6.6).
- `get_notifications(connect, since=None, exclude_agent=None, limit=100, since_id=None)` — poll for changes; `since_id` (rowid cursor) is preferred and wins when both args are supplied; the timestamp `since` path is legacy back-compat.
- `prune_notifications(connect, max_age_seconds=None, max_entries=None)` — clean up both `change_notifications` and `coordination_events` by age and/or by oldest-first excess over an entry cap.
- `wait_for_notifications(connect, agent_id, timeout_s=30, poll_interval_s=2, exclude_agent=None)` — long-poll loop: snapshots the latest notification id at entry, then returns new rows (id-ascending, max 100) or `{"notifications": [], "timed_out": True}`.

## Design Notes
- Zero internal dependencies — receives `connect()` from the caller.
- Cursor rationale: rowid is strictly monotonic whereas `created_at` can tie at the millisecond boundary; pre-fix callers compensated with a 1-second backwards window (T6.6).
- `wait_for_notifications` only sees rows inserted *after* it starts (baseline id captured first); `exclude_agent` filters out the caller's own events.
- `prune_notifications` manages two tables (`change_notifications` and `coordination_events`) with the same age/count policy; the returned `pruned` count is the sum.
- Polling sleeps are clamped to remaining timeout.

## Relationships
Imports: `coordinationhub.db` (ConnectFn)
Imported by: `change_subsystem.py`, `locking_subsystem.py`
