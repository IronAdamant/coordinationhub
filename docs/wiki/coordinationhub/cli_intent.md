# coordinationhub/cli_intent.py

CLI commands for the work intent board — lightweight, TTL-bound declarations of which file an agent intends to work on (advisory, unlike locks).

## Key Functions / Classes
- `cmd_declare_work_intent(engine, args)` — declares `agent_id` → `document_path` intent with a free-text description and TTL (default 60 s).
- `cmd_get_work_intents(engine, args)` — lists active intents, optionally filtered by `--agent-id` (replica-capable).
- `cmd_clear_work_intent(engine, args)` — clears the calling agent's declared intent.

## Design Notes
- Smallest of the CLI domain modules; pure presentation over `engine.declare_work_intent` / `get_work_intents` / `clear_work_intent`.
- Intents expire via TTL (engine-side housekeeping `prune_work_intents` per the note in `core.py`); the CLI never deletes expired rows itself.
- `get-work-intents` is the only read path and uses `@_command(replica=True)` for direct WAL reads with `--replica`; the two mutating commands use the default writer engine.
- All three handlers honor the shared `-j/--json` flag via `_print_json`.

## Relationships
Imports: `.cli_utils` (`print_json`, `command`).
Imported by: `coordinationhub/cli_commands.py` (re-export hub).
