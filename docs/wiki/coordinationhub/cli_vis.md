# coordinationhub/cli_vis.py

Change awareness, audit, graph, and assessment CLI commands: change notifications, conflict log, contention hotspots, coordination-spec load/validate, project scan, dashboard, agent status/tree, and the assessment runner.

## Key Functions / Classes
- `cmd_notify_change` / `cmd_get_notifications` / `cmd_prune_notifications` / `cmd_wait_for_notifications` — change-event recording, polling, pruning, and long-poll waiting.
- `cmd_get_conflicts` / `cmd_contention_hotspots` — conflict-log queries and lock-contention ranking (replica-capable).
- `cmd_load_spec` / `cmd_validate_spec` — reload the coordination spec from disk; validate the loaded graph.
- `cmd_scan_project(engine, args)` — file-ownership scan with optional extension filter and worktree root.
- `cmd_dashboard(engine, args)` — agents/locks/tasks status table; auto-reaps stale agents first for consistent display (Review Fourteen).
- `cmd_agent_status` / `cmd_agent_tree` (+ `_render_agent_tree`) — per-agent detail and recursive hierarchy rendering.
- `cmd_assess(engine, args)` — runs an assessment suite or scores the live session; supports `--format`, `--output`, `--graph-agent-id`, `--scope`.
- `_validate_assess_output(output_path, engine)` — T2.8 guard for `--output` paths.

## Design Notes
- T7.13 in `cmd_prune_notifications`: at least one of `--max-age-seconds` / `--max-entries` is required (exit 2 otherwise) — previously both defaulting to `None` pruned nothing while operators expected "no args = prune everything".
- T3.21 in `cmd_assess`: `--output` replaces stdout instead of duplicating to both (an `--also-stdout` escape hatch is read via getattr but not yet exposed in the parser).
- T2.8: `_validate_assess_output` rejects symlinks at any path component and paths escaping the project root (the mentioned `--force-absolute` override is "not yet implemented").
- T7.5: ASCII `=` framing instead of `═` for cp1252 stdout — note the final line of `cmd_dashboard` still prints `"═" * 60` (inconsistent with the header fix).
- Read-only commands use `@_command(replica=True)`; mutating ones (`notify-change`, `prune-notifications`, `load-spec`, `validate-spec`, `scan-project`) use the writer engine.

## Relationships
Imports: `.cli_utils` (`print_json`, `command`); stdlib `pathlib.Path`.
Imported by: `coordinationhub/cli_commands.py` (re-export hub); tests `tests/test_setup.py` (`cmd_dashboard`), `tests/test_cli_integration.py` (`_validate_assess_output`). Referenced in a comment in `coordinationhub/plugins/assessment/__init__.py` (no import).
