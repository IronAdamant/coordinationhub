# coordinationhub/plugins/assessment/__init__.py

Assessment plugin entry point: re-exports the runner API from `.assessment` and provides the plugin registration hooks consumed by `plugins.registry`.

## Key Functions / Classes
- Re-exports: `build_suite_from_db`, `build_trace_from_db`, `run_assessment`, `store_assessment_results`, `prune_assessment_results` (all from `.assessment`).
- `register_tools(dispatch_table)` — registers `run_assessment` (`suite_path`, `format`, `graph_agent_id`) and `assess_current_session` (`format`, `graph_agent_id`, `scope`) as `name -> (engine_method, [args])` entries.
- `register_cli(subparsers)` — no-op; assessment CLI is handled by the existing `cli_vis.py` dispatch.

## Design Notes
- Loaded dynamically by `registry._load_plugin("assessment")`; must keep the `register_tools` / `register_cli` module-level contract.

## Relationships
Imports: `.assessment`.
Imported by: `coordinationhub/plugins/registry.py` (dynamic `__import__`), `coordinationhub/visibility_subsystem.py` (via `from .plugins.assessment import assessment`).
