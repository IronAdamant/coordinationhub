"""Assessment plugin for CoordinationHub."""

from __future__ import annotations

from typing import Any

from .assessment import (
    build_suite_from_db,
    build_trace_from_db,
    run_assessment,
    store_assessment_results,
    prune_assessment_results,
)
__all__ = [
    "build_suite_from_db",
    "build_trace_from_db",
    "run_assessment",
    "store_assessment_results",
    "prune_assessment_results",
    "register_tools",
    "register_cli",
]


def register_tools(dispatch_table: dict[str, tuple[str, list[str]]]) -> None:
    """Register assessment tools in the dispatch table."""
    dispatch_table["run_assessment"] = ("run_assessment", ["suite_path", "format", "graph_agent_id"])
    dispatch_table["assess_current_session"] = (
        "assess_current_session", ["format", "graph_agent_id", "scope"]
    )


def register_cli(subparsers) -> None:
    """Register assessment CLI commands."""
    # Handled by existing cli_vis.py dispatch; no-op for now
    pass
