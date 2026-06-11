# coordinationhub/plugins/graph/__init__.py

Graph plugin entry point: re-exports the coordination-graph API from `.graphs` and registers the graph MCP tools.

## Key Functions / Classes
- Re-exports: `CoordinationGraph`, `clear_graph`, `find_graph_spec`, `get_graph`, `load_coordination_spec_from_disk`, `load_graph`, `set_graph`, `validate_graph`.
- `register_tools(dispatch_table)` — registers `load_coordination_spec` (`path`) and `validate_graph` (no args) as `name -> (engine_method, [args])` entries.
- `register_cli(subparsers)` — no-op.

## Design Notes
- Loaded dynamically by `registry._load_plugin("graph")`; must keep the `register_tools` / `register_cli` contract.
- Core/subsystem modules bypass this package `__init__` and import `coordinationhub.plugins.graph.graphs` directly as `_g`.

## Relationships
Imports: `.graphs`.
Imported by: `coordinationhub/plugins/registry.py` (dynamic `__import__`); `core.py`, `context.py`, `change_subsystem.py`, `visibility_subsystem.py`, `identity_subsystem.py` import the `.graphs` submodule through this package.
