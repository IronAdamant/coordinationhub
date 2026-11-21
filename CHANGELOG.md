# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Fewer calls per agent turn. `MIGRATION.md` (Changed in 1.4.0) lists each difference from 0.8.0; `tests/lock_batches.rs` has a test for each.

### Added
- `acquire_lock` and `release_lock` take a list of paths in `document_path` (at most 500). A list is locked in one transaction, all or nothing: if any path is held, none is taken and every holder is named. `best_effort: true` takes the free paths and reports the rest. One path keeps the 0.8.0 reply
- `acquire-lock` and `release-lock` take several paths before the agent (`acquire-lock a.rs b.rs agent`), and `acquire-lock --best-effort`
- `register_agent` (and `register`) returns the agent's own live locks (`held_locks`) and its unread messages (`unread_messages`, and the newest 10 in `messages`), so an agent that restarts needs no further calls. The fields appear only when not empty

## [1.3.0] - 2026-11-15

From a line-by-line audit of 1.2.0. `MIGRATION.md` lists each difference from 0.8.0.

Part A: wire, security and storage; `tests/wire_storage.rs` and `tests/http_security.rs` have a test for each.
Part B: locks, hooks, CLI output and tests; `tests/locks_hooks_cli.rs` has a test for each.
Part C: documents, release tooling and clippy; the tool's behaviour does not change.

### Security
- The HTTP auth token comes from the operating system's random generator on every platform, and `serve` / `serve-sse` do not start without it; on Windows 1.0 to 1.2 used the clock (a timestamp followed by sixteen zeros). Tokens are compared in constant time
- The dashboard page no longer contains the token (it was in a `<meta>` tag that `GET /` served without credentials). `serve-sse` prints and opens `http://host:port/#token=<token>`; the page reads the fragment, keeps the token for the tab and sends it as a bearer header
- HTTP requests must name the server in `Host` (`421` otherwise), must not carry another site's `Origin` (`403`), and `POST /call` must be `application/json` (`415`): another web page or a DNS name pointed at 127.0.0.1 can no longer use the hub
- `--no-auth` with a non-loopback `--host` is refused; a non-loopback `--host` with auth prints a warning
- A request nested 100,000 deep no longer overflows the stack and kills `serve-mcp` or `serve-sse`: nesting is limited to 128 levels
- At most 256 HTTP connections are served at once (`COORDINATIONHUB_MAX_CONNECTIONS`), then `503`
- An MCP `Content-Length` frame is capped at 1,000,000 bytes instead of allocating whatever the header says
- `~/.coordinationhub/dashboard.log`, which holds the token, is created with mode 0600 on Unix and an existing file is set to it

### Fixed
- Region locks are inclusive and 1-based (10-20 and 20-30 conflict on line 20); a region with one end, a start after its end or a line below 1 is refused with the field and the rule, in every lock tool and the CLI
- Two subagents of one type started by the hook get their own agents (`<session>.<type>.<n>`); a `SubagentStop` deregisters only its own, and a subagent's write never registers a root agent named by its raw id
- The lock-denied hook message names the holder and the expiry and says what to do; it no longer suggests `release-lock` on another agent's lock
- Subagent prompts and descriptions are redacted before they are stored; hex tokens are redacted in any case
- `descendants_status` follows re-parented agents
- A failed local-time conversion falls back to a UTC stamp instead of printing garbage
- `agent-tree`, `watch`, `dashboard`, `agent-status`, `load-spec` and `dead-letter-queue` print their data; `list_dead_letter` rows carry `dead_letter_at` and the other failure fields
- `load-spec`, `validate-spec` and `doctor` exit 1 on failure
- `watch` writes no escape codes to a pipe or with `NO_COLOR`
- `check_zero_deps` also refuses target-specific, workspace and underscore-spelled dependency tables and a `package.build` script
- A number that is not finite can no longer reach the journal: `"ttl": 1e400` made 1.2.0 write `Infinity`, after which every command failed with `unexpected JSON byte I`. Such numbers are refused (MCP, HTTP and the CLI's `--ttl inf`), every number in a tool call must lie between -1e15 and 1e15, and replies never contain `NaN` or `Infinity`
- A store 1.2.0 damaged that way opens with no hand edit: the rows get finite values and the journal is rewritten once
- An incomplete or unparseable last journal record (a write that did not finish) is cut off and kept in `coordination.jsonl.<UTC time>.torn` instead of failing every command; no record is ever appended onto a partial line. A bad record in the middle is an error naming the line and the recovery step
- JSON is read strictly: `01`, `1.`, `-.5`, raw control characters in strings, trailing bytes and duplicate object keys (named in the error) are refused
- MCP `Content-Length` frames with more than one header line work; a bad frame or bytes that are not UTF-8 get a JSON-RPC error instead of ending the server
- HTTP requests are read as bytes to their exact `Content-Length`: a body with a blank line in it is no longer cut, a character split across packets is no longer corrupted, and the body is parsed once
- The dashboard shows data while auth is on (it sent no token before), keeps its `<!DOCTYPE html>`, and shows a banner with the time of the last update when the server is unreachable or the data is stale
- The browser opener's child process is waited for
- What an older version stored stays readable: a message payload 1.2.0 stored with a repeated key or 300 levels of nesting is returned (last key wins, up to 320 levels) instead of `null`; one that cannot be shown as JSON comes back as its stored text with `payload_unreadable`
- `doctor` reads the journal with the store's reader under the store's lock: it no longer fails a store the hub opens and repairs, and says what the next command will mend
- A complete final journal line that is not a record goes to a `.unreadable` file and is reported as that, not as an unfinished write
- A whole final record without a line feed is applied exactly once, also when the line feed cannot be written at first
- An HTTP request head with bare line feeds is answered `400` at once instead of holding its connection until the deadline

### Changed
- `register_agent` refuses IDs over 256 characters or with control characters or line breaks
- `scan_project` tracks `.rs` by default, skips `target` and does not follow directory symlinks
- `load_coordination_spec` reads at most 1 MiB, `run_assessment` suites at most 16 MiB
- `init` keeps a differing hooks file as `hooks.json.bak`
- `--replica` and `--force-refresh` help says they have no effect; tool descriptions no longer mention SQLite, WAL or Python
- `get_agent_status` adds `locks` with each lock's type, region and expiry
- `tests/surface.rs` checks every reply instead of discarding it
- CI workflows run with `contents: read` and pin third-party actions to commit SHAs
- Hub events, change notifications and claim events older than seven days (`COORDINATIONHUB_EVENTS_MAX_AGE_S`) are dropped by housekeeping and by calls on a large journal; each agent's last lifecycle event and open lock-lapse warnings are kept
- A journal over 1 MiB (`COORDINATIONHUB_COMPACT_MIN_BYTES`) with more than twice as many records as live rows is rewritten as a snapshot; other servers and processes on the store reload on their next call. A 1.2.0 process sharing the store may not (see `MIGRATION.md`)
- `/events` builds the dashboard only when the store has changed and sends a keepalive every 2 s instead of every 0.2 s
- HTTP errors: `411` without `Content-Length`, `431` for oversized headers, `501` for `Transfer-Encoding`; `Invalid JSON` says why
- An HTTP request head must be complete within 5 seconds of connecting and a body within 30 seconds after that (`408`), so sockets that send nothing cannot hold the connection slots
- Dashboard page: text contrast at least 4.5:1, fonts at least 12 px, labelled zoom buttons, a `main` landmark
- `tools_version` is `1.3.0`
- Tests bind port 0 instead of fixed ports

### Added
- `COORDINATIONHUB_FSYNC=1` syncs the journal after each commit (off by default; the README explains the trade-off)
- `doctor` lists torn records that were cut off the journal (`journal_tail`)

### Known limits
- Compaction counts retained events as live rows, so a very busy store still grows within the seven-day window
- The lock file grows before the compaction's rename, so a failed rename makes other processes reload once
- Linux needs glibc 2.25 or newer (or musl 1.1.20) for `getrandom`

### Documented
- README: CLI exit codes; a quiet (reaped) agent keeps its locks until their TTL runs out
- README: the store (strict JSON, torn records, retention, compaction, durability) and the HTTP server (where the token is and who can read it, status codes)
- `AGENTS.md` is rewritten for the Rust tree (it described the Python package, SQLite, WAL and pytest): module map, how the store works, the checks a pull request must pass, the hard rules, and tool groups with the single-agent setting `coordinationhub: tools core` (also in README, which suggested `tools tasks`). `CLAUDE.md` points to it. `tests/doc_paths.rs` fails when `AGENTS.md`, `CLAUDE.md` or `COMPLETE_PROJECT_DOCUMENTATION.md` names a path that is not in the tree
- Tool counts: 54 MCP tools, the 53 schemas in `assets/tools.json` plus `who` (`COMPLETE_PROJECT_DOCUMENTATION.md` said 50)
- `SECURITY_FIXES.md` lists the 1.3.0 security and storage fixes with the Rust file for each; the 0.8.0 audit stays as history
- `RELEASING.md` and `scripts/release.sh` describe the real release: bump the version (every place is listed), merge, tag `vX.Y.Z` on main, and `release.yml` builds and publishes. The script checks the tree and prints the tag command; it no longer runs pytest, edits a Python file, tags or pushes. `scripts/hooks/pre-commit` runs the std-only checks instead of the Python doc generator

### Removed
- Files generated from the Python tree: `docs/wiki/` (90 files), `wiki-local/` (3), `journal/` (2), `library.md`, `file_health.json`, `file_health.md`, `pending_updates.md`, `monitored_paths.txt`, `exclude_patterns.txt`; and `scripts/check_stale_drift.sh`, which ran the Python-tree doc tools on this tree. They remain at the tag `pre-rust-python-legacy`
- `IMPLEMENTATION_PLAN_HA_SPAWNER.md`: the coordinator lease and the spawn registry it planned are in the Rust tree; its read-replica stage is not needed (every process reads the journal; `--replica` has no effect)
- `coordination_spec.yaml` and `coordination_spec.json` at the root: nothing read them (the YAML hid the JSON, and YAML is not read)

### Build
- `cargo clippy --all-targets -- -D warnings` is clean on Rust 1.83 and runs in CI on Linux, macOS and Windows (34 warnings fixed without changing a reply)

## [1.2.0] - 2026-11-11

From a large Rust workspace (about fifteen lane reports). `MIGRATION.md` lists each difference from 0.8.0; `tests/m5_fixes.rs` has a test for each.

### Added
- `heartbeat` renews the caller's locks that have less than half their TTL left, and reports `locks_renewed`, `locks_expiring` (TTL shorter than the next heartbeat) and `locks_expired` (ran out before a heartbeat could renew them)
- `get_notifications` with `agent_id` reports that agent's `locks_expiring` (under half their TTL) and `locks_expired`
- `list_agents` names the agents it hides for being quiet (`stale_hidden`, with a `hint`)
- Every wait is capped at 600 s and replies `wait_capped_s` when the asked timeout was longer; `COORDINATIONHUB_MAX_WAIT_S` sets a lower cap
- MCP `notifications/cancelled` stops a running wait, which then sends no response
- `COORDINATIONHUB_LOCK_WAIT_S` (default 30): how long a call waits for the store's file lock before failing with `store busy`
- Tool descriptions say when to use each tool

### Changed
- `release_lock` and `refresh_lock` of a lock whose TTL ran out answer `reason: "expired"` with `expired_at`, the current holder (`locked_by`) if another agent took the file, and an `error` sentence; 1.1 answered `not_locked` or `not_owner`, or released the dead row as if it were held
- `deregister_agent` counts only live locks in `locks_released` and lists lapsed ones in `locks_expired`; `admin_locks` `release_by_agent` likewise (`released`, `expired`)
- A heartbeat from an agent the reaper marked stopped makes it active again (`revived: true`) instead of answering `agent_stopped`; a deregistered agent still gets `agent_stopped`
- Waiting calls run on their own thread and engine in `serve-mcp` and `serve`, so a long poll no longer holds up other agents' calls
- `tools_version` is `1.2.0`
- CI, `parity` and `bench` read Python 0.8.0 from the `pre-rust-python-legacy` tag; the `python-legacy` branch is gone from GitHub

### Fixed
- Lock lapses were silent: the reaper deleted expired locks without a trace, so a lane learned of it only from a puzzling release reply. Each expiry is now recorded (`lock.expired` event) and reported to its holder
- `heartbeat`, `update_task_status` and other calls could hang until the client's 1800 s idle timeout behind another agent's long poll on the same server, because the stdio server answered one call at a time
- `list_agents` returned `[]` while lanes worked: after 600 s without a heartbeat (a long build) the reaper marked each lane stopped, and every later heartbeat was refused
- A call that failed to read the journal kept the store's file lock until that server's next call, stalling every other process; the lock is released at once. The file lock wait is bounded, and the Windows lock call passes a full `OVERLAPPED`

## [1.1.1] - 2026-11-05

From a live test on a large Rust workspace (B12).

### Fixed
- `deregister_agent` releases the coordinator lease its agent holds (`lease_released: true`), as 0.8.0 does. It matched the lease name `coordinator` instead of the stored `COORDINATOR_LEADER`, so the lease stayed held until its TTL ran out
- An expired coordinator lease has no holder: `manage_leases` `get`, `get-leader`, `leader-status` and `ha-dashboard` show no leader, and `refresh` of an expired lease is refused with a pointer to `acquire_coordinator_lease`. 0.8.0 showed a lapsed leader indefinitely (see `MIGRATION.md`)

## [1.1.0] - 2026-11-05

Changes for agents, from a multi-agent run on one store. `MIGRATION.md` lists each difference from 0.8.0.

### Added
- `acknowledge_handoff`, `complete_handoff` and `cancel_handoff` MCP tools (54 tools). The `wait_for_handoff` modes still work
- `assign_task` `force` argument and CLI `assign-task --force` to reassign on purpose
- `get_available_tasks` `unassigned_only` argument: only tasks an idle agent can claim
- `coordinationhub --version` / `-V`

### Changed
- `assign_task` refuses a task another agent holds, in one step under the store lock, so concurrent claims have exactly one winner; the refusal names the holder and the next step. The CLI exits 4 when refused and 3 for a missing task
- MCP tool replies are compact JSON with UTF-8 text: 23% fewer reply bytes on the 39-call multi-agent run
- `manage_leases` action `get` no longer needs `agent_id`
- An unknown tool name gets up to three close matches instead of the whole tool list
- `tools_version` is `1.1.0`

### Fixed
- The release workflow publishes without a checkout (`gh release --repo`) and replaces the assets of an existing release

## [1.0.0] - 2026-11-05

Rust rewrite of the Python 0.8.0 program. The Python tree remains on `python-legacy`.

### Added
- Stdio MCP server, HTTP dashboard server, and CLI, with no Cargo crates
- Every 0.8.0 CLI command and flag, parsed like Python 3.12's argparse (prefixes, typed and choice errors, help wrapped to the terminal), plus `init --monitor-skill`, the `serve-sse` browser launch and `auto-start-dashboard`
- Parity harness and benchmark gate as std-only examples (`examples/parity.rs`, `examples/bench.rs`), with `check_zero_deps` and `check_no_todos`; `gen_docs`, `propose_stale_phrases` and `update_dispatch_floor` are ported the same way
- `who` for a compact picture plus claim, handoff, and release of a work area
- `COORDINATIONHUB_TOOLS` / `COORDINATIONHUB_DISABLE` to switch MCP tool groups off (default surface unchanged)
- Release workflow that builds linux x64, linux arm64, macOS x64, macOS arm64, and Windows x64, with `SHA256SUMS`

### Changed
- On-disk state is `coordination.jsonl` instead of `coordination.db`. See `MIGRATION.md`.
- Where 0.8.0 crashed, hid a tool error, or printed the wrong result, the working result is returned; `MIGRATION.md` lists each case.

## [0.8.0] - 2026-06-11

**Final release.** Development is paused — see the README retrospective.

### Added
- `LICENSE` file (MIT — was declared in metadata but never shipped)
- PEP 639 license metadata (`License-Expression: MIT` in wheel METADATA)
- Python 3.13 and 3.14 support: CI test matrix + trove classifiers
- Project `CLAUDE.md`; per-file wiki entries for all source modules under `docs/wiki/`

### Changed
- `typing.Callable` → `collections.abc.Callable` across the package (PEP 585)
- README: project retrospective notes (Grok's and Claude's)
- `scripts/release.sh`: version bump now edits only the `__version__` line instead of overwriting `coordinationhub/__init__.py` (which would have destroyed the package's re-exports)

### Removed
- Cursor and Kimi CLI hook adapters (`hooks/cursor.py`, `hooks/kimi_cli.py`) and their tests
- `~/.claude/settings.json` auto-install in `coordinationhub init` — hooks are now written only to the vendor-neutral `~/.coordinationhub/hooks.json`; `--auto-dashboard` is a deprecated no-op
- Spawn-source vocabulary reduced to `external` / `stdio_adapter` / `cc`

## [0.7.12] - 2026-05-15

### Changed
- Consolidated release automation into a single robust `release.yml` workflow
- Added `scripts/release.sh` helper script for one-command releases
- Made GitHub Release creation idempotent
- Added support for manual `workflow_dispatch` releases

### Removed
- Deprecated separate `publish.yml` workflow (all logic now in `release.yml`)

## [0.7.11] - 2026-05-15

### Fixed
- Trusted Publisher configuration for reliable PyPI publishing

## [0.7.10] - 2026-05-15

### Added
- Combined GitHub Release + PyPI publishing workflow

[Unreleased]: https://github.com/IronAdamant/coordinationhub/compare/v1.1.1...HEAD
[1.1.1]: https://github.com/IronAdamant/coordinationhub/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/IronAdamant/coordinationhub/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/IronAdamant/coordinationhub/compare/v0.8.0...v1.0.0
[0.8.0]: https://github.com/IronAdamant/coordinationhub/compare/v0.7.12...v0.8.0
[0.7.12]: https://github.com/IronAdamant/coordinationhub/compare/v0.7.11...v0.7.12
[0.7.11]: https://github.com/IronAdamant/coordinationhub/compare/v0.7.10...v0.7.11
[0.7.10]: https://github.com/IronAdamant/coordinationhub/compare/v0.7.9...v0.7.10
