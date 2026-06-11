# coordinationhub/cli_parser.py

Argparse construction for the entire CoordinationHub CLI — extracted from `cli.py` to keep the entry point under the 500-LOC module budget. Pure parser building; no command handlers.

## Key Functions / Classes
- `create_parser() -> argparse.ArgumentParser` — top-level builder; registers all subcommands via the topical `_add_*` helpers.
- `_make_shared()` — parent parser with flags every command inherits: `--storage-dir`, `--project-root`, `--namespace` (default `hub`), `-j/--json`, `--replica`.
- `_add_serve`, `_add_identity`, `_add_locking`, `_add_broadcast_handoff`, `_add_notifications`, `_add_visibility`, `_add_setup`, `_add_messaging`, `_add_tasks`, `_add_intent_and_deps`, `_add_leases`, `_add_spawner` — one builder per command domain.

## Design Notes
- T7.7: `-j` is the only short flag in the shared set; other short forms would collide per-command (`-t` means ttl on some commands, timeout/tree on others) — deliberately scoped narrow.
- T7.19: acquire-lock retry knobs bake units into the flag names (`--backoff-ms`, `--timeout-ms`) because the backoff ladder starts sub-second; all other timeout flags in the CLI are in seconds.
- T6.18: canonical names carry deprecated aliases — `--max-age-seconds` (alias `--max-age`) on prune-notifications; `--poll-interval` (alias `--interval`) on watch.
- T2.1: `serve`/`serve-sse` default to bearer auth with a generated token; `--auth-token` pins one, `--no-auth` disables (local-trust only).
- Subcommand dest is `command`; every subparser takes `parents=[shared]`, so handlers can rely on the shared attrs existing.
- A new subparser here must be paired with a `_COMMANDS` entry in `cli.py` and a handler re-exported by `cli_commands.py`.

## Relationships
Imports: none within the coordinationhub package (stdlib `argparse` only).
Imported by: `coordinationhub/cli.py` (`from .cli_parser import create_parser`); tests `tests/test_setup.py`, `tests/test_cli.py`.
