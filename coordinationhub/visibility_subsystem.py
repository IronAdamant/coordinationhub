"""Visibility subsystem — coordination graph, project scan, agent status, assessment.

T6.22 ninth step: extracted out of ``core_visibility.VisibilityMixin`` into
a standalone class. Coupling audit confirmed VisibilityMixin had zero
cross-mixin method calls and used three pieces of engine state —
``_connect``, ``_publish_event``, and ``_storage.project_root`` (for
``scan_project`` and ``run_assessment`` when ``scope='project'``) — which
are now injected as constructor dependencies. Same path-access shape as
:class:`WorkIntent` (commit ``3d1bd48``) and :class:`Change` (commit
``e0c21a8``): ``project_root_getter`` is a callable so a replica
produced by ``read_only_engine`` picks up its own storage root without
a rebind. The two infra callables follow the :class:`Lease` /
:class:`Dependency` pattern (commits ``b4a3e6b``, ``d6c8796``) — no
``_hybrid_wait`` dep since VisibilityMixin never waited on events.

Graph access note: while the VisibilityMixin docstring mentioned
``self._graph``, the mixin never actually read that attribute. The
loaded graph is stored at module level in ``plugins/graph/graphs.py``
via ``set_graph`` / ``get_graph`` / ``clear_graph``;
``_effective_graph`` reads that singleton directly. That means the
Visibility subsystem needs no ``graph_getter`` / ``graph_setter`` —
module-level state is shared automatically, so ``load_coordination_spec``
moves into the subsystem cleanly along with every other method.

See commits ``1ee46c6`` (Spawner), ``3d1bd48`` (WorkIntent),
``b4a3e6b`` (Lease), ``d6c8796`` (Dependency), ``d9f84d3``
(Messaging), ``ded641d`` (Handoff), ``e0c21a8`` (Change), and
``8182c7a`` (Task) for the eight prior extractions in this series.
This continues breaking the god-object inheritance chain on
``CoordinationEngine`` without changing observable behaviour.

Delegates to: graphs (plugins/graph/graphs.py), scan (scan.py),
agent_status (agent_status.py), assessment (plugins/assessment/assessment.py).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .plugins.graph import graphs as _g
from . import scan as _scan
from . import agent_status as _v
from .plugins.assessment import assessment as _assess
from . import agent_registry as _ar


class Visibility:
    """Coordination graph, file ownership scan, agent status, and assessment.

    Constructed by :class:`CoordinationEngine` and exposed as
    ``engine._visibility``. The engine keeps facade methods for each
    public operation so the existing tool API is preserved.
    """

    def __init__(
        self,
        connect_fn: Callable[[], Any],
        publish_event_fn: Callable[[str, dict[str, Any]], None],
        project_root_getter: Callable[[], Path | None],
    ) -> None:
        self._connect = connect_fn
        self._publish_event = publish_event_fn
        self._project_root_getter = project_root_getter

    # ------------------------------------------------------------------ #
    # Graph & Visibility
    # ------------------------------------------------------------------ #

    def load_coordination_spec(self, path: str | None = None) -> dict[str, Any]:
        """Load or reload a YAML/JSON coordination spec from disk."""
        target = Path(path) if path else None
        if path and target and not target.is_file():
            return {"loaded": False, "error": f"Coordination spec not found: {path}"}
        result = _g.load_coordination_spec_from_disk(
            self._connect, self._project_root_getter(), target,
        )
        self._publish_event(
            "graph.loaded",
            {"loaded": result.get("loaded"), "path": str(target) if target else None},
        )
        return result

    def validate_graph(self) -> dict[str, Any]:
        """Validate the loaded coordination graph."""
        return _g.validate_graph_tool()

    def _effective_graph(self):
        """Return the loaded graph or a dynamically-built implicit graph."""
        graph = _g.get_graph()
        if graph is not None:
            return graph
        return _g.build_implicit_graph(self._connect)

    def scan_project(
        self,
        worktree_root: str | None = None,
        extensions: list[str] | None = None,
    ) -> dict[str, Any]:
        """Scan project files and assign ownership based on coordination graph."""
        if extensions is not None and not extensions:
            return {"scanned": 0, "owned": 0, "error": "extensions list cannot be empty"}
        graph = self._effective_graph()
        project_root = self._project_root_getter()
        result = _scan.scan_project_tool(
            self._connect, project_root, worktree_root, extensions, graph,
        )
        self._publish_event(
            "scan.completed",
            {
                "scanned": result.get("scanned", 0),
                "owned": result.get("owned", 0),
                "worktree_root": worktree_root or str(project_root),
            },
        )
        return result

    def get_agent_status(self, agent_id: str) -> dict[str, Any]:
        """Get full status for an agent: locks, notifications, descendants, responsibilities."""
        # Avoid MRO/HTTP transport issues by using the module-level get_lineage directly
        return _v.get_agent_status_tool(
            self._connect, agent_id,
            lambda aid: _ar.get_lineage(self._connect, aid),
        )

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
        suite_path: str | None = None,
        format: str = "markdown",
        graph_agent_id: str | None = None,
        scope: str = "project",
    ) -> dict[str, Any]:
        """Run an assessment suite or score the live session.

        If suite_path is provided, loads the JSON suite and runs it.
        If suite_path is omitted, synthesizes a live session trace from DB state.
        """
        graph = self._effective_graph()
        project_root = self._project_root_getter()
        if suite_path is not None:
            suite_file = Path(suite_path)
            if not suite_file.is_file():
                return {"error": f"Suite file not found: {suite_path}"}
            try:
                suite = _assess.load_suite(suite_file)
            except Exception as exc:
                return {"error": f"Failed to load suite: {exc}"}
        else:
            worktree_root = (
                str(project_root)
                if scope == "project" and project_root
                else None
            )
            suite = _assess.build_suite_from_db(
                self._connect,
                suite_name="live_session",
                worktree_root=worktree_root,
            )

        with self._connect() as conn:
            result = _assess.run_assessment(suite, graph, graph_agent_id=graph_agent_id)
            _assess.store_assessment_results(conn, result)

        self._publish_event(
            "assessment.completed",
            {
                "suite_path": suite_path,
                "graph_agent_id": graph_agent_id,
                "scores": result.get("scores", {}),
            },
        )

        if format == "json":
            return result
        report = _assess.format_markdown_report(result)
        return {"report": report, "scores": result}

    def prune_assessment_results(
        self, max_age_seconds: float = 30 * 24 * 3600.0,
    ) -> dict[str, Any]:
        """Delete ``assessment_results`` rows older than ``max_age_seconds``.

        T7.32: ``details_json`` carries the full trace per metric, so the
        table grows quickly on a hub that runs assessments periodically.
        Default retention is 30 days; the HousekeepingScheduler calls this
        on a timer.
        """
        with self._connect() as conn:
            return _assess.prune_assessment_results(conn, max_age_seconds)
