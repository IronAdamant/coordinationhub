# coordinationhub/cli_setup.py

CLI commands for setup and diagnostics: `init`, `doctor` (re-exported from `cli_setup_doctor`), `auto-start-dashboard`, and `watch`. Writes the vendor-neutral hooks config and bootstraps the storage directory + database.

## Key Functions / Classes
- `cmd_init(args)` — detects project root, creates `.coordinationhub/` storage, runs the first engine migration, writes the hooks config to `~/.coordinationhub/hooks.json`, then runs `run_doctor()`. Flags: `--auto-dashboard` (now deprecated no-op), `--monitor-skill`.
- `cmd_auto_start_dashboard(args) -> int` — idempotent pre-flight: probes host:port with a 0.3 s socket connect; if free, spawns `serve-sse --no-browser` as a detached subprocess logging to `~/.coordinationhub/dashboard.log`. Always exits 0.
- `cmd_watch(args)` — live agent-tree refresh loop; ANSI clear-screen each tick, fresh engine per iteration, Ctrl+C to stop.
- `_HOOKS_CONFIG` / `_fill_hook_command(config, python_path)` — hook-event template (SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, SubagentStart/Stop, SessionEnd) with the stdio_adapter command filled in.
- `_install_auto_dashboard_hook(python_path)` — deprecation notice only: Claude Code settings.json integration has been removed; prints manual `serve-sse` instructions instead.
- `_install_monitor_skill()` — copies `data/monitor_skill.md` to `~/.coordinationhub/skills/coordinationhub-monitor/SKILL.md` (vendor-neutral path).

## Design Notes
- Hooks are written ONLY to the vendor-neutral `~/.coordinationhub/hooks.json`; the working tree removes all writes to Claude Code's `~/.claude/settings.json`. `--auto-dashboard` is kept for CLI compatibility but is a no-op.
- T6.20: all three handlers intentionally skip the `@_command` decorator — `init` must create the storage dir before any engine exists; `auto-start-dashboard` never constructs an engine (decorator would add DB startup cost to every IDE SessionStart hook); `watch` needs a fresh engine per tick so stale in-memory caches don't produce stale renders.
- T3.18: `watch` uses ANSI `\x1b[2J\x1b[H` instead of shelling out to `clear`/`cls` (no subprocess per tick; works on Windows 10+ VT terminals).
- Hook commands run `{python} -m coordinationhub.hooks.stdio_adapter` with the interpreter captured from `sys.executable` at init time.

## Relationships
Imports: `.cli_setup_doctor` (`cmd_doctor`, `run_doctor`); lazily `.paths` (`detect_project_root`), `.core` (`CoordinationEngine`), `.cli_utils` (`engine_from_args`, `close` in `cmd_watch`).
Imported by: `coordinationhub/cli_commands.py` (re-export hub); tests `tests/test_setup.py`, `tests/test_cli.py`, `tests/test_cli_integration.py`.
