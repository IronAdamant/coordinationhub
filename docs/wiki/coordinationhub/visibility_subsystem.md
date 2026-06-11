# coordinationhub/visibility_subsystem.py

Observability subsystem: coordination graph load/validate, project scan with ownership assignment, agent status/tree/file-map views, and assessment runs. Ninth T6.22 extraction from `core_visibility.VisibilityMixin`; exposed as `engine._visibility`.

## Key Functions / Classes
- `Visibility` — subsystem class; ctor takes `connect_fn`, `publish_event_fn`, `project_root_getter` (no `_hybrid_wait` — the mixin never waited on events).
- `Visibility.load_coordination_spec(path)` — load/reload a YAML/JSON spec from disk; publishes `graph.loaded`.
- `Visibility.validate_graph()` — validate the loaded graph.
- `Visibility._effective_graph()` — loaded graph or a dynamically built implicit graph from DB state.
- `Visibility.scan_project(worktree_root, extensions)` — scan files, assign ownership per the graph; publishes `scan.completed`; rejects an explicitly empty `extensions` list.
- `Visibility.get_agent_status(agent_id)` / `get_agent_tree(agent_id)` / `get_file_agent_map(agent_id)` — status, hierarchy tree, file-to-owner map.
- `Visibility.update_agent_status(agent_id, current_task, scope)` — update task or declared scope.
- `Visibility.run_assessment(suite_path, format, graph_agent_id, scope)` — run a JSON suite or synthesize a live-session trace from DB state; stores results; publishes `assessment.completed`; markdown report by default.
- `Visibility.prune_assessment_results(max_age_seconds)` — T7.32 retention pruning (default 30 days; called by the HousekeepingScheduler).

## Design Notes
- Graph state is a module-level singleton in `plugins/graph/graphs.py` (`set_graph`/`get_graph`/`clear_graph`); the old mixin docstring mentioned `self._graph` but never read it. Because that state is shared automatically, this subsystem needs no graph getter/setter injection.
- `get_agent_status` passes a module-level `get_lineage` closure directly to avoid MRO/HTTP transport issues.
- `run_assessment` without `suite_path` builds the suite from DB; `scope='project'` constrains it to the project worktree root. T7.32: `details_json` carries full per-metric traces, so `assessment_results` grows quickly — pruning is required.
- `project_root_getter` is a closure so a `read_only_engine` replica picks up its own storage root without rebind.

## Relationships
Imports: `plugins.graph.graphs`, `scan`, `agent_status`, `plugins.assessment.assessment`, `agent_registry`
Imported by: `core.py` (constructed by `CoordinationEngine`)
