# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-11-04

Rust rewrite of the Python 0.8.0 program. The Python tree remains on `python-legacy`.

### Added
- Stdio MCP server, HTTP dashboard server, and CLI, with no Cargo crates
- Every 0.8.0 CLI command and flag, parsed like Python 3.12's argparse (prefixes, typed and choice errors, help wrapped to the terminal), plus `init --monitor-skill`, the `serve-sse` browser launch and `auto-start-dashboard`
- Parity harness and benchmark gate as std-only examples (`examples/parity.rs`, `examples/bench.rs`), with `check_zero_deps` and `check_no_todos`; `gen_docs`, `propose_stale_phrases` and `update_dispatch_floor` are ported the same way
- `who` for a compact picture plus claim, handoff, and release of a work area
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

[Unreleased]: https://github.com/IronAdamant/coordinationhub/compare/v0.8.0...HEAD
[0.8.0]: https://github.com/IronAdamant/coordinationhub/compare/v0.7.12...v0.8.0
[0.7.12]: https://github.com/IronAdamant/coordinationhub/compare/v0.7.11...v0.7.12
[0.7.11]: https://github.com/IronAdamant/coordinationhub/compare/v0.7.10...v0.7.11
[0.7.10]: https://github.com/IronAdamant/coordinationhub/compare/v0.7.9...v0.7.10
