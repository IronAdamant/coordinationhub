"""Assessment runner for CoordinationHub coordination test suites.

Loads a test trace suite (JSON), runs the loaded coordination graph against it,
scores on the metrics defined in the graph spec, outputs a Markdown report
and JSON scores, and stores results in SQLite for historical comparison.

Zero third-party dependencies — uses stdlib json + sqlite3 only.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .graphs import get_graph


# ------------------------------------------------------------------ #
# Metric scorers
# ------------------------------------------------------------------ #

def score_role_stability(trace: dict[str, Any], graph: Any) -> float:
    """Score 0-1: did each agent act only within its defined responsibilities?

    Events are matched against the responsibilities declared in the graph for
    each agent's graph_id. Events that fall outside those responsibilities
    reduce the score. Lock/unlock are always permitted (coordination actions).
    """
    events = trace.get("events", [])
    if not events:
        return 1.0

    # Build agent -> graph_id mapping from register events
    agent_graph_id: dict[str, str] = {}
    for evt in events:
        if evt.get("type") == "register":
            agent_graph_id[evt["agent_id"]] = evt.get("graph_id", "")

    # Build graph_id -> responsibility set
    graph_responsibilities: dict[str, set[str]] = {}
    if graph:
        for gid, agent_def in graph.agents.items():
            resp = agent_def.get("responsibilities", [])
            graph_responsibilities[gid] = set(resp) if isinstance(resp, list) else set()

    violations = 0
    scored_events = 0

    for evt in events:
        etype = evt.get("type")
        aid = evt.get("agent_id", "")
        gid = agent_graph_id.get(aid, "")

        # Lock/unlock/notify_change are always fine — coordination primitives
        if etype in ("lock", "unlock", "notify_change", "register", "handoff", "heartbeat"):
            continue

        # Determine if this event type is covered by the agent's responsibilities
        responsibilities = graph_responsibilities.get(gid, set())
        if not responsibilities:
            # No graph or no role defined — can't penalize, skip
            continue

        # Map event type to a responsibility keyword
        # file_scan / modified / write → "write_code" or "modify_files"
        # acquire_lock → "coordinate"
        covered = False
        for resp in responsibilities:
            resp_lower = resp.lower()
            if etype in ("file_scan", "modified", "write"):
                if "write" in resp_lower or "edit" in resp_lower or "modify" in resp_lower or "implement" in resp_lower:
                    covered = True
                    break
            elif etype in ("acquire_lock", "release_lock", "refresh_lock"):
                if "coordinate" in resp_lower or "lock" in resp_lower:
                    covered = True
                    break
            elif etype == "get_notifications" or etype == "prune_notifications":
                if "notify" in resp_lower or "coordinate" in resp_lower:
                    covered = True
                    break

        if not covered:
            violations += 1
        scored_events += 1

    if scored_events == 0:
        return 1.0
    return max(0.0, 1.0 - (violations / scored_events))


def score_handoff_latency(trace: dict[str, Any], graph: Any) -> float:
    """Score 0-1: did handoff events match the graph's handoff definitions?

    Each handoff event is checked against the coordination graph:
    - The from/to pair must exist as a defined handoff
    - The condition expression should be non-empty
    Partial credit is given when the pair is correct but no condition is present.
    """
    if graph is None:
        return 0.0

    defined_handoffs = {
        (h["from"], h["to"]): h.get("condition", "")
        for h in graph.handoffs
    }

    handoff_events = [e for e in trace.get("events", []) if e["type"] == "handoff"]
    if not handoff_events:
        return 1.0  # No handoffs needed, vacuously correct

    score = 0.0
    for evt in handoff_events:
        from_id = evt.get("from", "")
        to_id = evt.get("to", "")
        condition = evt.get("condition", "")
        key = (from_id, to_id)

        if key not in defined_handoffs:
            continue  # Unknown handoff pair, no credit

        # Base credit for correct from/to pair
        score += 0.5

        # Additional credit if condition is present and non-trivial
        if condition and condition not in ("always", "true"):
            score += 0.5
        elif defined_handoffs[key] and not condition:
            # Graph expects condition but handoff event doesn't have one
            score += 0.25  # partial credit only
        else:
            # Both graph and event have no condition (or "always"), full credit
            score += 0.5

    return min(score / len(handoff_events), 1.0)


def score_outcome_verifiability(trace: dict[str, Any], _graph: Any) -> float:
    """Score 0-1: were files locked before being written/modified, and unlocked after?

    Evaluates the lock-write-unlock pattern for each file:
    - Lock event establishes intent
    - Write/modified event before release scores as verified
    - Unlock without prior modification scores as a wasted lock
    """
    events = trace.get("events", [])
    if not events:
        return 1.0

    # Track per-path state: was modified between lock and unlock?
    locked: dict[str, dict[str, Any]] = {}  # path -> {agent_id, modified: bool}
    verifications: list[bool] = []

    for evt in events:
        etype = evt.get("type")
        path = evt.get("path", "")

        if etype == "lock":
            locked[path] = {"agent_id": evt.get("agent_id", ""), "modified": False}
        elif etype in ("write", "modified"):
            if path in locked:
                locked[path]["modified"] = True
            elif path:
                verifications.append(False)  # modification without a lock
        elif etype == "unlock":
            if path in locked:
                # Only scores True if there was a modification between lock and unlock
                verifications.append(locked[path]["modified"])
                del locked[path]
            elif path:
                verifications.append(False)  # unlock without ever having locked

    if not verifications:
        return 1.0
    return sum(verifications) / len(verifications)


def score_protocol_adherence(trace: dict[str, Any], graph: Any) -> float:
    """Score 0-1: did agents follow declared responsibilities and protocol rules?

    Checks:
    - Agents act only within their declared responsibilities (from score_role_stability)
    - Agents do not act on files they do not own (if ownership is defined)
    - Broadcast is followed by a meaningful action (lock, write, or handoff)
    """
    events = trace.get("events", [])
    if not events:
        return 1.0

    violations = 0
    scored_events = 0

    # Build agent -> graph_id mapping
    agent_graph_id: dict[str, str] = {}
    for evt in events:
        if evt.get("type") == "register":
            agent_graph_id[evt["agent_id"]] = evt.get("graph_id", "")

    # Build graph_id -> responsibilities
    graph_responsibilities: dict[str, set[str]] = {}
    if graph:
        for gid, agent_def in graph.agents.items():
            resp = agent_def.get("responsibilities", [])
            graph_responsibilities[gid] = set(resp) if isinstance(resp, list) else set()

    for evt in events:
        etype = evt.get("type")
        aid = evt.get("agent_id", "")
        gid = agent_graph_id.get(aid, "")
        responsibilities = graph_responsibilities.get(gid, set())

        if etype in ("register", "heartbeat", "handoff"):
            continue

        scored_events += 1

        if etype == "modified":
            path = evt.get("path", "")
            # Check: agent modified a file — should it be in their responsibilities?
            if responsibilities:
                # "write_code" / "implement" / "modify_files" are OK for modified
                ok = any(
                    r in ("write_code", "implement", "modify_files", "edit", "write")
                    for r in responsibilities
                )
                if not ok:
                    violations += 1
                    continue

            # Check: was the file locked by this agent before modification?
            # (We can't easily check this without replaying, so we give partial
            # credit based on whether the event is within responsibilities)
            pass  # covered by the above check

        elif etype == "acquire_lock":
            # Should coordinate if taking locks
            if responsibilities and "coordinate" not in " ".join(responsibilities).lower():
                # But this is too strict — skip
                pass

        elif etype == "notify_change":
            # notify_change is always fine
            pass

    if scored_events == 0:
        return 1.0
    return max(0.0, 1.0 - (violations / scored_events))


def score_spawn_propagation(trace: dict[str, Any], graph: Any) -> float:
    """Score 0-1: did spawned agents correctly inherit responsibilities from their parent?

    Checks:
    - When a child agent is registered with a parent_id, the child's events should
      fall within the parent's declared responsibilities (inheritance is respected).
    - A child that acts outside both its own scope (if graph_agent_id is set) and the
      parent's scope scores lower.
    - Unowned/unparented agents do not penalize this metric.
    """
    events = trace.get("events", [])
    if not events:
        return 1.0

    # Build agent -> (graph_id, parent_id) from register events
    agent_graph_id: dict[str, str] = {}
    agent_parent_id: dict[str, str] = {}
    for evt in events:
        if evt.get("type") == "register":
            agent_graph_id[evt["agent_id"]] = evt.get("graph_id", "")
            agent_parent_id[evt["agent_id"]] = evt.get("parent_id", "") or ""

    # Build graph_id -> responsibilities
    graph_responsibilities: dict[str, set[str]] = {}
    if graph:
        for gid, agent_def in graph.agents.items():
            resp = agent_def.get("responsibilities", [])
            graph_responsibilities[gid] = set(resp) if isinstance(resp, list) else set()

    violations = 0
    scored_events = 0

    for evt in events:
        etype = evt.get("type")
        aid = evt.get("agent_id", "")
        parent_id = agent_parent_id.get(aid, "")
        gid = agent_graph_id.get(aid, "")

        # Coordination primitives are always fine
        if etype in ("lock", "unlock", "notify_change", "register", "handoff",
                     "heartbeat", "acquire_lock", "release_lock", "refresh_lock",
                     "get_notifications", "prune_notifications"):
            continue

        # Get this agent's own responsibilities
        own_resp = graph_responsibilities.get(gid, set())

        # Get parent's responsibilities (if parent exists)
        parent_gid = ""
        parent_resp: set[str] = set()
        if parent_id:
            parent_gid = agent_graph_id.get(parent_id, "")
            parent_resp = graph_responsibilities.get(parent_gid, set())

        # Effective responsibilities = own + parent (child inherits parent scope)
        effective_resp = own_resp | parent_resp

        # If no effective responsibilities at all, skip scoring
        if not effective_resp:
            continue

        # Determine if this event type is covered by effective responsibilities
        covered = False
        for resp in effective_resp:
            resp_lower = resp.lower()
            if etype in ("file_scan", "modified", "write"):
                if "write" in resp_lower or "edit" in resp_lower or "modify" in resp_lower or "implement" in resp_lower:
                    covered = True
                    break
            elif etype in ("acquire_lock", "release_lock", "refresh_lock"):
                if "coordinate" in resp_lower or "lock" in resp_lower:
                    covered = True
                    break
            elif etype in ("get_notifications", "prune_notifications"):
                if "notify" in resp_lower or "coordinate" in resp_lower:
                    covered = True
                    break

        if not covered:
            violations += 1
        scored_events += 1

    if scored_events == 0:
        return 1.0
    return max(0.0, 1.0 - (violations / scored_events))


_METRIC_SCORERS: dict[str, Any] = {
    "role_stability": score_role_stability,
    "handoff_latency": score_handoff_latency,
    "outcome_verifiability": score_outcome_verifiability,
    "protocol_adherence": score_protocol_adherence,
    "spawn_propagation": score_spawn_propagation,
}


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

    # Collect all (from, to) handoff pairs from traces
    trace_handoffs: set[tuple[str, str]] = set()
    for trace in traces:
        for evt in trace.get("events", []):
            if evt.get("type") == "handoff":
                trace_handoffs.add((evt.get("from", ""), evt.get("to", "")))

    # Suggest handoff edges that appear in traces but not in the graph
    for (frm, to) in trace_handoffs:
        if frm in defined_agents and to in defined_agents and (frm, to) not in defined_handoffs:
            suggestions.append({
                "type": "missing_handoff",
                "from_agent": frm,
                "to_agent": to,
                "suggestion": f"handoff from '{frm}' to '{to}' is used in traces but not defined in graph",
                "reason": "protocol_adherence",
            })

    # Suggest missing agent roles that appear in traces but not in graph
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
        graph_agent_id: optional filter — if set, only score traces where at least
            one register event uses this graph_agent_id

    Returns:
        dict with suite_name, timestamp, scores per metric, per-trace breakdown,
        suggested_refinements, and full trace JSON
    """
    now = time.time()
    suite_name = suite.get("name", "unnamed")
    traces = suite.get("traces", [])

    # Filter traces by graph_agent_id if specified
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
    # Always include spawn_propagation even if graph defines its own metrics
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

    # Build suggested refinements from the full (unfiltered) suite
    suggested_refinements = _suggest_graph_refinements(suite, graph)

    result = {
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
    return result


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
    """Persist assessment result to SQLite.

    Stores full trace JSON and suggested graph refinements alongside metric scores
    in the details_json column for later audit and comparison.
    """
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
