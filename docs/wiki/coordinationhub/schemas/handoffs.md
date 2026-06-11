# coordinationhub/schemas/handoffs.py

Handoffs MCP tool schemas for CoordinationHub. Pure data declaration (no logic) for the single unified handoff-lifecycle tool.

## Key Functions / Classes
- `TOOL_SCHEMAS_HANDOFFS: dict[str, dict]` — the only symbol; declares one tool:
  - `wait_for_handoff` — unified handoff operation dispatched on `mode`:
    - `status` — return the handoff record
    - `ack` — acknowledge the handoff (requires `agent_id`)
    - `complete` — mark the handoff completed
    - `cancel` — cancel the handoff
    - `completion` (default) — wait for completion (`timeout_s` default 30)

## Design Notes
- Pure data declarations — no logic; re-exported by `coordinationhub.schemas` (module docstring).
- Despite the name, `wait_for_handoff` is the full handoff CRUD/wait surface; only `handoff_id` is in `required`, with the default `mode='completion'` preserving the original wait behavior.
- Handoff records are created elsewhere: `broadcast` (see `coordination.py`) with `handoff_targets` writes the handoffs table that this tool operates on.
- `agent_id` is only required for `mode='ack'` (stated in the field description, not enforced via `oneOf`).

## Relationships
Imports: none within the package (only `from __future__ import annotations`).
Imported by: `coordinationhub/schemas/__init__.py` (merged into the aggregated `TOOL_SCHEMAS` and re-exported in `__all__`).
