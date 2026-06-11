# coordinationhub/cli.py

CLI entry point and dispatch table for the `coordinationhub` command. Maps ~80 subcommand names to handler functions; parser construction lives in `cli_parser`, handlers in `cli_commands` (re-exported from the `cli_*` sub-modules).

## Key Functions / Classes
- `_COMMANDS` — dict mapping subcommand name (e.g. `"acquire-lock"`) to handler function name (e.g. `"cmd_acquire_lock"`).
- `_get_handler(name)` — lazy `from . import cli_commands` lookup, so importing `cli` stays cheap.
- `main(argv=None) -> int` — parses args, dispatches, converts handler return values and exceptions into exit codes.

## Design Notes
- T3.16 exit-code contract: handlers that return an explicit `int` propagate it as the process exit code (3 = not found, 4 = denied/conflict); a `None` return means success (0).
- Unhandled exceptions print `{"error": ...}` JSON when `--json` is set, otherwise `Error: ...` to stderr, and exit 1. `SystemExit` is re-raised untouched.
- No subcommand → prints help, exits 0; unknown command in the table → help, exits 1.
- Handlers are resolved by name at dispatch time (lazy import of `cli_commands`), keeping startup fast for `--help`.

## Relationships
Imports: `.cli_parser` (`create_parser`); `.cli_commands` (lazily, inside `_get_handler`).
Imported by: `coordinationhub/__main__.py` (`from .cli import main`); console-script entry `coordinationhub = "coordinationhub.cli:main"` in pyproject.toml; tests `tests/test_cli.py`, `tests/test_setup.py` (use `create_parser`, `_COMMANDS`, `_get_handler`).
