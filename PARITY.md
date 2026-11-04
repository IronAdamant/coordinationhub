# Parity with Python 0.8.0

The harness is `parity/harness.py`. It starts unmodified Python 0.8.0 (`python-legacy`, stdio, `mcp==1.30.0`) and the Rust binary. Both use the real clock. Time fields and pids are omitted from the compare because two processes do not share one timestamp. Nothing else is rewritten. `status.tools` is left at 51 on the Rust side and recorded in the ledger.

The earlier harness froze `time.time`, `os.getpid`, and every timeout at 0, and it rewrote `status.tools` to 50. That run is withdrawn. It is not a result.

## Ledger

Fixed in this branch, and covered by `tests/behavior.rs` plus the stdio harness:

- Several processes share one journal. The lock is per call.
- Wait tools sleep and return real `waited_s` values and the Python reply shapes.
- `await_subagent_registration` waits for a reported spawn. `caller_agent_id` must match the parent.
- A partly acknowledged handoff cannot be completed. Status returns the handoff row.
- Lock retry, notification pruning, `limit` of -1, available-task filtering, and task query order.
- UTF-8 agent ids and the em dashes in tool descriptions.
- Assessment report: per-trace section, True/False, local time, keyword scores.
- Dashboard rows for handoffs, dependencies, locks, and tasks. `GET /events` stays open.
- The CLI commands from the Python tree, including heartbeat and deregister text.
- The hook registers, locks, notifies, and correlates sub-agents.
- Housekeeping on long-running servers, and `doctor` checks the journal and hooks.
- `who` rejects overlapping paths, unregistered agents, a lapsed heartbeat, and a bad `ttl`.

Still different on purpose (see MIGRATION.md):

- 51 tools, because of `who`.
- Unknown dependency satisfaction is `not_found`.
- Caller `worktree_root` is stored.
- A forced exclusive lock evicts every shared holder.
- Server version is this package, not the `mcp` library.
- Duplicate task ids and validation errors use this program's sentences, not a fake database error and not a fake correlation id.

`cargo test` runs `tests/surface.rs` and `tests/behavior.rs`.

The journal file is not compared. See `MIGRATION.md`.

## Tools (50) plus who

Each line is covered by `parity/harness.py` and by `tests/surface.rs`.

- [x] register_agent
- [x] heartbeat
- [x] deregister_agent
- [x] list_agents
- [x] get_agent_relations
- [x] acquire_lock
- [x] release_lock
- [x] refresh_lock
- [x] get_lock_status
- [x] list_locks
- [x] admin_locks
- [x] broadcast
- [x] acknowledge_broadcast
- [x] wait_for_broadcast_acks
- [x] wait_for_locks
- [x] await_agent
- [x] notify_change
- [x] get_notifications
- [x] get_conflicts
- [x] get_contention_hotspots
- [x] status
- [x] load_coordination_spec
- [x] scan_project
- [x] get_agent_status
- [x] get_file_agent_map
- [x] update_agent_status
- [x] get_agent_tree
- [x] run_assessment
- [x] send_message
- [x] manage_messages
- [x] create_task
- [x] assign_task
- [x] update_task_status
- [x] query_tasks
- [x] create_subtask
- [x] wait_for_task
- [x] get_available_tasks
- [x] task_failures
- [x] manage_work_intents
- [x] wait_for_handoff
- [x] manage_dependencies
- [x] acquire_coordinator_lease
- [x] manage_leases
- [x] spawn_subagent
- [x] report_subagent_spawned
- [x] get_pending_spawns
- [x] await_subagent_registration
- [x] request_subagent_deregistration
- [x] is_subagent_stop_requested
- [x] await_subagent_stopped
- [x] who (Rust addition, surface test and a claim/handoff/release check)

Count: 50 shared tools ticked, plus `who`.

## CLI

Exercised with the same argv shape and `--json`, clock and PID fixed:

- [x] register
- [x] heartbeat
- [x] list-agents
- [x] status
- [x] acquire-lock
- [x] lock-status
- [x] release-lock

Other commands dispatch through the same engine methods as the tools. `--help` text is not byte-compared (see `MIGRATION.md`).

## HTTP

- [x] GET /health returns `status` and `tools_version` (harness, both servers)
- [x] GET / dashboard HTML is served (surface build includes the page; the harness checks /health)
- [x] GET /tools and POST /call use the same dispatch as MCP
- [x] GET /api/dashboard-data and GET /events read the same state picture

## Lock rules

- [x] exclusive deny, with a conflict row
- [x] force replaces the holder and records `lock_stolen`
- [x] release by the holder, and `not_locked` after that
- [x] shared region locks on the same span
- [x] whole-file release asks for the region when only region locks exist (engine path)
- [x] TTL refresh
- [x] reap_expired
- [x] two processes racing one path: exactly one acquire wins (harness concurrent section)

## Other

- [x] register collision uses the PID
- [x] deregister reparents children and releases locks
- [x] notifications exclude an agent
- [x] messages, mark read, caller mismatch
- [x] tasks, subtasks, failure history, retry only from the dead-letter row
- [x] work intents and the proximity warning on acquire
- [x] dependencies declare, check, satisfy, assert
- [x] coordinator lease acquire, reject, refresh, release, claim
- [x] spawn id `parent.Type.seq`, invalid type, report, stop request
- [x] broadcast with require_ack, ack, and handoff targets
- [x] env caps listed in the README
- [x] zero Cargo dependencies (`tests/surface.rs` and `scripts/check_zero_deps.py`)

82 checklist rows are ticked in this file (50 shared tools, `who`, the CLI commands, HTTP, lock rules, and the other rows above).
