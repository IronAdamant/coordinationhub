# Migration from Python 0.8.0

The `python-legacy` branch is the Python 0.8.0 tree, unchanged. This branch is the Rust program. Tool names, argument names, and the JSON returned for the shared tools are what the parity harness compares.

## Install

Replace `pip install coordinationhub` with the Rust binary:

```bash
cargo install --path . --locked
```

The console script is still named `coordinationhub`. `serve-mcp` replaces `python -m coordinationhub.mcp_stdio`. The optional `mcp` Python package is no longer required. Current releases of that package no longer expose the `Server.list_tools` decorator the Python server called, so the legacy stdio entry point does not start on a current install. The Rust server speaks newline JSON-RPC itself.

## Storage

The journal is `coordination.jsonl` plus `coordination.lock` in the same directory Python used for `coordination.db`.

Rust does not read or write the SQLite file. Copying an old `coordination.db` into the directory does not restore agents or locks. Start a fresh journal, or keep a Python 0.8.0 process on `python-legacy` until that swarm is finished.

The on-disk bytes are not part of the parity compare. Tool results are.

## Tool surface

The 50 Python tools keep their names and input schemas (`TOOLS_VERSION` stays `1.0.0` for that set).

One tool was added:

- `who` with `action` of `status` (default), `claim`, `release`, or `handoff`

`status.tools` counts the Rust surface, so it is 51 rather than 50. That number is a real difference. The harness records it. It does not rewrite it.

`who` claims are not file locks. A claim names an area. It ends when its `ttl` elapses (default 600 seconds), when the owner's heartbeat is older than `COORDINATIONHUB_AGENTS_STALE_TIMEOUT_S` (default 600), or when the owner is deregistered. A claim on `src/parser` blocks a claim on `src/parser/lexer.rs`. The claimer and a handoff recipient must be registered and active. `ttl` must be a number and must not be negative. Relatum still holds the handoff document. stele-context still holds the code map.

## Behaviour kept

Lock acquire, release, refresh, region overlap, shared locks, denied attempts, retry with jittered backoff, and expiry follow the Python rules, including the strict `region_end` boundary. Heartbeat, register collision, descendant reparenting, tasks, messages, intents, dependencies, leases (`COORDINATOR_LEADER`), spawns, broadcasts, and handoffs return the same JSON shapes. Wait tools sleep until the condition is true or the timeout elapses, and `waited_s` is the time this process actually waited. Several processes share one journal: the lock is held for one call, then released, and the next call reads new lines.

`run_assessment` prints a per-trace section, `True` or `False` for the graph, and a local timestamp. Scores use the same keyword groups as the Python scorers. With no graph loaded, handoff latency is 0.

`GET /events` stays open, sends `id:` and `data:` frames, and replays the current snapshot when `Last-Event-ID` is behind.

`coordinationhub hook` registers the session, locks on PreToolUse Write or Edit, notifies and releases on PostToolUse, and correlates a stashed Agent description with SubagentStart.

Long-running `serve` and `serve-mcp` processes reap stale agents, expired locks, and lapsed claims. Set `COORDINATIONHUB_HOUSEKEEPING=0` to turn that off. Python leaves it off unless the variable is set.

YAML coordination specs still fail with the same message Python produced when `ruamel.yaml` was not installed. Use `coordination_spec.json`.

## Behaviour that differs on purpose

These are the remaining differences. They are not papered over.

- `status.tools` is 51 because of `who`. Python reports 50.
- `manage_dependencies` mode `satisfy` on an unknown `dep_id` returns `not_found`. Python reported the dependency as satisfied.
- `register_agent` keeps a `worktree_root` the caller passed. The Python MCP path stores the project root instead.
- `acquire_lock` with `force` on an exclusive lock removes every conflicting holder, including every shared holder. Python removed one row and could leave an exclusive lock beside a shared lock.
- `initialize` reports this package version (`1.0.0`). Python's stdio server reports the version of the `mcp` library.
- A duplicate task id returns `task id already exists: ...`. Python's stdio adapter hides the database error behind an internal message and a correlation id. This program does not invent that text.
- Validation errors from the Rust server start with `Input validation error:`. The wording after that follows this program's checker. It is not a byte-for-byte copy of the `mcp` 1.30 jsonschema sentence.
- Characters above U+FFFF are written as UTF-16 surrogate pairs, which is what Python's JSON encoder does. Control characters below U+0020 are escaped.
- `GET /events` requires the same bearer token as the other routes and allows four open streams per client address. A fifth receives 429.
- A `who` area is resolved against the project root. A claim on a directory name blocks a file underneath it, and `./name` is the same area.
- Assessment with no spec file uses the same implicit graph Python builds from the live agents, so Graph Loaded is True and a quiet session scores 100%. The live trace id is `live_session`.
- `init` writes the same seven hook events Python writes (SessionStart, UserPromptSubmit, PreToolUse for Write|Edit and Agent, PostToolUse for Write|Edit plus the Stele and Trammel tools, SubagentStart, SubagentStop, SessionEnd), with the same matchers, timeouts, and status messages. The command is this binary plus `hook`, not `python -m coordinationhub.hooks.stdio_adapter`. An earlier build wrote only SessionStart. That was a gap, and it is fixed.
- `doctor` checks the JSONL journal, the hooks file, and that the journal parses. It does not open a SQLite schema version. `doctor --json` prints `{"checks", "all_ok"}`. After `init`, every check passes.
- `watch` refreshes until interrupted and prints the summary line (active agents, locks, conflicts). An earlier build printed the tree once. That was a gap, and it is fixed.
- `auto-start-dashboard` stays quiet. If the port is free it starts `serve-sse` in the background.
- `coordinationhub <command> --help` prints that command's usage, a one-line description, and its flags. The wording is not a copy of argparse's generated help. An earlier build printed only the global command list.
- In prose mode, these six commands exit 1 with `Error: 'field'` on stderr when Python 0.8.0 raises, and they print the same header line Python prints before the exception: `get-conflicts` (when any conflict exists), `get-available-tasks` and `query-tasks` (when the result has task rows), `get-leader` and `leader-status` (when a lease row exists), and `list-pending-spawns` (when a spawn row is missing `id`, or the description is null). Empty results print the same sentences as 0.8.0. JSON mode does not crash. This matches 0.8.0, including the crash. The stderr line is `Error: ...`, which is what `cli.py` prints. It is not a copied CPython traceback.
- `prune-notifications` deletes old change notifications and the coordination events those calls recorded. The printed count is both. Events are recorded for register, deregister, notify, declare-dependency, create-task, send-message, a reported spawn, lock acquire, and lease acquire or refresh. Python also records an event for lock release, lease release and claim as its own topic, handoffs, broadcasts, task assignment and status, dependency satisfaction, scan, graph load, and assessment. A prune after one of those calls can count fewer rows here.
- `doctor` always exits 0, including when no hooks file is present. The hooks check then says the config was not found. The storage check looks for `coordination.jsonl`, not `coordination.db`.

## Config snippets

See the README for Claude Code, Cursor, and Grok Build. Point `command` at the Rust binary and pass `serve-mcp`.
