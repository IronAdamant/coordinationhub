# coordinationhub/schemas/deps.py

Cross-Agent Dependencies MCP tool schemas for CoordinationHub. Pure data declaration (no logic) for the single unified dependency-management tool.

## Key Functions / Classes
- `TOOL_SCHEMAS_DEPS: dict[str, dict]` — the only symbol; declares one tool:
  - `manage_dependencies` — unified dependency management dispatched on `mode`:
    - `declare` — create a dependency (requires `dependent_agent_id` + `depends_on_agent_id`; optional `depends_on_task_id`, `condition` default `"task_completed"`)
    - `check` / `blockers` / `assert` — query blockers for an `agent_id`
    - `satisfy` — mark a dependency satisfied (requires `dep_id`)
    - `list` — list all dependencies
    - `wait` — poll until a `dep_id` is satisfied (`timeout_s` default 60, `poll_interval_s` default 2)

## Design Notes
- Pure data declarations — no logic; re-exported by `coordinationhub.schemas` (module docstring).
- Single multiplexed tool: only `mode` is in `required`; per-mode field requirements are documented in descriptions (e.g. "Required for declare") rather than enforced via `oneOf` branches (contrast with `intent.py` / `messaging.py`).
- Field semantics shift with mode: `agent_id` serves check/blockers/assert/list, `dep_id` serves satisfy/wait.

## Relationships
Imports: none within the package (only `from __future__ import annotations`).
Imported by: `coordinationhub/schemas/__init__.py` (merged into the aggregated `TOOL_SCHEMAS` and re-exported in `__all__`).
