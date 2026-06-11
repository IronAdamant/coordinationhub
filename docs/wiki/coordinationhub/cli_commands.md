# coordinationhub/cli_commands.py

Pure re-export hub for all CLI command handlers. Aggregates every `cmd_*` function from the domain-specific `cli_*` sub-modules so `cli.py`'s lazy importer only needs to import one module.

## Key Functions / Classes
- No definitions of its own — exclusively `from .cli_<domain> import cmd_*` statements.
- Re-exports, grouped by source module: `cli_agents` (serve/status/register/heartbeat/deregister/list-agents/agent-relations), `cli_locks` (locks, broadcasts, handoffs, messaging, await-agent), `cli_vis` (notifications, conflicts, spec, dashboard, assess, agent-tree), `cli_setup` (init/doctor/watch/auto-start-dashboard), `cli_tasks` (task registry + DLQ), `cli_intent` (work intents), `cli_deps` (cross-agent dependencies), `cli_leases` (HA coordinator leases), `cli_spawner` (sub-agent spawn registry), `cli_sse` (`cmd_serve_sse`).

## Design Notes
- Exists purely as an import indirection layer: `cli.py._get_handler` does `from . import cli_commands` once at dispatch time and resolves handlers via `getattr`, so adding a new command means (1) define handler in a domain module, (2) re-export here, (3) add to `_COMMANDS` in `cli.py`, (4) add parser in `cli_parser.py`.
- Importing this module pulls in every command sub-module (and transitively `cli_utils` → `core`), which is why `cli.py` defers the import until after argument parsing.
- Names re-exported here must exactly match handler-name strings in `cli.py._COMMANDS` — a typo surfaces as `AttributeError` at dispatch, not import time.

## Relationships
Imports: `.cli_agents`, `.cli_locks`, `.cli_vis`, `.cli_setup`, `.cli_tasks`, `.cli_intent`, `.cli_deps`, `.cli_leases`, `.cli_spawner`, `.cli_sse`.
Imported by: `coordinationhub/cli.py` (lazily, inside `_get_handler`).
