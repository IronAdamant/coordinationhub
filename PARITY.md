# Parity with Python 0.8.0

The harness is `parity/harness.py`. It loads Python from the `python-legacy` branch and the Rust binary from `target/release/coordinationhub`. Both sides see the same tool name and the same JSON arguments, with the clock fixed at 1700000000 and the PID fixed at 4242.

Latest run on this branch: `diffs 0` (exit 0).

The compare sorts agent rows by `agent_id` when every row has a heartbeat, sorts conflict rows by `id`, and rewrites `status.tools` to 50. Those three normalisers are deliberate:

- SQLite does not define order when every heartbeat is the same instant. One Python query returned newest-first and the next oldest-first.
- Conflict rows with the same timestamp came back in either id order.
- `who` makes the Rust tool count 51. The field is the only numeric change in `status`.

The journal file is not compared. See `MIGRATION.md`.

`cargo test --release` runs `tests/surface.rs`, which calls all 50 Python tools and `who`.

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
