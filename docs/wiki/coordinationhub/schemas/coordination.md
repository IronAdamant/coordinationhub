# coordinationhub/schemas/coordination.py

Coordination Actions MCP tool schemas for CoordinationHub. Pure data declarations (no logic) for inter-agent announcement, acknowledgment, and waiting primitives.

## Key Functions / Classes
- `TOOL_SCHEMAS_COORDINATION: dict[str, dict]` — the only symbol; declares five tools:
  - `broadcast` — announce an intention to all live siblings; reports live siblings and lock conflicts. `handoff_targets` turns it into a formal multi-recipient handoff (recorded in the handoffs table); `require_ack=True` creates a trackable broadcast record and sends ack-request messages. Sibling staleness cutoff `ttl` defaults to 30 s.
  - `acknowledge_broadcast` — recipient acknowledges a broadcast by `broadcast_id` + `agent_id`
  - `wait_for_broadcast_acks` — poll until all expected acks arrive or `timeout_s` (default 30) expires
  - `wait_for_locks` — poll until all locks on `document_paths` are released or `timeout_s` (default 60) expires
  - `await_agent` — wait for an agent to complete (deregister); for sequential dependencies between agents (`timeout_s` default 60)

## Design Notes
- Pure data declarations — no logic; re-exported by `coordinationhub.schemas` (module docstring).
- `broadcast` is overloaded by design: plain announcement, formal handoff (via `handoff_targets`), or tracked acknowledged broadcast (via `require_ack` + optional `message`).
- The ack flow is a three-step protocol: `broadcast(require_ack=True)` → recipients call `acknowledge_broadcast` → sender calls `wait_for_broadcast_acks`.
- All waiting tools are poll-based with timeouts; none block indefinitely.

## Relationships
Imports: none within the package (only `from __future__ import annotations`).
Imported by: `coordinationhub/schemas/__init__.py` (merged into the aggregated `TOOL_SCHEMAS` and re-exported in `__all__`).
