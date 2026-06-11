# coordinationhub/plugins/graph/graphs.py

Declarative coordination graph: loader (YAML via ruamel.yaml, or JSON), schema validator, in-memory `CoordinationGraph` representation, a module-level singleton, and the graph MCP-tool implementations used by core.

## Key Functions / Classes
- `CoordinationGraph` — slotted in-memory graph with lookups: `agents` (id → def), `handoffs`, `escalation`, `assessment`, `agent(id)`, `outgoing_handoffs(from_id)`, `handoff_targets(from_id)`, `is_valid()` / `validation_errors()`.
- `validate_graph(data)` — returns `{valid, errors}`; per-section validators check required/unknown fields for agents (`id`, `role`, `responsibilities`, optional `model`), handoffs (`from`, `to`, `condition`, ids must be defined agents), escalation (`max_retries`, `fallback`), assessment (`metrics` list of strings).
- `load_graph(path)` — YAML or JSON by suffix; raises `ImportError` with install hint if YAML is requested but ruamel.yaml is missing.
- `find_graph_spec(project_root)` — looks for `coordination_spec.yaml` / `.yml` / `.json` at project root, in that order.
- `set_graph(data)` / `get_graph()` / `clear_graph()` — module-level singleton accessors for the currently loaded graph.
- `load_coordination_spec_from_disk(connect, project_root, path)` — load/validate/set; on success pre-populates `agent_responsibilities` for registered agents whose agent_id matches a graph agent id; any failure clears the graph and returns `loaded=False` with error info.
- `build_implicit_graph(connect)` — fallback graph synthesized from the live agent tree (orchestrator root + worker nodes + spawn handoffs) so scan/assessment work without a spec file.
- `validate_graph_tool()` — MCP tool: validate the currently loaded graph.

## Design Notes
- Zero internal deps on other coordinationhub modules (per module docstring); YAML support degrades gracefully to JSON-only (`_YAML_AVAILABLE`).
- T6.2: `_populate_agent_responsibilities_from_graph` prefetches active agent ids on one connection then `executemany`s upserts — the pre-fix loop opened 2 connections per graph agent.
- The loaded graph is process-global state (`_loaded_graph`); validation failure or load error always `clear_graph()`s rather than leaving a stale graph.
- Implicit-graph roles are derived from the agent_id suffix (numeric suffix → "worker", otherwise the suffix itself).

## Relationships
Imports: none within the coordinationhub package (stdlib + optional `ruamel.yaml`).
Imported by: `coordinationhub/core.py`, `coordinationhub/context.py`, `coordinationhub/change_subsystem.py`, `coordinationhub/visibility_subsystem.py`, `coordinationhub/identity_subsystem.py` (all as `from .plugins.graph import graphs as _g`), plus `coordinationhub/plugins/graph/__init__.py` (re-exports).
