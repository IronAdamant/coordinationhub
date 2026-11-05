# Migration from Python 0.8.0

The `python-legacy` branch is the Python 0.8.0 tree, unchanged (it is byte-identical to the PyPI 0.8.0 package). This branch is the Rust program. It keeps every 0.8.0 tool, CLI command, flag and reply shape. Where 0.8.0 crashed, hid an error, or printed the wrong thing, the Rust program gives the working result instead; each of those is listed below. `cargo run --release --example parity` compares the two and fails on any difference not listed here.

## Install

Replace `pip install coordinationhub` with the Rust binary:

```bash
cargo install --path . --locked
```

The command is still `coordinationhub`. `coordinationhub serve-mcp` replaces `python -m coordinationhub.mcp_stdio`; the Python `mcp` package is not needed. Nothing else is installed at run time.

## Storage

The store is `coordination.jsonl` plus `coordination.lock`, in the directory where 0.8.0 kept `coordination.db` (`<project>/.coordinationhub`, or `~/.coordinationhub` with no project). The SQLite file is not read. Start a fresh store, or let a running 0.8.0 swarm finish on `python-legacy` first. Tool replies are compared, not file bytes.

## Differences

### Accepted earlier by the owner

- **51 tools.** `who` is added (`status`, `claim`, `release`, `handoff` of a work area), so `status.tools` is 51. A `who` claim is not a file lock: it names an area, ends at its `ttl` (default 600 s), when the owner's heartbeat is older than `COORDINATIONHUB_AGENTS_STALE_TIMEOUT_S`, or at deregistration, and a claim on `src/parser` blocks one on `src/parser/lexer.rs`. `who` is an MCP tool only.
- **Satisfying an unknown dependency** (`manage_dependencies` mode `satisfy`, or `satisfy-dependency`) returns `not_found`. 0.8.0 reported it satisfied and recorded an event.
- **`register_agent` reply.** Its `worktree_root` is the value the caller passed. 0.8.0 replies with the project root. Both store the caller's value.
- **Forced exclusive lock** removes every conflicting holder, including shared holders. 0.8.0 removed one row and could leave an exclusive lock beside a shared one.
- **`initialize`** reports this package's version, `1.0.0`. 0.8.0 reports the `mcp` library's version.
- **Validation wording.** Schema errors start with `Input validation error:` as in 0.8.0; the sentence after it is this program's (for example `limit must be >= 1 (got -1)` where 0.8.0 says `-1 is less than the minimum of 1`).
- **Housekeeping.** `serve` and `serve-mcp` reap stale agents, expired locks and lapsed claims in the background unless `COORDINATIONHUB_HOUSEKEEPING=0`. 0.8.0 does it only when the variable is `1`.
- **Hook command.** `init` writes the same seven hook events, matchers, timeouts and status messages, with the command `<this binary> hook` instead of `python -m coordinationhub.hooks.stdio_adapter`, and prints `Hook command:` where 0.8.0 printed `Python interpreter:`. `--auto-dashboard` prints the dashboard command with this binary.
- **`doctor`.** It checks the JSONL journal instead of the SQLite file and schema table, and replaces the `hook_python` check (can `python3` import the package) with `hook_command`. Its messages name the journal: `journal exists at ...`, `journal parses; schema is current`, `coordinationhub 1.0.0 binary`.

### Fixes for 0.8.0 bugs

- **Tool errors are reported.** 0.8.0's stdio server turns every exception inside a tool into the ordinary text `Error: Internal tool execution error (correlation_id=...)` without `isError`. Here the reply has `isError: true` and says what went wrong:
  - a duplicate task id: `task <id> already exists; use a new task_id, or update_task_status / assign_task on the existing task` (the CLI prints the same; 0.8.0's CLI printed `UNIQUE constraint failed: tasks.id`)
  - an unknown tool: `Unknown tool: '<name>'. Available: [...]`
- **Integer-valued floats.** `3.0` where an integer is expected is taken as `3`, as JSON Schema allows. 0.8.0 accepted it at the schema and then failed inside the tool.
- **Readable (non-`--json`) output** reports what happened. 0.8.0 read keys its own replies do not have:
  - `claim-leadership` says `Leadership claimed` with the holder, TTL and expiry; 0.8.0 always printed `Leadership claim failed`. A refused claim also shows the holder's expiry.
  - `wait-for-task` says `Task completed` or `Task failed`; 0.8.0 printed `Timeout waiting for` for a finished task.
  - `scan-project` prints the file and owned counts; 0.8.0 printed 0 and 0.
  - `task-failure-history` prints `attempt N at <time>: <error>`; 0.8.0 printed `?: <error>`.
  - `report-subagent-spawned` prints `Description: (none)` when there is none; 0.8.0 printed `None`.
  - `get-conflicts`, `get-available-tasks`, `query-tasks`, `get-leader`, `leader-status` and `list-pending-spawns` print their rows. 0.8.0 printed the header and exited 1 with `Error: '<field>'` whenever there was a row. `query-tasks task`, `subtasks` and `tree` print the task(s); 0.8.0 printed `No tasks found`.
- **`doctor`, `init` and `auto-start-dashboard` honour `--storage-dir` and `--project-root`.** 0.8.0 accepted the flags and ignored them (it used the current directory's git project or `~/.coordinationhub`).

### Added at the owner's request

- **Tool groups.** `COORDINATIONHUB_TOOLS` and `COORDINATIONHUB_DISABLE` hide MCP tools by group or name (see the README). With neither set, `initialize` and `tools/list` are unchanged. When set, `initialize` carries a one-line `instructions` naming the groups that are on, and the `status` tool reports `tool_groups` and counts only the exposed tools.

### Platform and layout

- **Help layout.** `--help`, usage lines and argparse errors are laid out the way Python 3.12's argparse does, at any `COLUMNS` or terminal width. Python 3.13's argparse prints a few lines differently (an option with two names shows the metavar once; a `nargs='+'` positional wraps differently) and quotes the choices in `invalid choice` errors.
- **Windows output.** Text is UTF-8 with LF line endings, and MCP frames end with LF. When its output is piped, 0.8.0 on Windows writes the ANSI code page (and fails on characters outside it) and CRLF, unless `PYTHONUTF8=1` is set.
- **MCP framing.** The stdio server also accepts `Content-Length:` framed requests, besides the newline-delimited JSON 0.8.0 accepts.

## Config snippets

See the README for Claude Code, Cursor and Grok Build. Point `command` at the Rust binary and pass `serve-mcp`.
