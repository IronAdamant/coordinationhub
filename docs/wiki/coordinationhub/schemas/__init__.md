# coordinationhub/schemas/__init__.py

Aggregation point for all CoordinationHub MCP tool schemas. Imports the fourteen per-domain schema modules and merges them into the single `TOOL_SCHEMAS` dict consumed by the HTTP server, stdio MCP server, and documentation generator.

## Key Functions / Classes
- `TOOL_SCHEMAS: dict[str, dict]` — union (via `|`) of all fourteen group dicts: IDENTITY, LOCKING, COORDINATION, CHANGE, AUDIT, VISIBILITY, MESSAGING, TASKS, INTENT, HANDOFFS, DEPS, DLQ, LEASES, SPAWNER
- `TOOLS_VERSION = "1.0.0"` — semantic version of the `TOOL_SCHEMAS` shape (T6.13)
- Re-exports every group dict (`TOOL_SCHEMAS_IDENTITY` … `TOOL_SCHEMAS_SPAWNER`) in `__all__` for callers that want a single domain without pulling the whole surface

## Design Notes
- Each functional group lives in its own sibling module; this package only aggregates — no schema definitions live here.
- T6.13 versioning policy (comment): bump major when a tool is renamed/removed; minor when a tool or parameter is added backwards-compatibly; patch for description-only edits. Lets clients pinning an older major detect breaking changes at handshake without diffing schema dicts.
- `TOOLS_VERSION` is exposed via `mcp_server` on `/tools` and `/health` responses and on the stdio MCP `tools/list` handshake.
- Dict-union merge order means a duplicate tool name across groups would silently win by later position — group modules must keep tool names globally unique.

## Relationships
Imports: `.identity`, `.locking`, `.coordination`, `.messaging`, `.change`, `.audit`, `.visibility`, `.tasks`, `.intent`, `.handoffs`, `.deps`, `.dlq`, `.leases`, `.spawner` (all fourteen sibling schema modules).
Imported by: `coordinationhub/mcp_server.py` (`TOOL_SCHEMAS`, `TOOLS_VERSION`), `coordinationhub/mcp_stdio.py` (`TOOL_SCHEMAS`), `coordinationhub/dispatch.py` (lazy `from .schemas import TOOL_SCHEMAS` for T6.11 argument validation), `scripts/gen_docs.py` (doc generation).
