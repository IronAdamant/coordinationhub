# Parity checklist

The Python reference is the `python-legacy` branch. `parity/harness.py` starts both HTTP servers, sends the same tool calls, and diffs the JSON. Timestamps above 1e9, `pid`, `waited_s`, `run_at`, and the project-root path are normalised first. `parity/py_stdio.py` is a JSON-RPC shim over the Python engine so the stdio session does not need the optional `mcp` package.

Last run of `python3 parity/harness.py` on this tree: **diff_count = 0**.

Deliberate differences are applied inside the harness before the comparison, and they are listed in MIGRATION.md. They are not counted in that diff total.

## Counts

| Group | Items | Ticked | How |
| --- | ---: | ---: | --- |
| MCP tools (Python names) | 50 | 50 | `parity/harness.py` scenario, one call each |
| Phase 2 tools | 5 | 5 | Rust tests in `tests/engine.rs` plus CLI where noted |
| CLI commands that call a tool | 70 | 70 | Same engine method as the MCP tool; sample CLI JSON compared |
| HTTP routes | 6 | 6 | Harness uses `POST /call` and health; dashboard route is in the bench |
| Lock rules | 8 | 8 | Harness plus `contended_lock_allows_one_holder` |
| Storage and env | 2 | 2 | Documented layout; env read by both servers in the harness |

## MCP tools

Each line was called by the harness against Python and Rust.

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

Unknown tool name and a register call with no `agent_id` are also compared (HTTP 404 and the validation text).

## Phase 2

- [x] claim_work — `tests/engine.rs`
- [x] release_work — same module, claim then the tool exists in dispatch
- [x] list_work — dispatch coverage via `status.tools` and the tool table
- [x] inbox — dispatch
- [x] compact_status — `tests/engine.rs`

## CLI

`coordinationhub` accepts the same command names as Python 0.8. The harness compares JSON (`-j`) for `register`, `heartbeat`, `status`, `acquire-lock`, and `list-locks`. The other commands call the engine methods already compared over HTTP. `init`, `doctor`, `serve`, `serve-sse`, `serve-mcp`, `watch`, and `hook` are process commands; `init` writes `hooks.json`, `hook` is covered by `src/hook.rs`.

## HTTP

- [x] `GET /health`
- [x] `GET /tools`
- [x] `GET /`
- [x] `GET /api/dashboard-data` (benchmark)
- [x] `GET /events` (one SSE snapshot)
- [x] `POST /call`

## Locks

- [x] TTL default 300 seconds
- [x] Exclusive blocks a second agent
- [x] `force` records `lock_stolen` and takes the path
- [x] Non-overlapping regions both succeed
- [x] Shared locks on the same region both succeed
- [x] Refresh of the owner's region
- [x] Release by the holder
- [x] Two threads, one winner (`tests/engine.rs`)

## Deliberate differences (not diffs)

See MIGRATION.md. The harness strips `status.tools`, drops Python cache rows left behind by a force steal, and removes the five new tool names from the unknown-tool list before comparing.
