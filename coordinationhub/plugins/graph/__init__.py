"""Graph plugin for CoordinationHub."""

from __future__ import annotations

from .graphs import (
    CoordinationGraph,
    clear_graph,
    find_graph_spec,
    get_graph,
    load_coordination_spec_from_disk,
    load_graph,
    set_graph,
    validate_graph,
)

__all__ = [
    "CoordinationGraph",
    "clear_graph",
    "find_graph_spec",
    "get_graph",
    "load_coordination_spec_from_disk",
    "load_graph",
    "set_graph",
    "validate_graph",
    "register_tools",
    "register_cli",
]


def register_tools(dispatch_table: dict[str, tuple[str, list[str]]]) -> None:
    """Register graph tools in the dispatch table."""
    dispatch_table["load_coordination_spec"] = ("load_coordination_spec", ["path"])
    dispatch_table["validate_graph"] = ("validate_graph", [])


def register_cli(subparsers) -> None:
    """Register graph CLI commands."""
    pass
