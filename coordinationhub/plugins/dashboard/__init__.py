"""Dashboard plugin for CoordinationHub."""

from __future__ import annotations

from .dashboard import DASHBOARD_HTML, get_dashboard_data

__all__ = [
    "DASHBOARD_HTML",
    "get_dashboard_data",
    "register_tools",
    "register_cli",
]


def register_tools(dispatch_table: dict[str, tuple[str, list[str]]]) -> None:
    """Dashboard has no MCP tools; it serves HTTP endpoints."""
    pass


def register_cli(subparsers) -> None:
    """Register dashboard CLI commands."""
    pass
