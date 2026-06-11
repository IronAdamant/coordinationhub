# coordinationhub/cli_deps.py

CLI commands for cross-agent dependency declarations: declare, query, satisfy, and list dependencies between agents (optionally pinned to a specific task).

## Key Functions / Classes
- `cmd_declare_dependency(engine, args)` — declares dependent → depends-on edge with optional `--depends-on-task-id` and `--condition` (default `task_completed`).
- `cmd_manage_dependencies(engine, args)` — unified query; `assert` mode prints can-start/blockers, `check`/`blockers` modes print blocked state and unsatisfied deps.
- `cmd_satisfy_dependency(engine, args)` — marks a dependency row satisfied by numeric `dep_id`.
- `cmd_get_all_dependencies(engine, args)` — lists all declared dependencies, optionally filtered by dependent agent (replica-capable).

## Design Notes
- T7.5: text output uses ASCII tags `[OK]` / `[PENDING]` and `->` instead of ✓/✗/→ glyphs — cp1252 stdout (Windows default before PowerShell 7.2) dies on those code points.
- All handlers use the `@_command` decorator from `cli_utils`, so engine creation/cleanup is automatic; only `get-all-dependencies` is read-only (`replica=True`).
- Thin presentation layer: all logic lives in `engine.declare_dependency` / `manage_dependencies` / `satisfy_dependency` / `get_all_dependencies` (see `core.py`).

## Relationships
Imports: `.cli_utils` (`print_json`, `command`).
Imported by: `coordinationhub/cli_commands.py` (re-export hub).
