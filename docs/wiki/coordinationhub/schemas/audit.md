# coordinationhub/schemas/audit.py

Audit & Status MCP tool schemas for CoordinationHub. Pure data declarations (no logic) defining the parameter shapes for conflict-log and system-state inspection tools.

## Key Functions / Classes
- `TOOL_SCHEMAS_AUDIT: dict[str, dict]` — the only symbol; declares three tools:
  - `get_conflicts` — query the conflict log for lock steals and ownership violations (filters: `document_path`, `agent_id`, `limit` default 20)
  - `get_contention_hotspots` — rank files by lock-contention frequency to find coordination chokepoints (`limit` default 10)
  - `status` — summary of coordination system state (agents, locks, notifications, conflicts, whether a graph is loaded); takes no parameters

## Design Notes
- Pure data declarations — no logic; re-exported by `coordinationhub.schemas` (module docstring).
- Schemas follow JSON-Schema-style `parameters` objects; optional filters use `default: None`.
- `get_conflicts` is positioned for post-mortems and debugging agent interactions; `get_contention_hotspots` identifies files multiple agents fight over.
- All three tools are read-only queries; none mutate state.

## Relationships
Imports: none within the package (only `from __future__ import annotations`).
Imported by: `coordinationhub/schemas/__init__.py` (merged into the aggregated `TOOL_SCHEMAS` and re-exported in `__all__`).
