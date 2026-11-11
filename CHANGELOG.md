# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.2.0] - 2026-11-11

From Specula's M5 lanes (about fifteen lane reports). `MIGRATION.md` lists each difference from 0.8.0; `tests/m5_fixes.rs` has a test for each.

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

From the Specula live test (B12).

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
