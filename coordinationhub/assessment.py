"""Assessment runner for CoordinationHub coordination test suites.

Loads a test trace suite (JSON), runs it against the coordination graph,
outputs a Markdown report and JSON scores, stores results in SQLite.

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


# ------------------------------------------------------------------ #
# Suite loading
# ------------------------------------------------------------------ #

def load_suite(path: Path) -> dict[str, Any]:
    """Load a test suite JSON file."""
    return json.loads(path.read_text(encoding="utf-8"))


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
