# coordinationhub/cli_spawner.py

CLI commands for the HA coordinator spawner — managing the sub-agent spawn registry: registering spawn intent, reporting actual spawns, listing/cancelling pending spawns, and graceful child-agent shutdown.

## Key Functions / Classes
- `cmd_spawn_subagent(engine, args)` — registers intent to spawn a sub-agent (type, description, prompt, source); prints the `spawn_id`.
- `cmd_report_subagent_spawned(engine, args)` — reports that an external system actually spawned a child, matching it to a pending spawn record if one exists.
- `cmd_list_pending_spawns(engine, args)` — lists pending (or, with `--all`, also registered/expired) spawns with age and `[EXPIRED]`/`[REGISTERED]` markers.
- `cmd_cancel_spawn(engine, args)` — cancels a pending spawn; not-found → exit 3, other failure → exit 1.
- `cmd_request_subagent_deregistration(engine, args)` — requests graceful stop of a child agent; exit 0/1/3 contract maintained even with `--json`.
- `cmd_await_subagent_stopped(engine, args)` — polls until the child is stopped or timeout; suggests `deregister_agent` escalation on timeout.

## Design Notes
- T3.16: not-found paths return exit code 3 with the message on stderr so stdout pipes don't pick up the error as successful output; `cmd_request_subagent_deregistration` propagates the same codes when `--json` is set so scripted callers can branch on rc without re-parsing JSON.
- T3.19: `cmd_cancel_spawn` routes through `engine.cancel_spawn` instead of reaching into `engine._connect` + spawner primitives directly.
- T7.9: missing `created_at` renders as `age=unknown` — the previous `s.get('created_at', 0)` printed a ~1.8e9-second age on a missing/corrupt column.

## Relationships
Imports: `.cli_utils` (`print_json`, `command`). (The vestigial `from . import spawner` left over from T3.19 was removed 2026-06-11.)
Imported by: `coordinationhub/cli_commands.py` (re-export hub); tests `tests/test_cli.py` / `tests/test_cli_integration.py` (e.g. `cmd_cancel_spawn`, `cmd_request_subagent_deregistration`).
