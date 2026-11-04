# Python 0.8 to Rust 1.0

The `python-legacy` branch is the Python 0.8 tree, unchanged. This branch is the Rust program. Tool names, argument names, and the JSON fields agents already read stay the same unless a row below says otherwise.

## Install

Stop using `pip install coordinationhub` for new sessions. Build the Rust binaries (`coordinationhub` and `coordinationhub-mcp`) and put them on `PATH`. MCP client config points at `coordinationhub-mcp` with no extra Python package.

`coordinationhub init` writes hook commands that call `coordinationhub hook`, not `python -m coordinationhub.hooks.stdio_adapter`.

## Storage

Python kept a SQLite file at `.coordinationhub/coordination.db`. Rust keeps a JSON snapshot at `.coordinationhub/state.json`, guarded by `.coordinationhub/state.lock`.

Rust does not read the SQLite file. Copy the live facts across by calling the tools again (register, locks, tasks), or keep the Python process until that swarm is finished. The two files can sit side by side; they do not share rows.

This change is deliberate. The Rust standard library has no SQLite, and the project does not take Cargo crates.

## Behaviour kept

The 50 MCP tools keep their names and their success and error shapes. The parity harness posts the same JSON to both HTTP servers and expects zero differences after timestamps, process ids, and the project-root path are normalised. See PARITY.md.

File locks still expire by TTL (default 300 seconds). Region ends are exclusive. Two shared locks on the same region are allowed. An exclusive lock blocks other holders. `force: true` records a `lock_stolen` conflict and replaces the other holder's row. A lock outside an agent's declared scope is refused with `scope_violation`. Heartbeats do not reap locks; `admin_locks` does.

## Deliberate differences

1. `status.tools` counts every dispatched tool. Rust adds five tools, so the number is 55 rather than 50.
2. `GET /tools` and the unknown-tool error list include those five names: `claim_work`, `release_work`, `list_work`, `inbox`, `compact_status`.
3. After `acquire_lock` with `force: true`, Rust drops the previous holder's lock from `list_locks` at once. Python's in-memory cache could still show that row until the next refresh. The database row was already gone in Python; Rust matches the database.
4. The on-disk file is JSON, not SQLite. See Storage above.
5. The stdio server does not need the optional `mcp` Python package. Error text from a bad tool call is the validation message, so an agent can read it. The Python SDK path replaced many failures with a random correlation id.
6. `doctor` checks that the Rust binary runs and that `hooks.json` exists. It does not import a Python module.
7. Assessment of a live session uses the same metric names. With no spec file, both sides treat an implicit graph as loaded and score a quiet session at 1.0 on each metric. A custom suite file is still accepted when the path exists.

## Phase 2 additions

These are new. Old clients can ignore them.

- `claim_work` / `release_work` / `list_work` — an agent claims an area (a path prefix or a label). A second agent is refused while the holder is active and the claim has not expired. A stopped agent, or one whose heartbeat is older than 600 seconds, loses the claim on the next claim or list. Heartbeat refreshes the holder's claims.
- `inbox` — unread messages, broadcast ids still waiting for this agent, and handoff ids addressed to them.
- `compact_status` — one object: active agents, lock count, claim count, open tasks, and, when `agent_id` is set, that agent's locks, claims, unread count, children, and current task.

Deregistering an agent releases their claims as well as their locks.

## Versions

Rust crates report version 1.0.0. The Python package on `python-legacy` stays 0.8.0.
