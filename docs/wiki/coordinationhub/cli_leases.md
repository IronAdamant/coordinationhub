# coordinationhub/cli_leases.py

CLI commands for HA coordinator lease management: acquire/refresh/release the leadership lease, query the current leader, claim leadership from a failed leader, and an HA dashboard view.

## Key Functions / Classes
- `cmd_acquire_coordinator_lease(engine, args)` — attempts lease acquisition; on failure prints the current holder and expiry.
- `cmd_refresh_coordinator_lease(engine, args)` — refreshes TTL; returns exit code 4 when the caller is not the lease holder.
- `cmd_release_coordinator_lease(engine, args)` — releases the lease.
- `cmd_get_leader` / `cmd_leader_status` / `cmd_ha_dashboard` — read-only views over `engine.manage_leases(action="get")` (all `replica=True`).
- `cmd_claim_leadership(engine, args)` — `engine.manage_leases(action="claim", ...)` to take over from a failed leader.

## Design Notes
- T3.16 tail in `cmd_refresh_coordinator_lease`: exit codes propagate even with `--json` so scripts can branch on rc without re-parsing JSON. "Not the current lease holder" is a denial (exit 4, message on stderr), mirroring `cmd_acquire_lock`'s denial path — not a not-found (3).
- Three commands (`get-leader`, `leader-status`, `ha-dashboard`) are thin variations over the same `manage_leases(action="get")` call, differing only in rendering verbosity.
- TTL defaults are engine-side (parser passes `--ttl` default `None`; engine default is 10 s per `cli_parser` help text).

## Relationships
Imports: `.cli_utils` (`print_json`, `command`).
Imported by: `coordinationhub/cli_commands.py` (re-export hub); tests `tests/test_cli.py` / `tests/test_cli_integration.py` (e.g. `cmd_ha_dashboard`, `cmd_get_leader`, `cmd_refresh_coordinator_lease`).
