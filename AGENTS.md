# AGENTS.md: CoordinationHub

The guide for an agent working on this repository. `README.md` is the user guide, `MIGRATION.md` lists every deliberate difference from 0.8.0, `CHANGELOG.md` the releases.

## What this is

CoordinationHub is a shared board for coding agents on one project: agent identity and lineage, file locks (whole files or line ranges), a task board with dependencies, work intents, messages, broadcasts, handoffs, a coordinator lease, a sub-agent spawn registry and a live dashboard. Agents reach it over MCP stdio (`coordinationhub serve-mcp`), the command line, or HTTP (`serve`, `serve-sse`).

It is one binary built from the Rust standard library. Version 1.0.0 rewrote the Python 0.8.0 program with the same tools, commands, flags and replies; that tree is kept unchanged at the tag `pre-rust-python-legacy` and is used only as the parity and benchmark reference. There is no Python package, no SQLite and no `pytest` here.

## Hard rules

- **Std only.** `[dependencies]` and `[dev-dependencies]` stay empty: no crates, no build script, nothing installed at run time. Operating-system calls go through hand-declared FFI, as in `src/util.rs` and `src/rand.rs`. `check_zero_deps` enforces it.
- **Function parity with 0.8.0.** Every tool, command, flag and reply shape matches 0.8.0. Where 0.8.0 had a bug, return the working result and list it in `MIGRATION.md`. `MIGRATION.md` lists every deliberate difference and nothing else, and `examples/parity.rs` accepts only those (its `ALLOWED` list).
- **Rust 1.83.** `rust-toolchain.toml` pins 1.83.0 and `Cargo.toml` says `rust-version = "1.83"`. Do not raise either.
- **Faster than Python on every benchmark row** (`benchmark-gate` in CI).
- **Errors say** what is wrong, which field, what is expected and what to do next. MCP replies are compact JSON (`json::dumps_tight`).
- **No unfinished markers** in `src/` or `tests/` (`check_no_todos`).
- **Tests.** Every fix gets a test that fails without it, in the style of `tests/round5.rs` or `tests/wire_storage.rs`. The goldens in `tests/argparse_goldens.rs` were captured from unmodified 0.8.0 on Python 3.12; do not edit them to make a failure pass.
- **Docs travel with the code**: `README.md`, `CHANGELOG.md`, `MIGRATION.md`, the tool descriptions in `assets/tools.json`, this file, and a top entry in `LLM_Development.md` for significant changes.

## Before a pull request

```bash
cargo test --release
cargo clippy --all-targets -- -D warnings      # rustup component add clippy --toolchain 1.83.0
cargo run --release --example check_zero_deps   # no crates, no build script
cargo run --release --example check_no_todos    # no unfinished markers
# Python 0.8.0 reference (CI runs both on Linux):
git fetch origin tag pre-rust-python-legacy
python3.12 -m pip install "mcp==1.30.0"
cargo build --release
cargo run --release --example parity -- target/release/coordinationhub --python python3.12
cargo run --release --example bench -- target/release/coordinationhub --python python3.12
```

On Windows the binary is `target/release/coordinationhub.exe`; pass that path. CI (`.github/workflows/ci.yml`) runs the tests on Linux, macOS and Windows (x64 and arm64), clippy on all three systems, the two checks, the parity harness, and a job that the 0.8.0 tree still installs and passes its own tests; `.github/workflows/benchmark-gate.yml` runs `bench`. `RELEASING.md` covers releases.

## Module map

- `src/main.rs`: process entry; hands the arguments to `cli::main`.
- `src/lib.rs`: the module list and `VERSION` (from `Cargo.toml`).
- `src/cli.rs`: command dispatch (`serve-mcp`, `serve`, `serve-sse`, `init`, `doctor`, `hook`, and one command per tool). Flags follow the command, as in 0.8.0.
- `src/argparse.rs`: a port of the CPython 3.12 argparse paths the 0.8.0 parser uses: prefixes, short clusters, `choices`, int and float errors, help wrapped to `COLUMNS`.
- `src/cli_catalog.rs`: the 0.8.0 parser's commands, flags and help, introspected from it. Regenerate rather than hand-edit; the hand-written rows (`-V/--version`, `assign-task --force`, the `NO_EFFECT` help of `--replica` and `--force-refresh`, the 1.4.0 lock lists and `--best-effort`) are marked.
- `src/cli_cmds.rs`: command-line arguments to tool arguments. `src/cli_text.rs`: the readable output, matching 0.8.0's printers.
- `src/engine.rs`: `Engine::open` finds the project root and store and loads the coordination spec; `Engine::call` (MCP, with schema checks) and `call_cli` dispatch each tool, plus the additive `who`.
- `src/validate.rs`: argument checks against `assets/tools.json`, with 0.8.0's error text.
- `src/groups.rs`: the MCP tool groups and their switches (see Choosing groups).
- `src/mcp.rs`: the stdio JSON-RPC server: newline frames or `Content-Length` frames, each at most 1,000,000 bytes.
- `src/http.rs`: `serve` and `serve-sse`: `POST /call`, the `/events` stream and the dashboard; Host, Origin and Content-Type checks, the bearer token, connection and time limits.
- `src/waits.rs`: calls that wait run on an engine and thread of their own, so they never hold the server's shared engine.
- `src/hooks.rs`: `coordinationhub hook`, one process per IDE event: registers the session, locks a file before Write or Edit and notifies and releases after, gives each subagent its own agent, redacts prompts.
- `src/house.rs`: server housekeeping (stale agents, expired locks, lapsed claims) and event retention.
- `src/db.rs`: the store: the in-memory `State`, the journal, its file lock, torn-tail recovery, repair and compaction (next section).
- `src/json.rs`: the strict JSON parser and the writers. Read anything that comes back from the store with `parse_journal` (journal lines) or `parse_stored` (JSON text kept inside rows), never `parse`.
- `src/rand.rs`: the operating system's random bytes through FFI, for the HTTP token.
- `src/util.rs`: clock, local and UTC time stamps, paths, size limits, and the cross-process file lock (`flock`, `LockFileEx`).
- `src/act_identity.rs`: register, heartbeat, deregister, lineage, reaping quiet agents.
- `src/act_lock.rs`: locks and regions, lock lists (all or nothing, `best_effort`), conflicts, change notifications.
- `src/act_board.rs`: tasks, messages, work intents, dependencies, the coordinator lease, spawns, broadcasts, handoffs.
- `src/act_view.rs`: status, the coordination spec and graph, project scan, agent trees, assessment, `who` claims, dashboard rows.
- `assets/tools.json`: the 53 tool schemas and descriptions plus `tools_version`. `assets/dashboard.html`: the self-contained dashboard page. `assets/monitor_skill.md`: the skill `init --monitor-skill` installs.
- `examples/parity.rs` and `examples/bench.rs`: compare and time this binary against 0.8.0. `examples/check_zero_deps.rs` and `examples/check_no_todos.rs`: the CI checks. `examples/gen_docs.rs`, `examples/propose_stale_phrases.rs`, `examples/update_dispatch_floor.rs` and `examples/pyport/`: ports of 0.8.0's documentation tools; they read a Python tree (pass `--root`), not this one.
- `tests/`: integration tests that drive the library or the built binary; `tests/common/` holds the shared helpers and `tests/common/dashboard_harness.js` (runs the dashboard script under `node` when one is installed). `tests/doc_paths.rs` fails when this file names a path that is not in the tree.

## How state is stored

- **Where.** `--storage-dir`, else `.coordinationhub/` in the project root (`--project-root`, `COORDINATIONHUB_PROJECT_ROOT`, or the nearest folder with `.git`), else `~/.coordinationhub/`.
- **The journal.** `coordination.jsonl` is append-only, one JSON record per line: `put` a row, `del` a row, `ids` the id counters. Every call takes the exclusive lock on `coordination.lock` (waiting at most 30 s, `COORDINATIONHUB_LOCK_WAIT_S`, then `store busy`), reads what other engines and processes appended since it last looked, applies its change in memory and appends the rows that differ in one write. Nothing is synced unless `COORDINATIONHUB_FSYNC=1`.
- **Torn tail.** A last record that cannot be read is moved into `coordination.jsonl.<UTC time>.torn` (an unfinished write) or `.unreadable` (a complete line that is not a record) and cut off, with one notice on stderr, so no append lands on a partial line. A bad record in the middle is an error that names the line. `doctor` reads the journal the same way, under the lock, and changes nothing.
- **Repair.** A journal 1.2.0 wrote `Infinity` or `NaN` into is read, given finite values and rewritten once. Numbers are kept finite from then on.
- **Retention.** Hub events, change notifications and claim events older than `COORDINATIONHUB_EVENTS_MAX_AGE_S` (seven days; `0` keeps all) are dropped by server housekeeping and by any call once the journal passes the compaction size. Each agent's last lifecycle event and undelivered lock-lapse warnings stay.
- **Compaction.** Once the journal is over `COORDINATIONHUB_COMPACT_MIN_BYTES` (1 MiB; `0` turns it off) and holds more than twice as many records as live rows, it is rewritten as one record per row through a synced temporary file and a rename, under the lock. The lock file grows by one byte per compaction, which tells every other engine to reload.

## Choosing groups

The 54 MCP tools come in seven groups (`GROUPS` in `src/groups.rs`; `assets/tools.json` has 53 schemas, and `who` is added by the engine). `core` is always on. The agent working on a project picks the rest in the project's `AGENTS.md`: one line per agent tool in the first fenced block whose info string is `agent-tools`. `coordinationhub: tools <groups>` keeps only those groups plus `core`; `coordinationhub: disable <groups>` hides some. Tool names work as well as group names. The server rereads the file on the next request and tells the client the tool list changed, so no restart is needed. `COORDINATIONHUB_TOOLS` and `COORDINATIONHUB_DISABLE` in the client's `env` override the matching line. A mistake in the file is reported in the `initialize` instructions and by `status` (`tool_groups_warning`); it never stops the server. The command line is not affected.

| Group | Tools | For |
| --- | ---: | --- |
| `core` | 5 | register, heartbeat, deregister, list agents, status |
| `locks` | 13 | file locks, the coordinator lease, work intents, `who` claims |
| `tasks` | 9 | the task board: dependencies, assignment, waits, failures |
| `messages` | 11 | messages, broadcasts, handoffs, change notifications |
| `spawn` | 8 | the sub-agent spawn registry |
| `agents` | 5 | lineage, agent status, the file-to-agent map |
| `diagnostics` | 3 | project scan, coordination spec, assessment |

CoordinationHub owns `locks`, `tasks` and `messages` among the IronAdamant tools: when it runs for several agents, keep them here and switch them off in the others. The examples below go inside a block fenced with three backticks and the info string `agent-tools` (shown here as `text` so this repository's own server is not switched).

Several agents or sessions sharing one checkout:

```text
coordinationhub: tools locks, tasks, messages
trammel: disable locks
chisel: disable locks
```

One agent working alone (solo): there is nothing to lock or message and no one to hand work to, so keep only the core tools:

```text
coordinationhub: tools core
```

`core` still registers the agent and reports `status`. Add a group back when a second agent joins; a call to a hidden tool answers with the line to change.
