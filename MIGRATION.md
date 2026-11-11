# Migration from Python 0.8.0

The tag `pre-rust-python-legacy` (formerly the `python-legacy` branch) is the Python 0.8.0 tree, unchanged (it is byte-identical to the PyPI 0.8.0 package). This branch is the Rust program. It keeps every 0.8.0 tool, CLI command, flag and reply shape. Where 0.8.0 crashed, hid an error, or printed the wrong thing, the Rust program gives the working result instead; each of those is listed below. `cargo run --release --example parity` compares the two and fails on any difference not listed here.

## Install

Replace `pip install coordinationhub` with the Rust binary:

```bash
cargo install --path . --locked
```

The command is still `coordinationhub`. `coordinationhub serve-mcp` replaces `python -m coordinationhub.mcp_stdio`; the Python `mcp` package is not needed. Nothing else is installed at run time.

## Storage

The store is `coordination.jsonl` plus `coordination.lock`, in the directory where 0.8.0 kept `coordination.db` (`<project>/.coordinationhub`, or `~/.coordinationhub` with no project). The SQLite file is not read. Start a fresh store, or let a running 0.8.0 swarm finish on 0.8.0 first. Tool replies are compared, not file bytes.

## Differences

### Accepted earlier by the owner

- **`who`** is added (`status`, `claim`, `release`, `handoff` of a work area), and `status.tools` counts it. A `who` claim is not a file lock: it names an area, ends at its `ttl` (default 600 s), when the owner's heartbeat is older than `COORDINATIONHUB_AGENTS_STALE_TIMEOUT_S`, or at deregistration, and a claim on `src/parser` blocks one on `src/parser/lexer.rs`. `who` is an MCP tool only.
- **Satisfying an unknown dependency** (`manage_dependencies` mode `satisfy`, or `satisfy-dependency`) returns `not_found`. 0.8.0 reported it satisfied and recorded an event.
- **`register_agent` reply.** Its `worktree_root` is the value the caller passed. 0.8.0 replies with the project root. Both store the caller's value.
- **Forced exclusive lock** removes every conflicting holder, including shared holders. 0.8.0 removed one row and could leave an exclusive lock beside a shared one.
- **`initialize`** reports this package's version (the one `coordinationhub --version` prints). 0.8.0 reports the `mcp` library's version.
- **Validation wording.** Schema errors start with `Input validation error:` as in 0.8.0; the sentence after it is this program's (for example `limit must be >= 1 (got -1)` where 0.8.0 says `-1 is less than the minimum of 1`).
- **Housekeeping.** `serve` and `serve-mcp` reap stale agents, expired locks and lapsed claims in the background unless `COORDINATIONHUB_HOUSEKEEPING=0`. 0.8.0 does it only when the variable is `1`.
- **Hook command.** `init` writes the same seven hook events, matchers, timeouts and status messages, with the command `<this binary> hook` instead of `python -m coordinationhub.hooks.stdio_adapter`, and prints `Hook command:` where 0.8.0 printed `Python interpreter:`. `--auto-dashboard` prints the dashboard command with this binary.
- **`doctor`.** It checks the JSONL journal instead of the SQLite file and schema table, and replaces the `hook_python` check (can `python3` import the package) with `hook_command`. Its messages name the journal: `journal exists at ...`, `journal parses; schema is current`, `coordinationhub <version> binary`.

### Fixes for 0.8.0 bugs

- **Tool errors are reported.** 0.8.0's stdio server turns every exception inside a tool into the ordinary text `Error: Internal tool execution error (correlation_id=...)` without `isError`. Here the reply has `isError: true` and says what went wrong:
  - a duplicate task id: `task <id> already exists; use a new task_id, or update_task_status / assign_task on the existing task` (the CLI prints the same; 0.8.0's CLI printed `UNIQUE constraint failed: tasks.id`)
  - an unknown tool: `Unknown tool: '<name>'.` followed by up to three close names and a pointer to `tools/list` (changed in 1.1.0, below)
- **Integer-valued floats.** `3.0` where an integer is expected is taken as `3`, as JSON Schema allows. 0.8.0 accepted it at the schema and then failed inside the tool.
- **Readable (non-`--json`) output** reports what happened. 0.8.0 read keys its own replies do not have:
  - `claim-leadership` says `Leadership claimed` with the holder, TTL and expiry; 0.8.0 always printed `Leadership claim failed`. A refused claim also shows the holder's expiry.
  - `wait-for-task` says `Task completed` or `Task failed`; 0.8.0 printed `Timeout waiting for` for a finished task.
  - `scan-project` prints the file and owned counts; 0.8.0 printed 0 and 0.
  - `task-failure-history` prints `attempt N at <time>: <error>`; 0.8.0 printed `?: <error>`.
  - `report-subagent-spawned` prints `Description: (none)` when there is none; 0.8.0 printed `None`.
  - `get-conflicts`, `get-available-tasks`, `query-tasks`, `get-leader`, `leader-status` and `list-pending-spawns` print their rows. 0.8.0 printed the header and exited 1 with `Error: '<field>'` whenever there was a row. `query-tasks task`, `subtasks` and `tree` print the task(s); 0.8.0 printed `No tasks found`.
- **An expired coordinator lease has no holder** (1.1.1). Once its `expires_at` has passed, `manage_leases` `get`, `get-leader`, `leader-status` and `ha-dashboard` report no leader, and `refresh` by the old holder is refused with `Lease expired at <time>; call acquire_coordinator_lease to take it again` (CLI exit 4). 0.8.0's `get_lease_holder` documents that an expired lease reads as unheld, but its query never checked the expiry, so a lapsed leader was shown indefinitely and its refresh revived the lease. Acquire and claim treated an expired lease as free in both versions. Releasing your own expired lease still succeeds.
- **`doctor`, `init` and `auto-start-dashboard` honour `--storage-dir` and `--project-root`.** 0.8.0 accepted the flags and ignored them (it used the current directory's git project or `~/.coordinationhub`).

### Added at the owner's request

- **Tool groups.** An `agent-tools` block in the project's `AGENTS.md` (chosen by agents) and `COORDINATIONHUB_TOOLS` / `COORDINATIONHUB_DISABLE` (set by the operator, winning per key) hide MCP tools by group or name (see the README). `initialize` advertises `tools.listChanged: true` (0.8.0: `false`), and after an edit to the block that changes the tool set the server sends `notifications/tools/list_changed`. With no block and neither variable set, `tools/list` is unchanged. When set, `initialize` carries a one-line `instructions` listing the groups on and off, and the `status` tool reports `tool_groups` and counts only the exposed tools.

### Changed in 1.1.0 for agents

Each item was found in a multi-agent run (a planner and two workers on one store) and has a test in `tests/agent_improvements.rs`; the parity harness checks the 0.8.0 side of each.

- **`assign_task` refuses a task another agent holds.** 0.8.0 overwrote the assignee (last write wins), so two agents claiming the same task both believed they had it. The check and the write now happen in one step under the store lock. A refused reply is `{"assigned": false, "reason": "already_assigned", "task_id", "assigned_agent_id": <holder>, "status", "error"}`, and `error` says who holds the task and what to do next (another task via `get_available_tasks` with `unassigned_only`, ask the holder, or `force`). Assigning to the current assignee still succeeds. The new `force` argument (CLI `assign-task --force`) reassigns on purpose. The CLI prints `Assignment refused: ...` and exits 4 (0.8.0 printed `Task assigned` and exited 0), and for a task that does not exist prints `Task not found: <id>` and exits 3 (0.8.0 printed `Task assigned`).
- **Explicit handoff tools.** `acknowledge_handoff` (`handoff_id`, `agent_id`), `complete_handoff` and `cancel_handoff` (`handoff_id`) do what `wait_for_handoff` modes `ack`, `complete` and `cancel` do, with the same replies; a refused one also carries an `error` sentence (for example `handoff 1 is pending: 0 of 2 recipients have acknowledged; each recipient calls acknowledge_handoff before complete_handoff`). 0.8.0 had only the modes, so an agent could call `wait_for_handoff` expecting to complete a handoff and just wait. The modes still work unchanged; the `wait_for_handoff` description now points to the new tools. They are in the `messages` group, and `status.tools` is 54.
- **Compact MCP replies.** Tool results are JSON without indentation or spaces after separators, and non-ASCII text (such as the `get_agent_tree` box drawing) is sent as UTF-8 instead of `\uXXXX` escapes. The values are unchanged. 0.8.0 indented by 2. On the 39-call multi-agent run the reply text fell from 9,934 to about 7,600 bytes (23% less). CLI `--json` output is unchanged.
- **`--version` and `-V`** print `coordinationhub <version>` and exit 0 (argparse's `version` action). 0.8.0 had no version flag, so the top-level usage line now reads `coordinationhub [-h] [-V]`.
- **`manage_leases` action `get` needs no `agent_id`.** 0.8.0's schema required one for every action, so an agent reading the leader had to invent an id. The other actions without `agent_id` return `agent_id is required for action '<action>'; pass the agent acting on the lease`.
- **`get_available_tasks` takes `unassigned_only`.** With it, tasks already assigned to an agent are left out, so an idle agent sees only tasks it can claim. Without it the reply is as in 0.8.0.
- **Schema version.** `tools_version` (HTTP `/health` and the schema listing) is `1.1.0` because the tool schemas above changed; 0.8.0 reports `1.0.0`.
- **Unknown tool error.** It names up to three close tools (`Unknown tool: 'assign_tsk'. Did you mean 'assign_task'? tools/list names every tool this server offers.`) instead of listing every tool, about 1.2 KB each time.

### Platform and layout

- **Help layout.** `--help`, usage lines and argparse errors are laid out the way Python 3.12's argparse does, at any `COLUMNS` or terminal width. Python 3.13's argparse prints a few lines differently (an option with two names shows the metavar once; a `nargs='+'` positional wraps differently) and quotes the choices in `invalid choice` errors.
- **Windows output.** Text is UTF-8 with LF line endings, and MCP frames end with LF. When its output is piped, 0.8.0 on Windows writes the ANSI code page (and fails on characters outside it) and CRLF, unless `PYTHONUTF8=1` is set.
- **MCP framing.** The stdio server also accepts `Content-Length:` framed requests, besides the newline-delimited JSON 0.8.0 accepts.

### Changed in 1.2.0 for agents

Each item was found by Specula's M5 lanes and has a test in `tests/m5_fixes.rs`; the parity harness checks the 0.8.0 side of each.

- **Heartbeats renew locks.** `heartbeat` gives every live lock of the caller with less than half its TTL left a fresh TTL (`locked_at` becomes now). 0.8.0 left locks to run out during long builds whatever the agent did. The reply adds, only when non-empty: `locks_renewed` (count), `locks_expiring` (`document_path`, `expires_in_s`: locks whose TTL is shorter than `next_heartbeat_in`), `locks_expired` (`document_path`, `expired_at`) and a `hint`. With nothing to report the reply is 0.8.0's.
- **Lapsed locks are reported.** When a lock's TTL runs out, the hub records a `lock.expired` event as it drops the row (on reap, release, refresh, heartbeat or deregistration). The holder's `release_lock` and `refresh_lock` then answer `{"released"|"refreshed": false, "reason": "expired", "expired_at", "locked_by"?, "error"}` once, where 0.8.0 answered `not_locked` or `not_owner`, or released the expired row as if it were held. A second call gets 0.8.0's `not_locked`. Taking the lock again, releasing it, `release_by_agent` or deregistration clears the report.
- **`deregister_agent`** counts only live locks in `locks_released` and lists lapsed ones in `locks_expired` (omitted when empty); 0.8.0 counted every row it deleted. `admin_locks` `release_by_agent` likewise returns `released` (live) and `expired` (omitted when empty).
- **`get_notifications` with `agent_id`** adds that agent's `locks_expiring` (live locks under half their TTL) and `locks_expired`, when there are any. 0.8.0 used `agent_id` only as the long-poll waiter's name.
- **A quiet agent comes back.** A heartbeat from an agent the reaper marked stopped (no heartbeat for the stale timeout) makes it active and replies `revived: true`; 0.8.0 answered `agent_stopped` for ever after. A deregistered agent still gets `agent_stopped`. The reaper records an `agent.reaped` event to tell the two apart.
- **`list_agents`** with `active_only` (the default) adds `stale_hidden` (ids) and a `hint` when it leaves out agents that never deregistered but are quiet: active with a heartbeat older than `stale_timeout`, or marked stopped by the reaper.
- **Waits are capped and concurrent.** Every wait (`get_notifications` with `timeout_s`, `wait_for_task`, `wait_for_locks`, `await_agent`, `await_subagent_registration`, `await_subagent_stopped`, `wait_for_broadcast_acks`, `wait_for_handoff` completion, `acquire_lock` with `retry`) lasts at most 600 s, or `COORDINATIONHUB_MAX_WAIT_S` when lower; a reply whose timeout was cut carries `wait_capped_s`. 0.8.0 waited as long as asked. In `serve-mcp` and `serve` waits run on their own thread and engine, so other calls are answered meanwhile, and `notifications/cancelled` stops one (no response is sent, as MCP specifies). At most 64 waits run at once per server; a 65th is refused with a sentence saying so.
- **Store lock.** A call waits at most 30 s (`COORDINATIONHUB_LOCK_WAIT_S`) for the journal's file lock and then fails with `store busy: ...` naming the lock file and what to do; before, it waited indefinitely.
- **Readable output.** `release-lock` and `refresh-lock` print the `error` sentence under `FAILED: expired`; `deregister` adds `Locks already expired: N` when there were any.
- **Descriptions.** Tool descriptions say when to use each tool (for example `acquire_lock`: before editing a file other agents may also edit) and that waits are capped. `tools_version` is `1.2.0`.

## Config snippets

See the README for Claude Code, Cursor and Grok Build. Point `command` at the Rust binary and pass `serve-mcp`.
