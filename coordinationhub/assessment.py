"""Assessment runner for CoordinationHub coordination test suites.

Loads a test trace suite (JSON) OR synthesizes one from live DB state,
runs it against the coordination graph, outputs a Markdown report and
JSON scores, and stores results in SQLite.

Metric scorers live in assessment_scorers.py and are re-exported here
for backward compatibility.

Zero third-party dependencies — uses stdlib json + sqlite3 only.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .assessment_scorers import (
    METRIC_SCORERS as _METRIC_SCORERS,
    score_role_stability,
    score_handoff_latency,
    score_outcome_verifiability,
    score_protocol_adherence,
    score_spawn_propagation,
    event_matches_responsibility,
    build_trace_mappings,
    COORDINATION_PRIMITIVES,
)
from .db import ConnectFn


# ------------------------------------------------------------------ #
# Suite loading
# ------------------------------------------------------------------ #

def load_suite(path: Path) -> dict[str, Any]:
    """Load a test suite JSON file."""
    return json.loads(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ #
# Live-DB trace synthesis
# ------------------------------------------------------------------ #

def build_trace_from_db(
    connect: ConnectFn,
    trace_id: str = "live-session",
    worktree_root: str | None = None,
) -> dict[str, Any]:
    """Synthesize an assessment trace from current DB state.

    Reads the ``agents``, ``change_notifications``, and ``lineage``
    tables and emits register / lock / modified / unlock / handoff
    events in the format the metric scorers expect. Hooks never emit
    explicit unlock events, so each change_notification becomes a
    synthetic lock → modified → unlock triple — this gives
    ``score_outcome_verifiability`` real data to work with while
    remaining faithful to what was actually observed.

    Args:
        connect: connection factory
        trace_id: label for the returned trace
        worktree_root: optional filter — only include agents and
            change notifications for this worktree

    Returns:
        trace dict with ``trace_id`` and ``events`` keys, events sorted
        by timestamp
    """
    events: list[dict[str, Any]] = []

    with connect() as conn:
        # Register events
        if worktree_root:
            agent_rows = conn.execute(
                """
                SELECT a.agent_id, a.parent_id, a.started_at,
                       ar.graph_agent_id
                FROM agents a
                LEFT JOIN agent_responsibilities ar
                       ON a.agent_id = ar.agent_id
                WHERE a.worktree_root = ?
                ORDER BY a.started_at
                """,
                (worktree_root,),
            ).fetchall()
        else:
            agent_rows = conn.execute(
                """
                SELECT a.agent_id, a.parent_id, a.started_at,
                       ar.graph_agent_id
                FROM agents a
                LEFT JOIN agent_responsibilities ar
                       ON a.agent_id = ar.agent_id
                ORDER BY a.started_at
                """,
            ).fetchall()

        for row in agent_rows:
            evt: dict[str, Any] = {
                "type": "register",
                "agent_id": row["agent_id"],
                "_ts": row["started_at"],
            }
            if row["graph_agent_id"]:
                evt["graph_id"] = row["graph_agent_id"]
            if row["parent_id"]:
                evt["parent_id"] = row["parent_id"]
            events.append(evt)

        # Lock → modified → unlock triples from change_notifications
        if worktree_root:
            notif_rows = conn.execute(
                """
                SELECT document_path, agent_id, created_at
                FROM change_notifications
                WHERE change_type = 'modified' AND worktree_root = ?
                ORDER BY created_at
                """,
                (worktree_root,),
            ).fetchall()
        else:
            notif_rows = conn.execute(
                """
                SELECT document_path, agent_id, created_at
                FROM change_notifications
                WHERE change_type = 'modified'
                ORDER BY created_at
                """,
            ).fetchall()

        for row in notif_rows:
            base_ts = row["created_at"]
            path = row["document_path"]
            agent_id = row["agent_id"]
            # Microsecond offsets keep the triple in lock→modified→unlock
            # order even after the outer sort merges them with register
            # events that share the same timestamp.
            events.append({
                "type": "lock", "path": path, "agent_id": agent_id,
                "_ts": base_ts - 1e-6,
            })
            events.append({
                "type": "modified", "path": path, "agent_id": agent_id,
                "_ts": base_ts,
            })
            events.append({
                "type": "unlock", "path": path, "agent_id": agent_id,
                "_ts": base_ts + 1e-6,
            })

        # Handoff events: lineage rows where parent and child have
        # different graph roles.
        lineage_rows = conn.execute(
            """
            SELECT l.parent_id, l.child_id, l.spawned_at,
                   par.graph_agent_id AS from_graph,
                   car.graph_agent_id AS to_graph
            FROM lineage l
            LEFT JOIN agent_responsibilities par
                   ON l.parent_id = par.agent_id
            LEFT JOIN agent_responsibilities car
                   ON l.child_id = car.agent_id
            WHERE par.graph_agent_id IS NOT NULL
              AND car.graph_agent_id IS NOT NULL
              AND par.graph_agent_id != car.graph_agent_id
            ORDER BY l.spawned_at
            """,
        ).fetchall()

        for row in lineage_rows:
            events.append({
                "type": "handoff",
                "from": row["from_graph"],
                "to": row["to_graph"],
                "_ts": row["spawned_at"],
            })

    events.sort(key=lambda e: e.get("_ts", 0.0))
    # Strip internal sort keys — the scorers only look at documented fields.
    for evt in events:
        evt.pop("_ts", None)

    return {"trace_id": trace_id, "events": events}


def build_suite_from_db(
    connect: ConnectFn,
    suite_name: str = "live_session",
    worktree_root: str | None = None,
) -> dict[str, Any]:
    """Build a single-trace assessment suite from current DB state."""
    trace = build_trace_from_db(
        connect,
        trace_id=suite_name,
        worktree_root=worktree_root,
    )
    return {"name": suite_name, "traces": [trace]}


# ------------------------------------------------------------------ #
# Graph refinement suggestion
# ------------------------------------------------------------------ #

def _suggest_graph_refinements(suite: dict[str, Any], graph: Any) -> list[dict[str, Any]]:
    """Analyze trace suite and suggest graph refinements.

    Returns a list of suggestion dicts with keys: type, from_agent, to_agent,
    suggested_responsibility, reason.
    """
    suggestions: list[dict[str, Any]] = []
    if not suite or not graph:
        return suggestions

    traces = suite.get("traces", [])
    defined_agents = set(graph.agents.keys())
    defined_handoffs = {(h["from"], h["to"]) for h in graph.handoffs}

    trace_handoffs: set[tuple[str, str]] = set()
    for trace in traces:
        for evt in trace.get("events", []):
            if evt.get("type") == "handoff":
                trace_handoffs.add((evt.get("from", ""), evt.get("to", "")))

    for (frm, to) in trace_handoffs:
        if frm in defined_agents and to in defined_agents and (frm, to) not in defined_handoffs:
            suggestions.append({
                "type": "missing_handoff",
                "from_agent": frm,
                "to_agent": to,
                "suggestion": f"handoff from '{frm}' to '{to}' is used in traces but not defined in graph",
                "reason": "protocol_adherence",
            })

    for trace in traces:
        for evt in trace.get("events", []):
            if evt.get("type") == "register":
                gid = evt.get("graph_id", "")
                if gid and gid not in defined_agents:
                    suggestions.append({
                        "type": "missing_agent",
                        "graph_agent_id": gid,
                        "suggestion": f"agent role '{gid}' is registered in traces but not defined in graph",
                        "reason": "spawn_propagation",
                    })

    return suggestions


# ------------------------------------------------------------------ #
# Assessment run
# ------------------------------------------------------------------ #

def run_assessment(
    suite: dict[str, Any],
    graph: Any,
    store_fn: Any = None,
    graph_agent_id: str | None = None,
) -> dict[str, Any]:
    """Run a loaded suite against the current graph.

    Args:
        suite: parsed test suite dict
        graph: CoordinationGraph instance or None
        store_fn: optional callable(conn, results) to persist to SQLite
        graph_agent_id: optional filter -- if set, only score traces where at least
            one register event uses this graph_agent_id

    Returns:
        dict with suite_name, timestamp, scores per metric, per-trace breakdown,
        suggested_refinements, and full trace JSON
    """
    now = time.time()
    suite_name = suite.get("name", "unnamed")
    traces = suite.get("traces", [])

    if graph_agent_id:
        filtered = []
        for trace in traces:
            for evt in trace.get("events", []):
                if evt.get("type") == "register" and evt.get("graph_id") == graph_agent_id:
                    filtered.append(trace)
                    break
        traces = filtered

    all_metrics = [
        "role_stability", "handoff_latency", "outcome_verifiability",
        "protocol_adherence", "spawn_propagation",
    ]
    metrics = all_metrics[:]
    if graph and graph.assessment:
        configured = graph.assessment.get("metrics", metrics)
        metrics = configured
    if "spawn_propagation" not in metrics:
        metrics = metrics + ["spawn_propagation"]

    trace_scores: dict[str, dict[str, float]] = {}
    metric_totals: dict[str, float] = {m: 0.0 for m in metrics}

    for trace in traces:
        trace_id = trace.get("trace_id", "unknown")
        trace_scores[trace_id] = {}
        for metric in metrics:
            scorer = _METRIC_SCORERS.get(metric, lambda *a: 0.0)
            s = scorer(trace, graph)
            trace_scores[trace_id][metric] = s
            metric_totals[metric] += s

    num_traces = len(traces)
    metric_averages = (
        {m: t / num_traces for m, t in metric_totals.items()} if num_traces > 0
        else {m: 0.0 for m in metrics}
    )
    overall = sum(metric_averages.values()) / len(metric_averages) if metric_averages else 0.0

    suggested_refinements = _suggest_graph_refinements(suite, graph)

    return {
        "suite_name": suite_name,
        "run_at": now,
        "metrics": metric_averages,
        "overall_score": overall,
        "traces": trace_scores,
        "graph_loaded": graph is not None,
        "graph_agent_id_filter": graph_agent_id,
        "suggested_refinements": suggested_refinements,
        "full_trace_json": json.dumps(traces, default=str),
    }


# ------------------------------------------------------------------ #
# Markdown report generator
# ------------------------------------------------------------------ #

def format_markdown_report(result: dict[str, Any]) -> str:
    """Format assessment results as a Markdown string."""
    lines = [
        f"# Assessment Report: {result['suite_name']}",
        "",
        f"**Overall Score:** {result['overall_score']:.2%}",
        f"**Graph Loaded:** {result['graph_loaded']}",
        f"**Run At:** {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(result['run_at']))}",
    ]
    if result.get("graph_agent_id_filter"):
        lines.append(f"**Filtered by:** graph_agent_id = {result['graph_agent_id_filter']}")
    lines.extend(["", "## Metric Scores", "", "| Metric | Score |", "|--------|-------|"])
    for metric, score in result["metrics"].items():
        lines.append(f"| {metric} | {score:.2%} |")
    lines.append("")

    traces = result.get("traces", {})
    if traces:
        lines.extend(["## Per-Trace Breakdown", ""])
        for trace_id, scores in traces.items():
            lines.append(f"### {trace_id}")
            for metric, score in scores.items():
                lines.append(f"- {metric}: {score:.2%}")
            lines.append("")

    refinements = result.get("suggested_refinements", [])
    if refinements:
        lines.extend(["## Suggested Graph Refinements", ""])
        for r in refinements:
            lines.append(f"- [{r['type']}] {r.get('suggestion', '')}")
        lines.append("")

    return "\n".join(lines)


# ------------------------------------------------------------------ #
# SQLite storage
# ------------------------------------------------------------------ #

def store_assessment_results(
    conn: sqlite3.Connection,
    result: dict[str, Any],
) -> None:
    """Persist assessment result to SQLite."""
    now = result["run_at"]
    suite_name = result["suite_name"]
    full_trace = result.get("full_trace_json", "")
    refinements = result.get("suggested_refinements", [])
    graph_agent_id_filter = result.get("graph_agent_id_filter")
    for metric, score in result["metrics"].items():
        trace_best = max(
            (t.get(metric, 0.0) for t in result.get("traces", {}).values()),
            default=score,
        )
        details = {
            "overall": score,
            "trace_best": trace_best,
            "full_trace_json": full_trace,
            "suggested_refinements": refinements,
            "graph_agent_id_filter": graph_agent_id_filter,
        }
        conn.execute(
            "INSERT INTO assessment_results (suite_name, metric, score, details_json, run_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (suite_name, metric, score, json.dumps(details, default=str), now),
        )
