# coordinationhub/cli_locks.py

Document locking and coordination CLI commands: lock lifecycle (acquire/release/refresh/status/list/admin), broadcasts with optional acks, formal handoffs, lock waiting, agent awaiting, and direct agent-to-agent messaging.

## Key Functions / Classes
- `_fmt_lock_result(result, document_path)` — shared text renderer for LOCKED/RELEASED/REFRESHED/FAILED outcomes.
- `cmd_acquire_lock(engine, args)` — exclusive/shared lock with TTL, force, region (`--region-start/--region-end`), and retry/backoff knobs; denial → exit code 4.
- `cmd_release_lock` / `cmd_refresh_lock` / `cmd_lock_status` / `cmd_list_locks` — lock lifecycle; `list-locks` supports `--force-refresh` (re-warm in-memory cache from SQLite, T6.33).
- `cmd_admin_locks(engine, args)` — `release_by_agent` / `reap_expired` / `reap_stale` administrative actions.
- `cmd_broadcast(engine, args)` — sibling announcement; `--handoff-targets` turns it into a formal handoff, `--require-ack` into an acked broadcast. Renders three result shapes (handoff / ack-broadcast / plain).
- `cmd_acknowledge_broadcast` / `cmd_wait_for_broadcast_acks` — ack workflow for acked broadcasts.
- `cmd_acknowledge_handoff` / `cmd_complete_handoff` / `cmd_cancel_handoff` / `cmd_get_handoffs` / `cmd_wait_for_handoff` — handoff lifecycle.
- `cmd_wait_for_locks(engine, args)` — poll until the named documents' locks are released or timeout.
- `cmd_await_agent(engine, args)` — wait for an agent to complete.
- `cmd_send_message` / `cmd_get_messages` / `cmd_mark_messages_read` — point-to-point message inbox.

## Design Notes
- T3.17: lock-acquisition denial prints `FAILED: ...` to stderr (so pipelines don't confuse it with success output) and `cmd_acquire_lock` returns exit code 4; success/release/refresh fall through to 0.
- Read paths (`lock-status`, `list-locks`, `get-handoffs`, `get-messages`) use `@_command(replica=True)`; everything mutating uses the writer engine.
- Retry timing flags on acquire-lock are in milliseconds (`--backoff-ms`, `--timeout-ms`); see cli_parser T7.19 note.

## Relationships
Imports: `.cli_utils` (`print_json`, `command`); stdlib `sys`, `typing.Any`.
Imported by: `coordinationhub/cli_commands.py` (re-export hub); tests `tests/test_cli.py` / `tests/test_cli_integration.py` (e.g. `cmd_acquire_lock`).
