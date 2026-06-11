# coordinationhub/pending_tasks.py

Tiny FIFO queue correlating `PreToolUse[Agent]` (which carries the full task description) with the later `SubagentStart` (which carries none), so a spawned sub-agent's `current_task` reflects what was actually requested. Keyed by `(session_id, subagent_type)`.

## Key Functions / Classes
- `stash_pending_task(connect, tool_use_id, session_id, subagent_type, description, prompt=None)` — record a pending task from a `PreToolUse[Agent]` event; prunes pending rows older than `_STALE_TTL_SECONDS` (600 s) on every insert.
- `consume_pending_task(connect, session_id, subagent_type)` — pop the oldest unconsumed row for this session + type, mark it `consumed`, return the row dict (or `None`).
- `prune_consumed_pending_tasks(connect, max_age_seconds=600)` — explicit housekeeping for *consumed* rows; the stash path already prunes stale *unconsumed* rows.

## Design Notes
- Zero internal dependencies — receives `connect()` from the caller (same pattern as `notifications.py` / `conflict_log.py`).
- The table (`pending_tasks`) is shared with `spawner.py`, which stores pending spawn records in it under different `task_id`/`source` conventions; here `task_id` = tool_use_id and `scope_id` = session_id.
- T1.9: the `ON CONFLICT(task_id) DO UPDATE` is restricted to rows still in `pending` status — an IDE replaying the same tool_use_id (double-fire or crash/retry) is a silent no-op on an already-consumed row instead of re-queuing it.
- FIFO within `(session_id, subagent_type)`: two Explore agents spawned in a row pair first-with-first, second-with-second.
- `description`/`prompt` are truncated to `MAX_DESCRIPTION`/`MAX_PROMPT` at the write boundary (T6.14).
- Rows older than the TTL are assumed orphaned (Agent tool call errored before SubagentStart fired).

## Relationships
Imports: `coordinationhub.db` (ConnectFn), `coordinationhub.limits` (MAX_DESCRIPTION, MAX_PROMPT, truncate)
Imported by: `hooks/base.py` (function-local imports of `stash_pending_task` and `consume_pending_task`)
