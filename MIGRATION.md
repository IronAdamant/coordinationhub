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
- `init` writes a hook command of `coordinationhub hook`, not `python -m coordinationhub.hooks.stdio_adapter`.
- `doctor` checks the JSONL journal, the hooks file, and that the journal parses. It does not open a SQLite schema version.
- `watch` prints the agent tree once unless `--follow` is set. Python refreshes until interrupted.
- `auto-start-dashboard` prints the dashboard address. `--start` runs `serve-sse` in this process.
- Help text from `--help` is not the argparse transcript. The command names match.

## Config snippets

See the README for Claude Code, Cursor, and Grok Build. Point `command` at the Rust binary and pass `serve-mcp`.
