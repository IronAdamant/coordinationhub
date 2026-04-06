"""Declarative coordination graph loader, validator, and in-memory representation.

Re-exports from domain-specific sub-modules:
- graph_validate: validation functions
- graph_loader: file loading and spec auto-detection
- graph: CoordinationGraph in-memory object

Exports (backward-compatible):
- validate_graph, load_graph, find_graph_spec, CoordinationGraph
- set_graph, get_graph, clear_graph (singleton)
- load_coordination_spec_from_disk, validate_graph_tool

Supports both YAML (via ruamel.yaml) and JSON coordination spec files.
Falls back gracefully if the YAML library is unavailable.
"""

from __future__ import annotations

from .graph_validate import validate_graph as _validate_graph
from .graph_validate import (
    REQUIRED_AGENT_FIELDS,
    OPTIONAL_AGENT_FIELDS,
    ALL_AGENT_FIELDS,
    REQUIRED_HANDOFF_FIELDS,
    ALL_HANDOFF_FIELDS,
    REQUIRED_ESCALATION_FIELDS,
    ALL_ESCALATION_FIELDS,
    REQUIRED_ASSESSMENT_FIELDS,
    ALL_ASSESSMENT_FIELDS,
)
from .graph_loader import load_graph, find_graph_spec, _YAML_AVAILABLE
from .graph import CoordinationGraph

# Re-export validate_graph at module level for backward compatibility
validate_graph = _validate_graph


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
    """
    target = path or find_graph_spec(project_root)
    if target is None or not target.is_file():
        clear_graph()
        return {"loaded": False, "path": None}
    try:
        data = load_graph(target)
        validation = _validate_graph(data)
        if not validation["valid"]:
            clear_graph()
            return {"loaded": False, "errors": validation["errors"]}
        graph = set_graph(data)
        return {
            "loaded": True,
            "path": str(target),
            "agent_count": len(graph.agents),
            "agents": list(graph.agents.keys()),
        }
    except Exception as exc:
        clear_graph()
        return {"loaded": False, "error": str(exc)}


def validate_graph_tool() -> dict[str, Any]:
    """MCP tool implementation: validate the currently loaded graph."""
    graph = get_graph()
    if graph is None:
        return {"valid": False, "errors": ["No coordination graph is currently loaded"]}
    errors = graph.validation_errors()
    return {"valid": len(errors) == 0, "errors": errors}
