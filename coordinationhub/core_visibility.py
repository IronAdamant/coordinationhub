"""VisibilityMixin — coordination graph, project scan, agent status, assessment.

Expects the host class to provide:
    self._connect()     — callable returning a sqlite3 connection
    self._storage        — CoordinationStorage instance (provides project_root)
    self._graph          — loaded graph (or None) — set by host.start()

Delegates to: graphs (graphs.py), scan (scan.py), agent_status (agent_status.py),
assessment (assessment.py)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .plugins.graph import graphs as _g
from . import scan as _scan
from . import agent_status as _v
from .plugins.assessment import assessment as _assess


class VisibilityMixin:
    """Coordination graph, file ownership scan, agent status, and assessment."""

    # ------------------------------------------------------------------ #
    # Graph & Visibility
    # ------------------------------------------------------------------ #

    def load_coordination_spec(self, path: str | None = None) -> dict[str, Any]:
        """Load or reload a YAML/JSON coordination spec from disk."""
        target = Path(path) if path else None
        if path and target and not target.is_file():
            return {"loaded": False, "error": f"Coordination spec not found: {path}"}
        return _g.load_coordination_spec_from_disk(
            self._connect, self._storage.project_root, target,
        )

    def validate_graph(self) -> dict[str, Any]:
        """Validate the loaded coordination graph."""
        return _g.validate_graph_tool()

    def scan_project(
        self,
        worktree_root: str | None = None,
        extensions: list[str] | None = None,
    ) -> dict[str, Any]:
        """Scan project files and assign ownership based on coordination graph."""
        if extensions is not None and not extensions:
            return {"scanned": 0, "owned": 0, "error": "extensions list cannot be empty"}
        graph = _g.get_graph()
        return _scan.scan_project_tool(
            self._connect, self._storage.project_root, worktree_root, extensions, graph,
        )

    def get_agent_status(self, agent_id: str) -> dict[str, Any]:
        """Get full status for an agent: locks, notifications, descendants, responsibilities."""
        # get_lineage is on the host (IdentityMixin), pass it as a callable
        return _v.get_agent_status_tool(self._connect, agent_id, self.get_lineage)

    def get_agent_tree(self, agent_id: str | None = None) -> dict[str, Any]:
        """Print agent hierarchy as a tree."""
        return _v.get_agent_tree_tool(self._connect, agent_id)

    def get_file_agent_map(self, agent_id: str | None = None) -> dict[str, Any]:
        """Get mapping of files to the agents that own them."""
        return _v.get_file_agent_map_tool(self._connect, agent_id)

    def update_agent_status(
        self,
        agent_id: str,
        current_task: str | None = None,
        scope: list[str] | None = None,
    ) -> dict[str, Any]:
        """Update an agent's current task or declared scope."""
        return _v.update_agent_status_tool(self._connect, agent_id, current_task, scope)

    def run_assessment(
        self,
        suite_path: str,
        format: str = "markdown",
        graph_agent_id: str | None = None,
    ) -> dict[str, Any]:
        """Run a pre-authored assessment suite and return scores."""
        suite_file = Path(suite_path)
        if not suite_file.is_file():
            return {"error": f"Suite file not found: {suite_path}"}
        try:
            suite = _assess.load_suite(suite_file)
        except Exception as exc:
            return {"error": f"Failed to load suite: {exc}"}
        graph = _g.get_graph()
        with self._connect() as conn:
            result = _assess.run_assessment(suite, graph, graph_agent_id=graph_agent_id)
            _assess.store_assessment_results(conn, result)
        if format == "json":
            return result
        report = _assess.format_markdown_report(result)
        return {"report": report, "scores": result}

    def assess_current_session(
        self,
        format: str = "markdown",
        graph_agent_id: str | None = None,
        scope: str = "project",
    ) -> dict[str, Any]:
        """Build a trace from current DB state and run assessment.

        Reads live hook-recorded state (agents, notifications, lineage) and
        synthesizes a trace suite via build_suite_from_db.
        Works even when no coordination graph is loaded — scores ad-hoc sessions.
        """
        graph = _g.get_graph()
        worktree_root = (
            str(self._storage.project_root)
            if scope == "project" and self._storage.project_root
            else None
        )
        suite = _assess.build_suite_from_db(
            self._connect,
            suite_name="live_session",
            worktree_root=worktree_root,
        )
        with self._connect() as conn:
            result = _assess.run_assessment(
                suite, graph, graph_agent_id=graph_agent_id,
            )
            _assess.store_assessment_results(conn, result)
        if format == "json":
            return result
        report = _assess.format_markdown_report(result)
        return {"report": report, "scores": result}