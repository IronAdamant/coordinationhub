"""Web dashboard for CoordinationHub — zero external dependencies.

Provides a self-contained HTML dashboard that polls API endpoints
and renders agent trees, task boards, work intents, and handoffs
using pure SVG (no Mermaid, no D3, no CDN).

Usage:
    from .dashboard import get_dashboard_data, DASHBOARD_HTML

    # In MCP server:
    if self.path == "/":
        self._serve_dashboard()
    elif self.path.startswith("/api/"):
        self._serve_api(self.path)

    # Get aggregated data for API endpoints:
    data = get_dashboard_data(engine.connect)
"""

from __future__ import annotations

from typing import Any, Callable

# Type alias for the connect function passed by callers
ConnectFn = Callable[[], Any]


# ------------------------------------------------------------------ #
# Data aggregation
# ------------------------------------------------------------------ #

def get_dashboard_data(connect: ConnectFn) -> dict[str, Any]:
    """Aggregate all tables into a single dict for the dashboard.

    Returns:
        {
            "agents": [...],
            "tasks": [...],
            "work_intents": [...],
            "handoffs": [...],
            "dependencies": [...],
            "locks": [...],
        }
    """
    conn = connect()

    def _dict(rows, key=None):
        if key is None:
            return [dict(r) for r in rows]
        return {dict(r)[key]: dict(r) for r in rows}

    return {
        "agents": _dict(conn.execute(
            """
            SELECT a.*, r.current_task, r.role, r.graph_agent_id
            FROM agents a
            LEFT JOIN agent_responsibilities r ON r.agent_id = a.agent_id
            WHERE a.status != 'stopped'
            ORDER BY a.started_at
            """
        ).fetchall()),
        "tasks": _dict(conn.execute(
            "SELECT * FROM tasks ORDER BY created_at DESC"
        ).fetchall()),
        "work_intents": _dict(conn.execute(
            "SELECT * FROM work_intent ORDER BY declared_at DESC"
        ).fetchall()),
        "handoffs": _dict(conn.execute(
            "SELECT * FROM handoffs ORDER BY created_at DESC LIMIT 100"
        ).fetchall()),
        "dependencies": _dict(conn.execute(
            "SELECT * FROM agent_dependencies ORDER BY created_at DESC"
        ).fetchall()),
        "locks": _dict(conn.execute(
            """
            SELECT l.*, o.assigned_agent_id AS owner_agent_id
            FROM document_locks l
            LEFT JOIN file_ownership o ON o.document_path = l.document_path
            ORDER BY l.locked_at DESC
            """
        ).fetchall()),
    }


# ------------------------------------------------------------------ #
# HTML template — kept in a sibling module so the Python logic here
# stays well under the project's 500-code-LOC rule.  Re-exported so
# callers can keep ``from .dashboard import DASHBOARD_HTML``.
# ------------------------------------------------------------------ #

from .dashboard_html import DASHBOARD_HTML  # noqa: E402,F401


def _serve_dashboard(handler) -> None:
    """Serve the dashboard HTML (used by MCPRequestHandler)."""
    body = DASHBOARD_HTML.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _serve_api_dashboard(handler, engine) -> None:
    """Serve aggregated dashboard data as JSON."""
    import json
    data = get_dashboard_data(engine.connect)
    body = json.dumps(data, default=str).encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)