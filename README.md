# CoordinationHub

CoordinationHub is the live coordination layer for a swarm of coding agents. It tracks who is registered, who holds which file lock, who owns a piece of work, and what just changed. Agents talk to it over MCP or the command line. A small HTTP server serves a dashboard.

The core is Rust and uses only the Rust standard library. There are no Cargo crates.

Version 1.0.0 is a rewrite of the Python 0.8.0 tree. That tree is still on the `python-legacy` branch and is what the parity harness compares against.

## How this fits with the other tools

CoordinationHub is the live board: locks, heartbeats, claims, and a compact picture of who is doing what.

- [Relatum](https://github.com/IronAdamant/Relatum) stores the handoff documents an orchestrator passes to a subagent. It is the durable note, not the live lock.
- [stele-context](https://github.com/IronAdamant/stele-context) is the local code map and context cache. It does not decide who owns a file.
- [Trammel](https://github.com/IronAdamant/Trammel) plans and checks work. CoordinationHub records who claimed it.
- [Chisel](https://github.com/IronAdamant/Chisel) answers which tests a change touches. It does not coordinate the agents making the change.

Use them together. Do not look for a second copy of those jobs here.

## Install

Build from this repository:

```bash
cargo build --release
cargo install --path . --locked
```

`cargo install` uses the binary this repo builds. The crate is not published.

Tagged releases attach binaries and `SHA256SUMS` for:

- linux x64 and linux arm64
- macOS x64 and macOS arm64
- Windows x64

Check the sums before you copy a binary onto `PATH`.

## MCP

The stdio server speaks newline-delimited JSON-RPC.

### Claude Code

`.mcp.json` at the project root:

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

### Cursor

`~/.cursor/mcp.json` or the project `.cursor/mcp.json`:

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

### Grok Build

```json
{
  "mcpServers": {
    "coordinationhub": {
      "command": "coordinationhub",
      "args": ["serve-mcp"],
      "env": {
        "COORDINATIONHUB_PROJECT_ROOT": "."
      }
    }
  }
}
```

The same `serve-mcp` command is the one to put in a Grok connector that launches a local process. The HTTP server (`coordinationhub serve`) is there for the dashboard and for `POST /call`.

## Use

```bash
coordinationhub init
coordinationhub register agent-a --json
coordinationhub acquire-lock src/main.rs agent-a --json
coordinationhub who --action claim --agent-id agent-a --area src/main.rs
coordinationhub who --action status --json
coordinationhub serve --no-auth --port 9877
coordinationhub serve-sse --no-auth --port 9898
```

`who` is the compact call added in this rewrite. `status` lists the board. `claim` takes an area (a path, a task id, or a name) until the owner's heartbeat stops or someone hands it off. `release` drops it. `handoff` moves it to another registered agent.

## Storage

Python 0.8.0 stored a SQLite file at `.coordinationhub/coordination.db` under the project, or in `~/.coordinationhub` when no project root was set.

Rust stores an append-only journal at `coordination.jsonl` in that same directory, plus `coordination.lock`. The tool results match the Python tree. The file bytes do not. See `MIGRATION.md`.

## Environment

| Variable | Role |
| --- | --- |
| `COORDINATIONHUB_STORAGE_DIR` | Journal directory |
| `COORDINATIONHUB_PROJECT_ROOT` | Project root used to relativise paths |
| `COORDINATIONHUB_NAMESPACE` | Agent id prefix (default `hub`) |
| `COORDINATIONHUB_COORDINATION_URL` | URL embedded in a register response |
| `COORDINATIONHUB_NOW` | Frozen clock for tests, seconds |
| `COORDINATIONHUB_PID` | PID stored on the agent row |
| `COORDINATIONHUB_HOUSEKEEPING` | Accepted; the long-running pruner from Python is not started |
| `COORDINATIONHUB_MAX_DESCRIPTION` and the other `COORDINATIONHUB_MAX_*` caps | Same field caps as Python |
| `COORDINATIONHUB_SPAWN_TTL_SECONDS` | Pending spawn lifetime |
| `COORDINATIONHUB_MAX_PENDING_SPAWNS` | Cap on pending spawns |

## Develop

```bash
cargo test --release
python3 parity/harness.py target/release/coordinationhub
python3 parity/bench.py target/release/coordinationhub
```

The harness needs the `python-legacy` branch (`git fetch origin python-legacy`).

## Further reading

- `PARITY.md` lists every Python behaviour and the test that covers it
- `MIGRATION.md` lists what changed on purpose
- `BENCHMARKS.md` has the measured timings
- `CHANGELOG.md` records the release
