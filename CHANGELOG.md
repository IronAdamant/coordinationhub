# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/IronAdamant/coordinationhub/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/IronAdamant/coordinationhub/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/IronAdamant/coordinationhub/compare/v0.8.0...v1.0.0
[0.8.0]: https://github.com/IronAdamant/coordinationhub/compare/v0.7.12...v0.8.0
[0.7.12]: https://github.com/IronAdamant/coordinationhub/compare/v0.7.11...v0.7.12
[0.7.11]: https://github.com/IronAdamant/coordinationhub/compare/v0.7.10...v0.7.11
[0.7.10]: https://github.com/IronAdamant/coordinationhub/compare/v0.7.9...v0.7.10
