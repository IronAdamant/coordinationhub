# coordinationhub/schemas/messaging.py

Messaging MCP tool schemas for CoordinationHub. Pure data declarations (no logic) for direct agent-to-agent messages and the unified inbox tool.

## Key Functions / Classes
- `TOOL_SCHEMAS_MESSAGING: dict[str, dict]` — the only symbol; declares two tools:
  - `send_message` — direct message for query/response patterns; requires `from_agent_id`, `to_agent_id`, `message_type`; optional dict `payload` and `caller_agent_id` assertion
  - `manage_messages` — unified `send | get | mark_read` dispatched on `action`; `unread_only` (default False), `limit` (default 50), `message_ids` for mark_read, `since_id` cursor for incremental polling on get

## Design Notes
- Pure data declarations — no logic; re-exported by `coordinationhub.schemas` (module docstring).
- T6.12 / T7.49 (docstring): `payload` is declared `object` (dict-like) but the schema constrains shape, not size — serialized JSON is truncated to `MAX_MESSAGE` at write time at the primitive (T6.14), so a malicious caller cannot wedge megabytes of JSON into SQLite.
- T7.46: per-action required fields enforced via `oneOf` — `send` requires the addressing triple (`from_agent_id`/`to_agent_id`/`message_type`); `get`/`mark_read` only need the recipient `agent_id`.
- T2.4 anti-impersonation: optional `caller_agent_id` must equal `from_agent_id` on send (and `agent_id` on get/mark_read) — blocks forged sender identity and cross-agent inbox siphoning.
- `MAX_MESSAGE` is referenced in descriptions only; this module does not import `..limits` (enforcement lives in the primitive layer).

## Relationships
Imports: none within the package (only `from __future__ import annotations`).
Imported by: `coordinationhub/schemas/__init__.py` (merged into the aggregated `TOOL_SCHEMAS` and re-exported in `__all__`).
