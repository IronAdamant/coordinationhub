# coordinationhub/schemas/leases.py

HA Coordinator Leases MCP tool schemas for CoordinationHub. Pure data declarations (no logic) for coordinator-leadership lease acquisition and management (high-availability failover).

## Key Functions / Classes
- `TOOL_SCHEMAS_LEASES: dict[str, dict]` — the only symbol; declares two tools:
  - `acquire_coordinator_lease` — attempt to acquire the coordinator leadership lease (`COORDINATOR_LEADER`); on success the agent becomes the active coordinator; `ttl` default 10 s
  - `manage_leases` — unified lease management dispatched on `action`: `acquire` | `refresh` | `release` | `get` (returns current leader) | `claim` (failover claim); `ttl` applies to acquire/claim (default 10 s at the engine, `minimum: 0`)

## Design Notes
- Pure data declarations — no logic; re-exported by `coordinationhub.schemas` (module docstring).
- `acquire_coordinator_lease` overlaps with `manage_leases(action='acquire')`; the standalone tool is the dedicated leadership entry point while `manage_leases` covers the full lease lifecycle including `claim` for failover.
- The TTL default (10 s) is applied at the engine, not in the schema — the schema documents it in the description instead of setting a `default`.
- Lease name `COORDINATOR_LEADER` is fixed by the engine; there is no lease-name parameter.

## Relationships
Imports: none within the package (only `from __future__ import annotations`).
Imported by: `coordinationhub/schemas/__init__.py` (merged into the aggregated `TOOL_SCHEMAS` and re-exported in `__all__`).
