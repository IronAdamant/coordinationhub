# coordinationhub/dispatch.py

MCP tool dispatch: holds both the dispatch table (`TOOL_DISPATCH`) and the dispatch function (`dispatch_tool`), shared by the HTTP and stdio transports.

## Key Functions / Classes
- `TOOL_DISPATCH: dict[str, tuple[str, list[str]]]` — maps ~50 tool names to `(engine_method_name, allowed_kwargs)`, grouped by domain (identity, locking, coordination, change awareness, tasks, DLQ, work intents, handoffs, dependencies, leases, spawner).
- `dispatch_tool(engine, tool_name, arguments)` — validates and forwards a tool call to the matching engine method; raises `ValueError` for unknown tools / schema violations, `TypeError` for engine-level signature mismatches.

## Design Notes
- T7.42: the function used to live in `mcp_server`, which was the wrong home (transport code shouldn't own dispatch logic); `mcp_server` retains a back-compat re-export of `dispatch_tool` which `mcp_stdio` still uses.
- T6.11: arguments are validated against `TOOL_SCHEMAS[tool_name]["parameters"]` before dispatch (lazy imports of `validation` and `schemas`). Pre-fix, schemas were display-only and bad input either corrupted DB state or raised opaque errors deep in a primitive. Validation is skipped when a schema is absent.
- T3.5: explicit `None` values are preserved (only keys outside `allowed_args` are dropped) so callees can distinguish "intentionally unset" from "missing" — stripping them caused spurious missing-argument errors for tools whose signature accepts `None`.
- Unknown argument keys are logged at WARNING so callers notice typos (`agent_ids` vs `agent_id`) instead of silently getting a later failure.
- Adding a tool requires a `TOOL_DISPATCH` entry whose kwarg list matches the engine facade signature, plus a `TOOL_SCHEMAS` entry for validation.

## Relationships
Imports: `validation` and `schemas` (both lazily inside `dispatch_tool`).
Imported by: `mcp_server.py` (re-exports `dispatch_tool`; `mcp_stdio.py` consumes it via that re-export) and `change_subsystem.py` (imports `TOOL_DISPATCH` only).
