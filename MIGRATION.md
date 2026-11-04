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

`status.tools` counts the Rust surface, so it is 51 rather than 50. The harness rewrites that one field before it compares.

`who` claims are not file locks. A claim names an area, lasts until `ttl` seconds after the last heartbeat of the owner (default 600), and is dropped when the owner is deregistered. A second agent cannot claim the same area while the first claim is live. `handoff` moves it. This is the live ownership record. Relatum still holds the handoff document. stele-context still holds the code map.

## Behaviour kept

Lock acquire, release, refresh, region overlap, shared locks, force, denied attempts, and expiry follow the Python rules, including the strict `region_end` boundary. Heartbeat, register collision, descendant reparenting, tasks, messages, intents, dependencies, leases (`COORDINATOR_LEADER`), spawns, broadcasts, and handoffs return the same JSON the harness checks.

YAML coordination specs still fail with the same message Python produced when `ruamel.yaml` was not installed. Use `coordination_spec.json`.

## Behaviour that differs on purpose

- Help text from `--help` is not the argparse transcript. The command names and the flags the harness uses are the same. JSON output is what is compared.
- The process holds `coordination.lock` for the whole open engine, not only for one statement. Two CLI processes queue. Threads inside one process share the engine.
- Waits (`wait_for_task`, `await_agent`, and the other wait tools) return the current state. They do not sleep. Pass `timeout_s` of 0, which is what the harness does. A finished task still reports `timed_out: false`.
- `run_assessment` on a live session builds one register trace per agent and scores each metric at 1.0 when the trace has no contrary events. That matches the Python scorers for a register-only session. A hand-written suite with rich events is not scored by the Python keyword tables; those suites should be treated as a later gap if you depend on the numeric breakdown.
- HTTP `GET /events` sends one dashboard snapshot and closes. Python kept the stream open.
- `init` writes a hook command of `coordinationhub hook`, not `python -m coordinationhub.hooks.stdio_adapter`.
- `doctor` looks for `coordination.jsonl`.

## Config snippets

See the README for Claude Code, Cursor, and Grok Build. Point `command` at the Rust binary and pass `serve-mcp`.
