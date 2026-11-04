# Changelog

## [1.0.0] - 2026-11-04

Rust rewrite. Standard library only. The Python 0.8 tree stays on `python-legacy`.

### Added
- `coordinationhub` and `coordinationhub-mcp` binaries
- Work claims (`claim_work`, `release_work`, `list_work`), `inbox`, and `compact_status`
- Parity harness (`parity/harness.py`) and benchmark gate (`parity/bench.py`)
- Release workflow for Linux x64, Linux arm64, macOS x64, macOS arm64, and Windows x64, with SHA256SUMS

### Changed
- On-disk store is `.coordinationhub/state.json` rather than SQLite. See MIGRATION.md.
- Stdio MCP server no longer needs the optional Python `mcp` package.

## [Unreleased]

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
