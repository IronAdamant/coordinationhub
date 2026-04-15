"""Declarative coordination graph: loader, validator, in-memory representation.

Supports YAML (via ruamel.yaml) and JSON coordination spec files.  Falls
back gracefully if the YAML library is unavailable.  Zero internal deps
on other coordinationhub modules.
"""

from __future__ import annotations

import json
import time as _time
from pathlib import Path
from typing import Any


# ------------------------------------------------------------------ #
# Validation — schema field constants and per-section validators
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


# ------------------------------------------------------------------ #
# File loading — YAML + JSON, spec auto-detection
# ------------------------------------------------------------------ #

# Try YAML; degrade to JSON-only if not available
_YAML_AVAILABLE = False
try:
    from ruamel.yaml import YAML as _YAML

    _YAML_AVAILABLE = True
except ImportError:
    pass


def load_graph(path: Path) -> dict[str, Any]:
    """Load a coordination graph from a YAML or JSON file."""
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        if not _YAML_AVAILABLE:
            raise ImportError(
                "YAML support requires ruamel.yaml. "
                "Install it with: pip install coordinationhub[yaml] "
                "Or use coordination_spec.json instead."
            )
        yaml = _YAML(typ="safe")
        return yaml.load(text)  # type: ignore[return-value]
    return json.loads(text)


def find_graph_spec(project_root: Path | None) -> Path | None:
    """Look for coordination_spec.yaml then .yml then .json at project root."""
    if project_root is None:
        return None
    for candidate in [
        project_root / "coordination_spec.yaml",
        project_root / "coordination_spec.yml",
        project_root / "coordination_spec.json",
    ]:
        if candidate.is_file():
            return candidate
    return None


# ------------------------------------------------------------------ #
# CoordinationGraph — in-memory representation
# ------------------------------------------------------------------ #

class CoordinationGraph:
    """In-memory coordination graph with lookup helpers."""

    __slots__ = ("_data", "_agents", "_handoff_map")

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data
        self._agents: dict[str, dict[str, Any]] = {
            a["id"]: a for a in data.get("agents", []) if "id" in a
        }
        self._handoff_map: dict[str, list[dict[str, Any]]] = {}
        for h in data.get("handoffs", []):
            key = h.get("from", "")
            if key:
                self._handoff_map.setdefault(key, []).append(h)

    @property
    def raw(self) -> dict[str, Any]:
        return self._data

    @property
    def agents(self) -> dict[str, dict[str, Any]]:
        return self._agents

    @property
    def handoffs(self) -> list[dict[str, Any]]:
        return self._data.get("handoffs", [])

    @property
    def escalation(self) -> dict[str, Any] | None:
        return self._data.get("escalation")

    @property
    def assessment(self) -> dict[str, Any] | None:
        return self._data.get("assessment")

    def agent(self, graph_id: str) -> dict[str, Any] | None:
        return self._agents.get(graph_id)

    def outgoing_handoffs(self, from_id: str) -> list[dict[str, Any]]:
        return self._handoff_map.get(from_id, [])

    def handoff_targets(self, from_id: str) -> list[str]:
        return [h["to"] for h in self.outgoing_handoffs(from_id)]

    def is_valid(self) -> bool:
        return validate_graph(self._data)["valid"]

    def validation_errors(self) -> list[str]:
        return validate_graph(self._data)["errors"]

    def agent_ids(self) -> list[str]:
        return list(self._agents.keys())


# ------------------------------------------------------------------ #
# Module-level singleton
# ------------------------------------------------------------------ #

_loaded_graph: CoordinationGraph | None = None


def set_graph(data: dict[str, Any]) -> CoordinationGraph:
    """Set the module-level loaded graph."""
    global _loaded_graph
    _loaded_graph = CoordinationGraph(data)
    return _loaded_graph


def get_graph() -> CoordinationGraph | None:
    """Return the currently loaded graph, or None."""
    return _loaded_graph


def clear_graph() -> None:
    """Clear the loaded graph."""
    global _loaded_graph
    _loaded_graph = None


# ------------------------------------------------------------------ #
# Graph tool implementations (used by core.py)
# ------------------------------------------------------------------ #

def load_coordination_spec_from_disk(
    connect,
    project_root,
    path=None,
) -> dict[str, Any]:
    """Load (or reload) the coordination graph from disk.

    Returns dict with loaded/path/agent_count/agents on success,
    or loaded=False with error info.

    After a successful load, pre-populates agent_responsibilities for any
    registered agents whose agent_id matches a graph agent id.
    """
    target = path or find_graph_spec(project_root)
    if target is None or not target.is_file():
        clear_graph()
        return {"loaded": False, "path": None}
    try:
        data = load_graph(target)
        validation = validate_graph(data)
        if not validation["valid"]:
            clear_graph()
            return {"loaded": False, "errors": validation["errors"]}
        graph = set_graph(data)

        # Enforce graph_agent_id mapping: for each graph agent, if a registered
        # agent with that exact agent_id exists, populate agent_responsibilities.
        _populate_agent_responsibilities_from_graph(connect, graph)

        return {
            "loaded": True,
            "path": str(target),
            "agent_count": len(graph.agents),
            "agents": list(graph.agents.keys()),
        }
    except Exception as exc:
        clear_graph()
        return {"loaded": False, "error": str(exc)}


def _populate_agent_responsibilities_from_graph(
    connect,
    graph: CoordinationGraph,
) -> None:
    """For each graph agent whose id matches a registered agent, upsert agent_responsibilities."""
    now = _time.time()
    for graph_id, agent_def in graph.agents.items():
        with connect() as conn:
            row = conn.execute(
                "SELECT agent_id FROM agents WHERE agent_id = ? AND status = 'active'",
                (graph_id,),
            ).fetchone()
        if row:
            role = agent_def.get("role", "")
            model = agent_def.get("model", "")
            responsibilities = agent_def.get("responsibilities", [])
            with connect() as conn:
                conn.execute("""
                    INSERT INTO agent_responsibilities
                    (agent_id, graph_agent_id, role, model, responsibilities, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(agent_id) DO UPDATE SET
                        graph_agent_id = excluded.graph_agent_id,
                        role = excluded.role,
                        model = excluded.model,
                        responsibilities = excluded.responsibilities,
                        updated_at = excluded.updated_at
                """, (graph_id, graph_id, role, model, json.dumps(responsibilities), now))


def build_implicit_graph(connect) -> CoordinationGraph:
    """Build a minimal coordination graph from the live agent tree.

    Used as a fallback when no coordination_spec.yaml is loaded so that
    scan_project and assessment still have a graph to work with.
    """
    agents: list[dict[str, Any]] = []
    handoffs: list[dict[str, Any]] = []
    agent_ids: set[str] = set()

    with connect() as conn:
        rows = conn.execute(
            "SELECT agent_id, parent_id FROM agents WHERE status = 'active'"
        ).fetchall()

    # Always include an orchestrator node for the root
    agents.append({
        "id": "orchestrator",
        "role": "project manager",
        "responsibilities": ["coordinate", "orchestrate", "monitor"],
    })
    agent_ids.add("orchestrator")

    for row in rows:
        agent_id = row["agent_id"]
        parent_id = row["parent_id"]
        if parent_id is None:
            # Root agent maps to orchestrator
            continue
        # Derive a graph role from the agent_id suffix or use "worker"
        role = "worker"
        if "." in agent_id:
            suffix = agent_id.rsplit(".", 1)[-1]
            if suffix.isdigit():
                role = "worker"
            else:
                role = suffix
        graph_id = agent_id
        if graph_id not in agent_ids:
            agents.append({
                "id": graph_id,
                "role": role,
                "responsibilities": ["implement", "write", "code"],
            })
            agent_ids.add(graph_id)
        # Add handoff from orchestrator to child
        handoffs.append({
            "from": "orchestrator",
            "to": graph_id,
            "condition": "task_assigned",
        })
        # Add handoff from parent to child if parent is also active
        if parent_id in {r["agent_id"] for r in rows}:
            handoffs.append({
                "from": parent_id,
                "to": graph_id,
                "condition": "subtask_created",
            })

    data = {"agents": agents, "handoffs": handoffs}
    return CoordinationGraph(data)


def validate_graph_tool() -> dict[str, Any]:
    """MCP tool implementation: validate the currently loaded graph."""
    graph = get_graph()
    if graph is None:
        return {"valid": False, "errors": ["No coordination graph is currently loaded"]}
    errors = graph.validation_errors()
    return {"valid": len(errors) == 0, "errors": errors}
