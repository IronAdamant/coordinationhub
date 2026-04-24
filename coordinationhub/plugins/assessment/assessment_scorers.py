"""Assessment metric scorers for CoordinationHub.

Five metric scorers that evaluate coordination trace suites against a
declared coordination graph. Shared helpers eliminate keyword-matching
duplication across scorers.

Zero third-party dependencies.
"""

from __future__ import annotations

from typing import Any


# ------------------------------------------------------------------ #
# Shared constants and helpers
# ------------------------------------------------------------------ #

COORDINATION_PRIMITIVES = frozenset({
    "lock", "unlock", "notify_change", "register", "handoff", "heartbeat",
    "acquire_lock", "release_lock", "refresh_lock",
    "get_notifications", "prune_notifications",
})

# Event types → responsibility keywords (substring match).
# An event is "covered" if the agent has at least one responsibility
# containing any of the mapped keywords (case-insensitive).
_EVENT_RESPONSIBILITY_MAP: dict[frozenset[str], tuple[str, ...]] = {
    frozenset({"file_scan", "modified", "write"}): (
        "write", "edit", "modify", "implement", "develop", "build",
        "author", "produce", "create", "code", "fix",
    ),
    frozenset({"acquire_lock", "release_lock", "refresh_lock"}): (
        "coordinate", "lock", "manage", "orchestrate", "synchronize",
    ),
    frozenset({"get_notifications", "prune_notifications"}): (
        "notify", "coordinate", "monitor", "track", "observe",
    ),
    frozenset({"read", "search", "explore"}): (
        "read", "review", "explore", "research", "investigate",
        "analyze", "examine", "inspect", "audit", "quality",
    ),
    frozenset({"test", "run_tests"}): (
        "test", "verify", "validate", "quality", "qa", "check",
        "assert", "ensure",
    ),
    frozenset({"plan", "decompose"}): (
        "plan", "decompose", "design", "architect", "organize",
        "prioritize", "schedule", "scope",
    ),
    frozenset({"deploy", "release", "publish"}): (
        "deploy", "release", "publish", "ship", "deliver", "pipeline",
    ),
}


def event_matches_responsibility(
    event_type: str,
    responsibilities: set[str],
) -> bool:
    """Check if *event_type* is covered by any responsibility via keyword matching.

    Returns True if any responsibility string contains a keyword mapped to
    the event type. Returns False for unknown event types (not covered).
    """
    for event_types, keywords in _EVENT_RESPONSIBILITY_MAP.items():
        if event_type in event_types:
            for resp in responsibilities:
                resp_lower = resp.lower()
                if any(kw in resp_lower for kw in keywords):
                    return True
            return False
    # Unknown event type: fall back to token overlap with responsibility text
    tokens = set(event_type.lower().replace("_", " ").split())
    for resp in responsibilities:
        if tokens & set(resp.lower().split()):
            return True
    return False


def build_trace_mappings(
    events: list[dict[str, Any]],
    graph: Any,
) -> tuple[dict[str, str], dict[str, set[str]]]:
    """Extract agent→graph_id and graph_id→responsibilities from trace events.

    Returns:
        (agent_graph_id, graph_responsibilities)
    """
    agent_graph_id: dict[str, str] = {}
    for evt in events:
        if evt.get("type") == "register":
            agent_graph_id[evt["agent_id"]] = evt.get("graph_id", "")

    # T7.33: kept as a set so downstream ``|``/``&`` combinators in
    # score_spawn_propagation still work. The original audit flag was
    # about iteration order affecting trace-key tests; the scoring
    # paths here all short-circuit on first hit or boil down to a bool,
    # so iteration order doesn't leak into results. Leaving the set
    # type unchanged avoids a cascading rewrite for a non-bug.
    graph_responsibilities: dict[str, set[str]] = {}
    if graph:
        for gid, agent_def in graph.agents.items():
            resp = agent_def.get("responsibilities", [])
            graph_responsibilities[gid] = set(resp) if isinstance(resp, list) else set()

    return agent_graph_id, graph_responsibilities


# ------------------------------------------------------------------ #
# Metric scorers
# ------------------------------------------------------------------ #

def score_role_stability(trace: dict[str, Any], graph: Any) -> float:
    """Score 0-1: did each agent act only within its defined responsibilities?

    Events are matched against the responsibilities declared in the graph for
    each agent's graph_id. Events that fall outside those responsibilities
    reduce the score. Coordination primitives are always permitted.
    """
    events = trace.get("events", [])
    if not events:
        return 1.0

    agent_graph_id, graph_responsibilities = build_trace_mappings(events, graph)

    violations = 0
    scored_events = 0

    for evt in events:
        etype = evt.get("type")
        if etype in COORDINATION_PRIMITIVES:
            continue

        aid = evt.get("agent_id", "")
        gid = agent_graph_id.get(aid, "")
        responsibilities = graph_responsibilities.get(gid, set())
        if not responsibilities:
            continue

        if not event_matches_responsibility(etype, responsibilities):
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
        return 1.0

    # T3.10: rewritten as pure if/elif chain. The pre-fix code added a
    # baseline 0.5 then double-counted via an if/elif/else that also
    # added 0.5 in most branches, so ``condition == ""`` with a defined
    # non-empty condition scored 0.75 while a handoff with NO condition
    # at all (empty string + empty definition) fell into the else and
    # scored 1.0. New scoring is monotonic: stronger signal == higher
    # score.
    score = 0.0
    for evt in handoff_events:
        from_id = evt.get("from", "")
        to_id = evt.get("to", "")
        condition = evt.get("condition", "")
        key = (from_id, to_id)

        if key not in defined_handoffs:
            continue

        expected_condition = defined_handoffs[key]
        is_meaningful = condition and condition not in ("always", "true")
        if is_meaningful:
            score += 1.0
        elif expected_condition and not condition:
            # definition required a condition but the event had none
            score += 0.5
        elif condition in ("always", "true"):
            # trivial condition — acceptable but low information
            score += 0.75
        else:
            # no condition required, none provided — neutral pass
            score += 1.0

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

    locked: dict[str, dict[str, Any]] = {}
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
                verifications.append(False)
        elif etype == "unlock":
            if path in locked:
                verifications.append(locked[path]["modified"])
                del locked[path]
            elif path:
                verifications.append(False)

    if not verifications:
        return 1.0
    return sum(verifications) / len(verifications)


def score_protocol_adherence(trace: dict[str, Any], graph: Any) -> float:
    """Score 0-1: did agents follow declared responsibilities and protocol rules?

    Uses the shared event_matches_responsibility for consistent keyword matching.
    """
    events = trace.get("events", [])
    if not events:
        return 1.0

    agent_graph_id, graph_responsibilities = build_trace_mappings(events, graph)

    violations = 0
    scored_events = 0

    for evt in events:
        etype = evt.get("type")
        if etype in COORDINATION_PRIMITIVES:
            continue

        aid = evt.get("agent_id", "")
        gid = agent_graph_id.get(aid, "")
        responsibilities = graph_responsibilities.get(gid, set())
        if not responsibilities:
            scored_events += 1
            continue

        scored_events += 1
        if not event_matches_responsibility(etype, responsibilities):
            violations += 1

    if scored_events == 0:
        return 1.0
    return max(0.0, 1.0 - (violations / scored_events))


def score_spawn_propagation(trace: dict[str, Any], graph: Any) -> float:
    """Score 0-1: did spawned agents correctly inherit responsibilities from their parent?

    Child events are checked against the union of own + parent responsibilities.
    Unowned/unparented agents do not penalize this metric.
    """
    events = trace.get("events", [])
    if not events:
        return 1.0

    agent_graph_id, graph_responsibilities = build_trace_mappings(events, graph)

    agent_parent_id: dict[str, str] = {}
    for evt in events:
        if evt.get("type") == "register":
            agent_parent_id[evt["agent_id"]] = evt.get("parent_id", "") or ""

    violations = 0
    scored_events = 0

    for evt in events:
        etype = evt.get("type")
        if etype in COORDINATION_PRIMITIVES:
            continue

        aid = evt.get("agent_id", "")
        gid = agent_graph_id.get(aid, "")
        own_resp = graph_responsibilities.get(gid, set())

        parent_id = agent_parent_id.get(aid, "")
        parent_resp: set[str] = set()
        if parent_id:
            parent_gid = agent_graph_id.get(parent_id, "")
            parent_resp = graph_responsibilities.get(parent_gid, set())

        effective_resp = own_resp | parent_resp
        if not effective_resp:
            continue

        if not event_matches_responsibility(etype, effective_resp):
            violations += 1
        scored_events += 1

    if scored_events == 0:
        return 1.0
    return max(0.0, 1.0 - (violations / scored_events))


def score_leader_stability(trace: dict[str, Any], graph: Any) -> float:
    """Score 0-1: did the coordinator lease stay with one agent throughout the trace?

    T3.11: pre-fix this function returned the configured ``threshold``
    (0.8) as if it were a measurement, which showed up in the Markdown
    report as a spurious "leader_stability: 80%". Now the trace-event
    scan is always the source of truth; the ``assessment.leader_stability``
    config on the graph contributes a minimum-threshold warning only
    when the measured score falls below it.
    """
    events = trace.get("events", [])
    if not events:
        return 1.0

    # Scan events for any "transfer" or "leaseExpired" markers.
    transfer_markers = {"transfer", "leaseExpired", "leadershipClaimed"}
    markers = [e for e in events if e.get("type") in transfer_markers]
    if not markers:
        measured = 1.0
    else:
        measured = max(0.0, 1.0 - (len(markers) * 0.25))

    # Graph can warn if measurement is below the configured floor, but
    # never fabricates a score out of the threshold value itself.
    if graph and hasattr(graph, "assessment") and graph.assessment:
        cfg = graph.assessment.get("leader_stability")
        if cfg:
            threshold = cfg.get("threshold", 0.8)
            if measured < threshold:
                # Signal the drop but don't invent a different number.
                return measured
    return measured


# ------------------------------------------------------------------ #
# Registry
# ------------------------------------------------------------------ #

METRIC_SCORERS: dict[str, Any] = {
    "role_stability": score_role_stability,
    "handoff_latency": score_handoff_latency,
    "outcome_verifiability": score_outcome_verifiability,
    "protocol_adherence": score_protocol_adherence,
    "spawn_propagation": score_spawn_propagation,
    "leader_stability": score_leader_stability,
}
