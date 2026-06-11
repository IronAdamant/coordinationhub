# coordinationhub/plugins/assessment/assessment.py

Assessment runner for coordination test suites: loads a JSON trace suite or synthesizes one from live DB state, scores it against the coordination graph, renders a Markdown report, and persists/prunes results in SQLite. Metric scorers live in `assessment_scorers.py` and are re-exported here for backward compatibility.

## Key Functions / Classes
- `load_suite(path)` — parse a suite JSON file.
- `build_trace_from_db(connect, trace_id, worktree_root)` — synthesizes register / lock / modified / unlock / handoff events from the `agents`, `change_notifications`, and `lineage` tables (optionally filtered by worktree), sorted by timestamp.
- `build_suite_from_db(connect, suite_name, worktree_root)` — wraps a single synthesized trace into a suite dict.
- `run_assessment(suite, graph, store_fn, graph_agent_id)` — scores each trace on every configured metric (graph `assessment.metrics` overrides the default six; `spawn_propagation` is always appended), returns averages, per-trace breakdown, `suggested_refinements`, and `full_trace_json`.
- `_suggest_graph_refinements(suite, graph)` — flags handoffs used in traces but missing from the graph (`missing_handoff`) and registered roles not defined in the graph (`missing_agent`).
- `format_markdown_report(result)` — Markdown report with overall score, metric table, per-trace breakdown, refinement suggestions.
- `store_assessment_results(conn, result)` — one `assessment_results` row per metric; `details_json` carries trace_best, full trace, refinements, filter.
- `prune_assessment_results(conn, max_age_seconds)` — retention cap (T7.32: `details_json` carries the full trace per metric, so timer-driven assessments grow the table without bound).

## Design Notes
- Zero third-party dependencies — stdlib `json` + `sqlite3` only.
- Hooks never emit explicit unlock events, so each `modified` change_notification becomes a synthetic lock→modified→unlock triple, ordered by ±1 µs timestamp offsets so the triple survives the merged sort; internal `_ts` sort keys are stripped before scoring.
- `graph_agent_id` filter keeps only traces containing a register event with that `graph_id`.
- Unknown metrics fall back to a `0.0` scorer rather than raising.

## Relationships
Imports: `.assessment_scorers` (`METRIC_SCORERS`, all five scorers, `event_matches_responsibility`, `build_trace_mappings`, `COORDINATION_PRIMITIVES`), `coordinationhub.db` (`ConnectFn`).
Imported by: `coordinationhub/plugins/assessment/__init__.py` (re-exports), `coordinationhub/visibility_subsystem.py` (`from .plugins.assessment import assessment as _assess`).
