# coordinationhub/cli_setup_doctor.py

Diagnostic checks behind `coordinationhub doctor` — extracted from `cli_setup` so both modules stay under the 500-LOC budget. Each `_check_*` probes one layer independently and returns `(ok, message)`.

## Key Functions / Classes
- `_check_import()` — verifies `import coordinationhub` works in-process.
- `_check_hooks_config()` — looks for a hooks config at the vendor-neutral `~/.coordinationhub/hooks.json` (falling back to `~/.claude/settings.json`) containing all seven required hook events with a `coordinationhub` command. Missing config is a soft pass ("run 'coordinationhub init' if using an IDE with hooks").
- `_check_storage_dir()` — confirms `.coordinationhub/` and `coordination.db` exist for the detected project root (or `~` fallback).
- `_check_schema_version()` — opens the DB directly with sqlite3 and compares `schema_version` to `db._CURRENT_SCHEMA_VERSION`.
- `_check_hook_python()` — resolves the interpreter the hooks use (parsed from settings command, else `python3` on PATH) and runs `import coordinationhub` in a 10 s subprocess.
- `run_doctor() -> list[dict]` — runs all checks, catching crashes per-check; returns `{name, ok, message}` rows.
- `cmd_doctor(args)` — prints `[OK]`/`[FAIL]` lines or `{"checks": [...], "all_ok": ...}` JSON.

## Design Notes
- T6.20: `cmd_doctor` intentionally skips `@_command` — the decorator constructs a CoordinationEngine first, but doctor's whole job is diagnosing states where engine startup FAILS (corrupt DB, stuck migration, permission denied). It probes each layer independently instead.
- `_check_schema_version` deliberately bypasses the engine and reads SQLite directly for the same reason.
- `_CLAUDE_SETTINGS_PATH` (`~/.claude/settings.json`) is still consulted read-only as a legacy fallback for hooks/interpreter detection, even though `cli_setup` no longer writes there.
- `run_doctor` never raises: a crashing check is reported as `check crashed: ...`.

## Relationships
Imports: `.cli_utils` (`print_json`); lazily `.paths` (`detect_project_root`), `.db` (`_CURRENT_SCHEMA_VERSION`).
Imported by: `coordinationhub/cli_setup.py` (re-exports `cmd_doctor`/`run_doctor` for `cli_commands.py`).
