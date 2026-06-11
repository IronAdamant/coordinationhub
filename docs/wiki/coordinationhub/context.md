# coordinationhub/context.py

Context bundle builder for agent registration responses — assembles the dict returned by `register_agent` (sibling agents, active locks, recent notifications, coordination URL, graph-responsibility data, owned files, optional descendant status).

## Key Functions / Classes
- `build_context_bundle(connect_fn, agent_id, parent_id, project_root, graph_getter, list_agents_fn, default_port=9877, descendants_fn=None, read_connect_fn=None)` — the single public function; returns the bundle dict.
- `DEFAULT_PORT = 9877` — fallback port for the `coordination_url` field.

## Design Notes
- Zero internal dependencies on other coordinationhub modules (by design); all collaborators are injected callables.
- T7.29: when `read_connect_fn` is supplied (storage's `read_only_connection`), the inline SELECTs (locks, notifications, responsibilities, owned files) use it instead of `connect_fn`, so a burst of registrations doesn't serialize the writer pool on pure reads. Falls back to `connect_fn` when None.
- `dict.get(key, default)` gotcha documented in code: a present-but-NULL `responsibilities` column returns `None`, not the default — hence `resp.get("responsibilities") or "[]"` before `json.loads`.
- Active locks are filtered by `now < locked_at + lock_ttl`; notifications limited to the last 300 s, 20 rows.
- `coordination_url` honors the `COORDINATIONHUB_COORDINATION_URL` env var; other MCP servers (Stele, Chisel, Trammel) are deliberately not included in the bundle.
- `responsibilities` / `role` / `graph_agent_id` / `current_task` keys appear only when an `agent_responsibilities` row exists; `owned_files` and `descendants_status` are likewise conditional.

## Relationships
Imports: none from the package at runtime (`plugins.graph.graphs` only under `TYPE_CHECKING`).
Imported by: `identity_subsystem.py` (the only in-package consumer; also exercised directly by `tests/test_db_safety.py`).
