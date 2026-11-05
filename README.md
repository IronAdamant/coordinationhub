# CoordinationHub

A shared board for coding agents working on the same project. It records who is working, who holds which file, which tasks are open, claimed, done or handed off, and lets agents message and wait on each other. Agents use it through MCP (stdio) or the command line; a small HTTP server shows a live dashboard.

It is one Rust binary built from the standard library only: no crates, nothing to install at run time. Version 1.0.0 replaces the Python 0.8.0 program (kept on the `python-legacy` branch) with the same tools, commands and replies; `MIGRATION.md` lists the few deliberate differences.

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
coordinationhub acquire-lock src/api.rs worker-1
coordinationhub acquire-lock src/api.rs planner     # FAILED: src/api.rs is locked by worker-1 (exit 4)
coordinationhub update-task-status T-api completed --summary "3 tries with backoff"
coordinationhub release-lock src/api.rs worker-1
coordinationhub get-available-tasks                 # T-tests is open now
```

Over MCP the same steps are `register_agent`, `create_task`, `assign_task`, `acquire_lock`, `update_task_status`, `release_lock` and `get_available_tasks`. Add `--json` to any command for machine-readable output; `coordinationhub <command> --help` lists every flag.

## How it works

- **One store per project.** State is an append-only journal (`coordination.jsonl`). Each call takes a short file lock, reads lines written since its last call, applies the change and appends it, so any number of agent sessions, CLI calls and hooks share one board safely.
- **Agents** register with an id and an optional parent, send heartbeats, and deregister; leaving releases their locks and re-parents their children.
- **Locks** are exclusive or shared, for a whole file or a line range, with a TTL. A refused lock says who holds it and until when.
- **Work**: tasks with priority, dependencies and subtasks, assignment and status, failure history and retries, work intents, and agent-to-agent dependencies.
- **Talking**: direct messages, sibling broadcasts with acknowledgements, formal handoffs, change notifications, and wait tools that return as soon as the thing they wait for happens.
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

## Develop

```bash
cargo test --release
git fetch origin python-legacy && python -m pip install "mcp==1.30.0"
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
