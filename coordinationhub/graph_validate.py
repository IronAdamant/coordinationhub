"""Coordination graph validation functions.

Pure functions that validate a graph data dict and return error lists.
No internal module dependencies.
"""

from __future__ import annotations

from typing import Any


# ------------------------------------------------------------------ #
# Schema field constants
# ------------------------------------------------------------------ #

REQUIRED_AGENT_FIELDS = frozenset({"id", "role", "responsibilities"})
OPTIONAL_AGENT_FIELDS = frozenset({"model"})
ALL_AGENT_FIELDS = REQUIRED_AGENT_FIELDS | OPTIONAL_AGENT_FIELDS

REQUIRED_HANDOFF_FIELDS = frozenset({"from", "to", "condition"})
ALL_HANDOFF_FIELDS = REQUIRED_HANDOFF_FIELDS

REQUIRED_ESCALATION_FIELDS = frozenset({"max_retries", "fallback"})
ALL_ESCALATION_FIELDS = REQUIRED_ESCALATION_FIELDS

REQUIRED_ASSESSMENT_FIELDS = frozenset({"metrics"})
ALL_ASSESSMENT_FIELDS = REQUIRED_ASSESSMENT_FIELDS


# ------------------------------------------------------------------ #
# Per-section validators
# ------------------------------------------------------------------ #

def _validate_agents(agents: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    if not isinstance(agents, list):
        return ["agents must be a list"]
    seen_ids: set[str] = set()
    for i, agent in enumerate(agents):
        if not isinstance(agent, dict):
            errors.append(f"agents[{i}]: must be an object")
            continue
        missing = REQUIRED_AGENT_FIELDS - frozenset(agent.keys())
        if missing:
            errors.append(f"agents[{i}]: missing required fields: {sorted(missing)}")
        unknown = frozenset(agent.keys()) - ALL_AGENT_FIELDS
        if unknown:
            errors.append(f"agents[{i}]: unknown fields: {sorted(unknown)}")
        if "id" in agent and agent["id"] in seen_ids:
            errors.append(f"agents[{i}]: duplicate agent id {agent['id']!r}")
        seen_ids.add(agent.get("id", ""))
        if "responsibilities" in agent and not isinstance(agent["responsibilities"], list):
            errors.append(f"agents[{i}].responsibilities must be a list")
    return errors


def _validate_handoffs(handoffs: list[dict[str, Any]], agent_ids: set[str]) -> list[str]:
    errors: list[str] = []
    if not isinstance(handoffs, list):
        return ["handoffs must be a list"]
    for i, handoff in enumerate(handoffs):
        if not isinstance(handoff, dict):
            errors.append(f"handoffs[{i}]: must be an object")
            continue
        missing = REQUIRED_HANDOFF_FIELDS - frozenset(handoff.keys())
        if missing:
            errors.append(f"handoffs[{i}]: missing required fields: {sorted(missing)}")
        unknown = frozenset(handoff.keys()) - ALL_HANDOFF_FIELDS
        if unknown:
            errors.append(f"handoffs[{i}]: unknown fields: {sorted(unknown)}")
        if agent_ids:
            for field in ("from", "to"):
                if handoff.get(field) not in agent_ids:
                    errors.append(f"handoffs[{i}].{field}: {handoff[field]!r} is not a defined agent id")
    return errors


def _validate_escalation(escalation: dict[str, Any] | None) -> list[str]:
    if escalation is None:
        return []
    errors: list[str] = []
    if not isinstance(escalation, dict):
        return ["escalation must be an object"]
    missing = REQUIRED_ESCALATION_FIELDS - frozenset(escalation.keys())
    if missing:
        errors.append(f"escalation: missing required fields: {sorted(missing)}")
    unknown = frozenset(escalation.keys()) - ALL_ESCALATION_FIELDS
    if unknown:
        errors.append(f"escalation: unknown fields: {sorted(unknown)}")
    if "max_retries" in escalation and not isinstance(escalation["max_retries"], int):
        errors.append("escalation.max_retries must be an integer")
    return errors


def _validate_assessment(assessment: dict[str, Any] | None) -> list[str]:
    if assessment is None:
        return []
    errors: list[str] = []
    if not isinstance(assessment, dict):
        return ["assessment must be an object"]
    missing = REQUIRED_ASSESSMENT_FIELDS - frozenset(assessment.keys())
    if missing:
        errors.append(f"assessment: missing required fields: {sorted(missing)}")
    unknown = frozenset(assessment.keys()) - ALL_ASSESSMENT_FIELDS
    if unknown:
        errors.append(f"assessment: unknown fields: {sorted(unknown)}")
    if "metrics" in assessment:
        if not isinstance(assessment["metrics"], list):
            errors.append("assessment.metrics must be a list")
        elif not all(isinstance(m, str) for m in assessment["metrics"]):
            errors.append("assessment.metrics must be a list of strings")
    return errors


# ------------------------------------------------------------------ #
# Top-level validate_graph
# ------------------------------------------------------------------ #

def validate_graph(data: dict[str, Any]) -> dict[str, Any]:
    """Validate a coordination graph dict. Returns {valid: bool, errors: [str]}."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return {"valid": False, "errors": ["root must be an object"]}
    if "agents" not in data:
        errors.append("agents: required field is missing")
    errors.extend(_validate_agents(data.get("agents", [])))
    agent_ids = {a["id"] for a in data.get("agents", []) if isinstance(a, dict) and "id" in a}
    errors.extend(_validate_handoffs(data.get("handoffs", []), agent_ids))
    errors.extend(_validate_escalation(data.get("escalation")))
    errors.extend(_validate_assessment(data.get("assessment")))
    return {"valid": len(errors) == 0, "errors": errors}
