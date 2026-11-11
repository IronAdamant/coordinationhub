# CoordinationHub

A shared board for coding agents working on the same project. It records who is working, who holds which file, which tasks are open, claimed, done or handed off, and lets agents message and wait on each other. Agents use it through MCP (stdio) or the command line; a small HTTP server shows a live dashboard.

It is one Rust binary built from the standard library only: no crates, nothing to install at run time. It replaces the Python 0.8.0 program (kept at the tag `pre-rust-python-legacy`) with the same tools, commands and replies; `MIGRATION.md` lists the deliberate differences. Version 1.1.0 makes task claims safe under concurrency, adds explicit handoff tools and sends compact replies; 1.2.0 keeps locks alive while their holder heartbeats, reports lapsed locks, and stops long waits from holding up other calls (see the changelog). `coordinationhub --version` prints the installed version.

## Install

```bash
cargo install --path . --locked    # builds and installs `coordinationhub`
coordinationhub init               # optional: IDE hooks in ~/.coordinationhub/hooks.json
coordinationhub doctor             # check the setup
```

Tagged releases attach binaries for Linux x64 and arm64, macOS x64 and arm64, and Windows x64, with `SHA256SUMS`.

## Connect an agent

Claude Code (`.mcp.json` at the project root), Cursor (`.cursor/mcp.json`) and other MCP clients start the stdio server the same way:

```json
{
  "mcpServers": {
    "coordinationhub": {
      "command": "coordinationhub",
      "args": ["serve-mcp"]
    }
  }
}
```

The server keeps its store in the project's `.coordinationhub/` directory (the nearest folder with `.git`), or in `~/.coordinationhub/` outside a project. A client that cannot pass `--project-root` or `--storage-dir` can set `COORDINATIONHUB_PROJECT_ROOT` or `COORDINATIONHUB_STORAGE_DIR` in the server's `env`.

## Example

Two agents split dependent work and contend for a file:

```bash
coordinationhub register planner
coordinationhub register worker-1 --parent-id planner
coordinationhub create-task T-api planner "add retry to fetch()"
coordinationhub create-task T-tests planner "test the retry path" --depends-on T-api
coordinationhub assign-task T-api worker-1
coordinationhub assign-task T-api planner           # refused: T-api is already assigned to worker-1 (exit 4)
coordinationhub acquire-lock src/api.rs worker-1
coordinationhub acquire-lock src/api.rs planner     # FAILED: src/api.rs is locked by worker-1 (exit 4)
coordinationhub update-task-status T-api completed --summary "3 tries with backoff"
coordinationhub release-lock src/api.rs worker-1
coordinationhub get-available-tasks                 # T-tests is open now
```

Over MCP the same steps are `register_agent`, `create_task`, `assign_task`, `acquire_lock`, `update_task_status`, `release_lock` and `get_available_tasks`. Add `--json` to any command for machine-readable output; `coordinationhub <command> --help` lists every flag.

An agent claims a task by assigning it to itself. The claim is checked and written in one step under the store lock, so when several agents claim the same task at once exactly one gets it and the others are told who holds it; `get_available_tasks` with `unassigned_only` lists only tasks nobody holds, and `force` reassigns on purpose. A handoff goes `broadcast` (with `handoff_targets`), `acknowledge_handoff` by each recipient, then `complete_handoff`; `wait_for_handoff` only waits or reads the status.

## How it works

- **One store per project.** State is an append-only journal (`coordination.jsonl`). Each call takes a short file lock, reads lines written since its last call, applies the change and appends it, so any number of agent sessions, CLI calls and hooks share one board safely.
- **Agents** register with an id and an optional parent, send heartbeats, and deregister; leaving releases their locks and re-parents their children.
- **Locks** are exclusive or shared, for a whole file or a line range, with a TTL. A refused lock says who holds it and until when. Each heartbeat renews the caller's locks that have less than half their TTL left, so a lock lasts as long as its holder keeps heartbeating, and the TTL only frees the files of an agent that stopped. A lock that ran out anyway is reported as `expired` (with `expired_at`) by `release_lock`, `refresh_lock`, `heartbeat`, `get_notifications` (with `agent_id`), `deregister_agent` (`locks_expired`) and `admin_locks` `release_by_agent`.
- **Quiet agents.** The reaper marks an agent stopped after 600 s without a heartbeat (`COORDINATIONHUB_AGENTS_STALE_TIMEOUT_S`); its next heartbeat makes it active again (`revived: true`). `list_agents` names the agents it hides for being quiet in `stale_hidden`.
- **Waits** (`get_notifications` with `timeout_s`, `wait_for_*`, `await_*`, `acquire_lock` with `retry`) run on their own thread and engine, so they never hold up other calls on the same server, stop when the client cancels them, and last at most 600 s (`COORDINATIONHUB_MAX_WAIT_S` sets a lower cap) before answering `timed_out`. A call waits at most 30 s for the store's file lock (`COORDINATIONHUB_LOCK_WAIT_S`) and then fails with `store busy` instead of hanging.
- **Work**: tasks with priority, dependencies and subtasks, assignment and status, failure history and retries, work intents, and agent-to-agent dependencies.
- **Talking**: direct messages, sibling broadcasts with acknowledgements, formal handoffs, change notifications, and wait tools that return as soon as the thing they wait for happens. MCP replies are compact JSON.
- **Leadership**: one coordinator lease with a TTL; a refused lease names the holder and its expiry.
- **Hooks**: `coordinationhub hook` (written by `init`) registers a session, locks a file before Write or Edit, and notifies and releases afterwards.
- **Dashboard**: `coordinationhub serve-sse` serves a live page at http://127.0.0.1:9898.

It works alongside [Relatum](https://github.com/IronAdamant/Relatum) (handoff documents), [stele-context](https://github.com/IronAdamant/stele-context) (code map), [Trammel](https://github.com/IronAdamant/Trammel) (plans) and [Chisel](https://github.com/IronAdamant/Chisel) (test impact); it does not duplicate them.

## Performance

Measured on 5 November 2026 on a Windows 11 desktop: unmodified Python 0.8.0 (CPython 3.12.10, `mcp` 1.30.0) and the Rust release binary, both over MCP stdio, median of 3 runs, in seconds. Another build was running at the time, so the absolute numbers are noisy. CI's `benchmark-gate` reruns these rows on Linux and fails if Rust is slower on any of them. `BENCHMARKS.md` has the details and the longer review workload.

| Operation | Python seconds | Rust seconds |
| --- | ---: | ---: |
| startup and status | 1.7481 | 0.0102 |
| status x450 after 450 agents | 1.0599 | 0.2301 |
| register x100 | 2.2121 | 0.0945 |
| lock x100 | 1.8191 | 0.1246 |
| get_agent_tree x20 after 100 agents | 3.2875 | 0.2107 |

## Tool groups

The MCP server exposes 54 tools in seven groups. An agent that only needs part of them can switch the rest off in the client's `env` block; the CLI is not affected. `locks`, `tasks` and `messages` are the names the IronAdamant tools share for overlapping features, so one word means the same thing in every tool.

| Group | Tools | Overlaps with |
| --- | --- | --- |
| `core` (always on) | `register_agent`, `heartbeat`, `deregister_agent`, `list_agents`, `status` | |
| `locks` | `acquire_lock`, `release_lock`, `refresh_lock`, `get_lock_status`, `list_locks`, `admin_locks`, `wait_for_locks`, `get_conflicts`, `get_contention_hotspots`, `manage_work_intents`, `acquire_coordinator_lease`, `manage_leases`, `who` | Chisel locks, Trammel step claims |
| `tasks` | `create_task`, `create_subtask`, `assign_task`, `update_task_status`, `query_tasks`, `get_available_tasks`, `wait_for_task`, `task_failures`, `manage_dependencies` | Trammel plans |
| `messages` | `send_message`, `manage_messages`, `broadcast`, `acknowledge_broadcast`, `wait_for_broadcast_acks`, `acknowledge_handoff`, `complete_handoff`, `cancel_handoff`, `wait_for_handoff`, `notify_change`, `get_notifications` | |
| `spawn` | `spawn_subagent`, `report_subagent_spawned`, `get_pending_spawns`, `await_subagent_registration`, `request_subagent_deregistration`, `is_subagent_stop_requested`, `await_subagent_stopped`, `await_agent` | |
| `agents` | `get_agent_relations`, `get_agent_tree`, `get_agent_status`, `update_agent_status`, `get_file_agent_map` | |
| `diagnostics` | `scan_project`, `load_coordination_spec`, `run_assessment` | |

- `COORDINATIONHUB_TOOLS`: comma-separated groups and/or tool names to expose (plus `core`); `all` is accepted.
- `COORDINATIONHUB_DISABLE`: groups and/or tool names to hide, applied after `COORDINATIONHUB_TOOLS`.
- Case and spaces are ignored, and an empty value counts as unset. An unknown name, or naming `core` or one of its tools in `COORDINATIONHUB_DISABLE`, stops the server with one line on stderr and exit code 2, so the client shows it failed. Calling a hidden tool returns an error that names the variable and what to change, and `initialize` ends with one line listing the groups on and off. With no `agent-tools` block (below) and neither variable set, the tool list is unchanged. Agents normally choose the groups in the project's `AGENTS.md`; the variables are the operator's override.

```json
"env": {"COORDINATIONHUB_DISABLE": "spawn,diagnostics"}
```

A lean set for agents that coordinate files, tasks and messages (24 tools, about half the schema text: 18.9 KB of `tools/list` instead of 38.1 KB):

```json
"env": {"COORDINATIONHUB_TOOLS": "acquire_lock,release_lock,list_locks,who,acquire_coordinator_lease,manage_leases,create_task,assign_task,update_task_status,get_available_tasks,query_tasks,wait_for_task,send_message,manage_messages,get_notifications,broadcast,acknowledge_handoff,complete_handoff,wait_for_handoff"}
```

### Choosing groups

CoordinationHub's MCP tools come in groups, and the agent working on a project picks the ones it needs. Put one line per tool in the first fenced block whose info string is `agent-tools` in the project's `AGENTS.md` (project root: `--project-root`, `COORDINATIONHUB_PROJECT_ROOT`, the nearest folder with `.git`, else the current directory). Use `coordinationhub: tools <groups>` to keep only those (plus `core`) or `coordinationhub: disable <groups>` to hide some. The server rereads the file on the next request and the client refetches the tool list, so no restart is needed. `COORDINATIONHUB_TOOLS` / `COORDINATIONHUB_DISABLE` in the client's `env` override the matching line. A mistake in the file is reported in the `initialize` instructions and by `status`; it never stops the server.

CoordinationHub is the suite's owner for `locks`, `tasks` and `messages`. When it runs, keep those here and switch them off in the other tools; when it does not need them, switch them off here instead.

| Group | For | Turn it off when |
| --- | --- | --- |
| `core` | register, heartbeat, deregister, list agents, status | never (always on) |
| `locks` | file locks, the coordinator lease, work intents, `who` claims | one agent works on the project, or every agent has its own worktree |
| `tasks` | the shared task board: dependencies, assignment, waits, failures | one agent works alone, or Trammel's step queue already hands out the work |
| `messages` | messages, broadcasts, handoffs, change notifications | agents never need to talk while they work |
| `spawn` | the sub-agent spawn registry | the client tracks sub-agents itself (Claude Code's Task tool with the hook) |
| `agents` | lineage, agent status, the file-to-agent map | you do not inspect who spawned whom |
| `diagnostics` | project scan, coordination spec, assessment | you do not use a coordination spec |

Several agents or sessions sharing one checkout (CoordinationHub owns locks, tasks and messages):

```agent-tools
coordinationhub: tools locks, tasks, messages
trammel: disable locks
chisel: disable locks
```

One agent working alone:

```agent-tools
coordinationhub: tools tasks
```

## Develop

```bash
cargo test --release
git fetch origin tag pre-rust-python-legacy && python -m pip install "mcp==1.30.0"
cargo run --release --example parity -- target/release/coordinationhub --python python3.12
cargo run --release --example bench -- target/release/coordinationhub --python python3.12
```

`PARITY.md` explains the comparison with 0.8.0, `MIGRATION.md` lists the differences, `BENCHMARKS.md` has the timings, and `CHANGELOG.md` the releases.

## Environment

| Variable | Role |
| --- | --- |
| `COORDINATIONHUB_STORAGE_DIR`, `COORDINATIONHUB_PROJECT_ROOT` | Store and project for `serve-mcp` when the flags are not given |
| `COORDINATIONHUB_COORDINATION_URL` | URL included in a registration reply |
| `COORDINATIONHUB_HOUSEKEEPING` | `0` stops the background reaping of stale agents, expired locks and lapsed claims in `serve` and `serve-mcp` |
| `COORDINATIONHUB_AGENTS_STALE_TIMEOUT_S` | Heartbeat age after which an agent is stale (default 600) |
| `COORDINATIONHUB_MAX_DESCRIPTION` and the other `COORDINATIONHUB_MAX_*` caps | Field length caps, as in 0.8.0 |
| `COORDINATIONHUB_SPAWN_TTL_SECONDS`, `COORDINATIONHUB_MAX_PENDING_SPAWNS` | Pending sub-agent spawn lifetime and cap |
