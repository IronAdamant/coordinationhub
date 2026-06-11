# coordinationhub/schemas/intent.py

Work Intent Board MCP tool schemas for CoordinationHub. Pure data declaration (no logic) for the unified work-intent tool, with per-action required-field enforcement.

## Key Functions / Classes
- `TOOL_SCHEMAS_INTENT: dict[str, dict]` — the only symbol; declares one tool:
  - `manage_work_intents` — unified intent management dispatched on `action`:
    - `declare` — declare a free-text intent against a `document_path`; `intent` is capped at `MAX_INTENT` chars, `ttl` default 60 s (intent expiry)
    - `get` — read intents (needs only `agent_id`)
    - `clear` — clear intents (needs only `agent_id`)

## Design Notes
- Pure data declarations — no logic; re-exported by `coordinationhub.schemas` (module docstring).
- T7.46 (module docstring): `declare` requires `document_path` and `intent` while `get`/`clear` don't. A flat `required` list cannot express that, so the schema carries an additional `oneOf` whose branches pin per-action required fields; the validator walks the `oneOf` and picks the matching sub-schema.
- `intent.maxLength` is sourced from `coordinationhub.limits.MAX_INTENT` (env-overridable, default 1000), keeping the schema and the runtime cap in sync.
- One of only two schema modules (with `tasks.py`) that imports from `..limits`.

## Relationships
Imports: `coordinationhub.limits` (`MAX_INTENT`, via `from ..limits import MAX_INTENT`).
Imported by: `coordinationhub/schemas/__init__.py` (merged into the aggregated `TOOL_SCHEMAS` and re-exported in `__all__`).
