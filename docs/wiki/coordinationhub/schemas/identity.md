# coordinationhub/schemas/identity.py

Identity & Registration MCP tool schemas for CoordinationHub. Pure data declarations (no logic) for the agent lifecycle: register, heartbeat, deregister, and relationship queries.

## Key Functions / Classes
- `TOOL_SCHEMAS_IDENTITY: dict[str, dict]` — the only symbol; declares five tools:
  - `register_agent` — register an agent and receive a context bundle (siblings, active locks, coordination URLs, and graph responsibilities/owned files if a coordination graph is loaded). Requires `agent_id` (minLength 1); optional `parent_id` (for spawned sub-agents), `graph_agent_id` (graph role e.g. 'planner'), `worktree_root`.
  - `heartbeat` — keep the agent alive; description says call at least every 30 seconds
  - `deregister_agent` — deregister, orphan children to the grandparent, and release all the agent's locks
  - `list_agents` — list registered agents; `active_only` default True, `stale_timeout` default 600 s for stale detection via heartbeat age
  - `get_agent_relations` — `mode='lineage'` (default; ancestor chain + descendants) or `mode='siblings'` (same parent)

## Design Notes
- Pure data declarations — no logic; re-exported by `coordinationhub.schemas` (module docstring).
- Optional registration fields are genuinely omitted when unused (no `default: None`), e.g. omit `parent_id` for root agents.
- Deregistration is the cleanup point: children are reparented to the grandparent and locks released, so the lifecycle invariant is "no active locks owned by stopped agents".
- `agent_id` convention shown in description: `hub.12345.0`.

## Relationships
Imports: none within the package (only `from __future__ import annotations`).
Imported by: `coordinationhub/schemas/__init__.py` (merged into the aggregated `TOOL_SCHEMAS` and re-exported in `__all__`).
