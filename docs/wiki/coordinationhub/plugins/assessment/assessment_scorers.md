# coordinationhub/plugins/assessment/assessment_scorers.py

Six metric scorers (each `(trace, graph) -> float` in 0–1) that evaluate coordination trace suites against a declared coordination graph, plus the shared keyword-matching helpers they all use. Zero third-party dependencies.

## Key Functions / Classes
- `METRIC_SCORERS` — registry dict mapping metric name → scorer; consumed by `assessment.run_assessment`.
- `score_role_stability` — fraction of non-primitive events that fall within the agent's declared responsibilities.
- `score_handoff_latency` — checks handoff events against graph handoff definitions; partial credit for missing/trivial conditions.
- `score_outcome_verifiability` — lock→write/modified→unlock pattern per file; unmodified locks and unlocked writes count against.
- `score_protocol_adherence` — like role_stability but agents with no declared responsibilities still count as scored (not skipped).
- `score_spawn_propagation` — child events checked against the union of own + parent responsibilities; unparented/unowned agents don't penalize.
- `score_leader_stability` — penalizes 0.25 per `transfer`/`leaseExpired`/`leadershipClaimed` marker.
- `event_matches_responsibility(event_type, responsibilities)` — keyword substring matching via `_EVENT_RESPONSIBILITY_MAP`; unknown event types fall back to token overlap with responsibility text.
- `build_trace_mappings(events, graph)` — extracts `agent_id → graph_id` and `graph_id → responsibilities` from register events + graph definitions.
- `COORDINATION_PRIMITIVES` — event types (lock/unlock/register/heartbeat/etc.) always permitted and skipped by responsibility scoring.

## Design Notes
- Empty traces and zero-scored-event traces score 1.0 (benefit of the doubt), except `score_handoff_latency` which returns 0.0 when no graph is loaded.
- T3.10: handoff scoring was rewritten as a monotonic if/elif chain — the old baseline-0.5-plus-bonus logic double-counted and ranked a condition-free handoff above a partially-conforming one. Handoff pairs not defined in the graph contribute 0.
- T3.11: leader_stability measures the trace; the graph's `assessment.leader_stability.threshold` is a floor for warnings only and never fabricates the score (pre-fix it returned the 0.8 threshold as if measured).
- T7.33: `graph_responsibilities` values stay `set`s deliberately — scoring paths short-circuit or reduce to bools, so set iteration order can't leak into results; changing the type would cascade for a non-bug.

## Relationships
Imports: none within the coordinationhub package (stdlib `typing` only).
Imported by: `coordinationhub/plugins/assessment/assessment.py` (scorers, helpers, `METRIC_SCORERS`).
