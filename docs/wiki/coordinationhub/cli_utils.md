# coordinationhub/cli_utils.py

Shared CLI helpers used by every `cli_*` command module: JSON printing, engine construction from parsed args, safe close, and the `command` decorator that removes engine-lifecycle boilerplate from handlers.

## Key Functions / Classes
- `print_json(data)` — `json.dumps(..., indent=2, default=str)` to stdout (the `default=str` keeps Paths/timestamps serializable).
- `engine_from_args(args) -> CoordinationEngine` — builds and `start()`s a writer engine from `--storage-dir` / `--project-root` / `--namespace`.
- `replica_engine_from_args(args)` — when `--replica` is set, returns `engine.read_only_engine()` (direct WAL read, no writer round-trip); otherwise falls back to `engine_from_args`.
- `close(engine)` — best-effort `engine.close()`; exceptions are logged at debug level, never propagated.
- `command(*, replica=False)` — decorator: the wrapped handler receives `(engine, args)` instead of `(args,)`; engine creation (replica-capable when `replica=True`) and cleanup in `finally` are automatic, and the handler's return value (exit code) passes through.

## Design Notes
- This is the only `cli_*` module that imports `core` eagerly, so importing any command module pulls in the engine — one reason `cli.py` defers importing `cli_commands` until dispatch.
- `replica=True` on the decorator marks read-only commands; the replica path still constructs (and starts) a writer engine first, then derives the read-only view via `read_only_engine()`.
- Handlers that manage their own lifecycle (`cmd_serve`, `cmd_serve_sse`, `cmd_init`, `cmd_doctor`, `cmd_auto_start_dashboard`, `cmd_watch`) intentionally skip the decorator (see T6.20 notes in those modules).
- `functools.wraps` preserves handler names, which `cli.py` resolves by string via `getattr`.

## Relationships
Imports: `.core` (`CoordinationEngine`).
Imported by: `coordinationhub/cli_agents.py`, `cli_deps.py`, `cli_intent.py`, `cli_leases.py`, `cli_locks.py`, `cli_spawner.py`, `cli_tasks.py`, `cli_vis.py`, `cli_setup_doctor.py` (eagerly), and `cli_setup.py` (lazily inside `cmd_watch`).
