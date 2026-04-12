"""CoordinationEngine — core business logic for CoordinationHub.

Wires together the storage backend, agent_registry, lock_ops, conflict_log,
notifications, graph loading, and visibility helpers.

Locking and coordination methods live in core_locking.py (LockingMixin).
Project-root detection and path normalization live in paths.py.
Zero third-party dependencies.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from . import agent_registry as _ar
from . import conflict_log as _cl
from . import notifications as _cn
from . import lock_ops as _lo
from . import graphs as _g
from . import agent_status as _v
from . import scan as _scan
from . import assessment as _assess
from . import messages as _msg
from . import tasks as _tasks
from . import work_intent as _wi
from . import handoffs as _handoffs
from . import dependencies as _deps
from ._storage import CoordinationStorage
from .context import build_context_bundle
from .core_locking import LockingMixin
from .dispatch import TOOL_DISPATCH
from .paths import detect_project_root, normalize_path


class CoordinationEngine(LockingMixin):
    """Main coordinator. Manages agent identity, document locking, graph loading,
    file ownership tracking, and change notifications. Thread-safe via SQLite WAL.

    Locking and coordination methods are provided by ``LockingMixin``.
    """

    DEFAULT_PORT = 9877
    HEARTBEAT_INTERVAL = 30
    DEFAULT_TTL = 300.0

    def __init__(
        self,
        storage_dir: Path | None = None,
        project_root: Path | None = None,
        namespace: str = "hub",
    ) -> None:
        self._storage = CoordinationStorage(
            storage_dir=storage_dir,
            project_root=project_root or detect_project_root(),
            namespace=namespace,
        )

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        self._storage.start()
        _g.load_coordination_spec_from_disk(self._connect, self._storage.project_root)

    def close(self) -> None:
        self._storage.close()

    def _connect(self):
        return self._storage._connect()

    # ------------------------------------------------------------------ #
    # Agent ID generation
    # ------------------------------------------------------------------ #

    def generate_agent_id(self, parent_id: str | None = None) -> str:
        return self._storage.generate_agent_id(parent_id)

    # ------------------------------------------------------------------ #
    # Identity & Registration
    # ------------------------------------------------------------------ #

    def register_agent(
        self,
        agent_id: str,
        parent_id: str | None = None,
        graph_agent_id: str | None = None,
        worktree_root: str | None = None,
        claude_agent_id: str | None = None,
    ) -> dict[str, Any]:
        worktree = worktree_root or (
            str(self._storage.project_root) if self._storage.project_root else os.getcwd()
        )
        _ar.register_agent(self._connect, agent_id, worktree, parent_id, claude_agent_id=claude_agent_id)
        if parent_id is not None:
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO lineage (parent_id, child_id, spawned_at) VALUES (?, ?, ?)",
                    (parent_id, agent_id, time.time()),
                )
        if graph_agent_id:
            graph = _g.get_graph()
            if graph:
                agent_def = graph.agent(graph_agent_id)
                if agent_def:
                    _scan.store_responsibilities(
                        self._connect,
                        agent_id,
                        graph_agent_id,
                        agent_def.get("role", ""),
                        agent_def.get("model", ""),
                        agent_def.get("responsibilities", []),
                    )
        return self._context_bundle(agent_id, parent_id)

    def heartbeat(self, agent_id: str) -> dict[str, Any]:
        updated = _ar.heartbeat(self._connect, agent_id)
        return {"updated": updated.get("updated", False), "next_heartbeat_in": self.HEARTBEAT_INTERVAL}

    def deregister_agent(self, agent_id: str) -> dict[str, Any]:
        result = _ar.deregister_agent(self._connect, agent_id)
        with self._connect() as conn:
            lock_result = _lo.release_agent_locks(conn, "document_locks", agent_id, delete=True)
        result["locks_released"] = lock_result.get("released", 0)
        return result

    def list_agents(
        self, active_only: bool = True, stale_timeout: float = 600.0,
    ) -> dict[str, Any]:
        agents = _ar.list_agents(self._connect, active_only, stale_timeout)
        return {"agents": agents}

    def get_lineage(self, agent_id: str) -> dict[str, Any]:
        return _ar.get_lineage(self._connect, agent_id)

    def get_siblings(self, agent_id: str) -> dict[str, Any]:
        siblings = _ar.get_siblings(self._connect, agent_id)
        return {"siblings": siblings}

    def find_agent_by_claude_id(self, claude_agent_id: str) -> str | None:
        """Look up a hub.cc.* agent_id by the raw Claude Code hex ID."""
        return _ar.find_agent_by_claude_id(self._connect, claude_agent_id)

    # ------------------------------------------------------------------ #
    # Agent Dependencies
    # ------------------------------------------------------------------ #

    def await_agent(self, agent_id: str, timeout_s: float = 60.0) -> dict[str, Any]:
        """Wait for an agent to deregister (complete its work).

        Polls the agent status until the agent is stopped or timeout expires.
        Returns the final state.
        """
        import time as _time
        start = _time.time()
        poll_interval = 2.0
        while _time.time() - start < timeout_s:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT status FROM agents WHERE agent_id = ?", (agent_id,)
                ).fetchone()
                if row is None or row["status"] == "stopped":
                    return {
                        "awaited": True,
                        "agent_id": agent_id,
                        "status": row["status"] if row else "not_found",
                        "waited_s": _time.time() - start,
                    }
            remaining = timeout_s - (_time.time() - start)
            if remaining <= 0:
                break
            _time.sleep(min(poll_interval, remaining))
        return {
            "awaited": False,
            "agent_id": agent_id,
            "status": "timeout",
            "timeout_s": timeout_s,
        }

    # ------------------------------------------------------------------ #
    # Messaging
    # ------------------------------------------------------------------ #

    def send_message(
        self,
        from_agent_id: str,
        to_agent_id: str,
        message_type: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a message to another agent."""
        return _msg.send_message(self._connect, from_agent_id, to_agent_id, message_type, payload)

    def get_messages(
        self, agent_id: str, unread_only: bool = False, limit: int = 50,
    ) -> dict[str, Any]:
        """Get messages for an agent."""
        messages = _msg.get_messages(self._connect, agent_id, unread_only, limit)
        return {"messages": messages, "count": len(messages)}

    def mark_messages_read(
        self, agent_id: str, message_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """Mark messages as read."""
        return _msg.mark_messages_read(self._connect, agent_id, message_ids)

    # ------------------------------------------------------------------ #
    # Task Registry
    # ------------------------------------------------------------------ #

    def create_task(
        self, task_id: str, parent_agent_id: str, description: str,
        depends_on: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a new task in the shared registry."""
        return _tasks.create_task(
            self._connect, task_id, parent_agent_id, description, depends_on,
        )

    def assign_task(self, task_id: str, assigned_agent_id: str) -> dict[str, Any]:
        """Assign a task to an agent."""
        return _tasks.assign_task(self._connect, task_id, assigned_agent_id)

    def update_task_status(
        self, task_id: str, status: str,
        summary: str | None = None, blocked_by: str | None = None,
    ) -> dict[str, Any]:
        """Update task status, optionally with a completion summary or blocker."""
        return _tasks.update_task_status(
            self._connect, task_id, status, summary, blocked_by,
        )

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        """Get a single task by ID."""
        return _tasks.get_task(self._connect, task_id)

    def get_child_tasks(self, parent_agent_id: str) -> dict[str, Any]:
        """Get all tasks created by a given agent."""
        tasks = _tasks.get_child_tasks(self._connect, parent_agent_id)
        return {"tasks": tasks, "count": len(tasks)}

    def get_tasks_by_agent(self, assigned_agent_id: str) -> dict[str, Any]:
        """Get all tasks assigned to a given agent."""
        tasks = _tasks.get_tasks_by_agent(self._connect, assigned_agent_id)
        return {"tasks": tasks, "count": len(tasks)}

    def get_all_tasks(self) -> dict[str, Any]:
        """Get all tasks in the registry."""
        tasks = _tasks.get_all_tasks(self._connect)
        return {"tasks": tasks, "count": len(tasks)}

    # ------------------------------------------------------------------ #
    # Work Intent Board
    # ------------------------------------------------------------------ #

    def declare_work_intent(
        self, agent_id: str, document_path: str, intent: str, ttl: float = 60.0,
    ) -> dict[str, Any]:
        """Declare intent to work on a file before acquiring a lock."""
        return _wi.upsert_intent(self._connect, agent_id, document_path, intent, ttl)

    def get_work_intents(self, agent_id: str | None = None) -> dict[str, Any]:
        """Get all live work intents, optionally filtered by agent."""
        intents = _wi.get_live_intents(self._connect, agent_id)
        return {"intents": intents, "count": len(intents)}

    def clear_work_intent(self, agent_id: str) -> dict[str, Any]:
        """Clear an agent's declared work intent."""
        return _wi.clear_intent(self._connect, agent_id)

    # ------------------------------------------------------------------ #
    # Handoffs
    # ------------------------------------------------------------------ #

    def acknowledge_handoff(self, handoff_id: int, agent_id: str) -> dict[str, Any]:
        """Acknowledge receipt of a handoff."""
        return _handoffs.acknowledge_handoff(self._connect, handoff_id, agent_id)

    def complete_handoff(self, handoff_id: int) -> dict[str, Any]:
        """Mark a handoff as completed."""
        return _handoffs.complete_handoff(self._connect, handoff_id)

    def cancel_handoff(self, handoff_id: int) -> dict[str, Any]:
        """Cancel a handoff."""
        return _handoffs.cancel_handoff(self._connect, handoff_id)

    def get_handoffs(
        self, status: str | None = None, from_agent_id: str | None = None, limit: int = 50,
    ) -> dict[str, Any]:
        """Get handoffs with optional filtering."""
        handoffs = _handoffs.get_handoffs(self._connect, status, from_agent_id, limit)
        return {"handoffs": handoffs, "count": len(handoffs)}

    # ------------------------------------------------------------------ #
    # Cross-Agent Dependencies
    # ------------------------------------------------------------------ #

    def declare_dependency(
        self, dependent_agent_id: str, depends_on_agent_id: str,
        depends_on_task_id: str | None = None, condition: str = "task_completed",
    ) -> dict[str, Any]:
        """Declare that dependent_agent needs depends_on_agent to finish first."""
        return _deps.declare_dependency(
            self._connect, dependent_agent_id, depends_on_agent_id,
            depends_on_task_id, condition,
        )

    def check_dependencies(self, agent_id: str) -> dict[str, Any]:
        """Check unsatisfied dependencies for an agent."""
        unsatisfied = _deps.check_dependencies(self._connect, agent_id)
        return {"agent_id": agent_id, "blocked": len(unsatisfied) > 0,
                "unsatisfied": unsatisfied}

    def satisfy_dependency(self, dep_id: int) -> dict[str, Any]:
        """Mark a dependency as satisfied."""
        return _deps.satisfy_dependency(self._connect, dep_id)

    def get_blockers(self, agent_id: str) -> dict[str, Any]:
        """Alias for check_dependencies."""
        return self.check_dependencies(agent_id)

    def assert_can_start(self, agent_id: str) -> dict[str, Any]:
        """Structured check before starting work. Returns can_start bool."""
        result = self.check_dependencies(agent_id)
        if result["blocked"]:
            return {"can_start": False, "blockers": result["unsatisfied"]}
        return {"can_start": True}

    def get_all_dependencies(self, dependent_agent_id: str | None = None) -> dict[str, Any]:
        """Get all declared dependencies."""
        deps = _deps.get_all_dependencies(self._connect, dependent_agent_id)
        return {"dependencies": deps, "count": len(deps)}

    # ------------------------------------------------------------------ #
    # Change Awareness
    # ------------------------------------------------------------------ #

    def notify_change(
        self, document_path: str, change_type: str, agent_id: str,
    ) -> dict[str, Any]:
        norm_path = normalize_path(document_path, self._storage.project_root)
        return _cn.notify_change(
            self._connect, norm_path, change_type, agent_id, str(self._storage.project_root),
        )

    def claim_file_ownership(self, document_path: str, agent_id: str) -> None:
        """Assign file ownership on first write (INSERT OR IGNORE).

        Subsequent writes by other agents do not overwrite.  The
        ``scan_project`` tool can reassign based on graph roles later.
        """
        norm_path = normalize_path(document_path, self._storage.project_root)
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO file_ownership "
                "(document_path, assigned_agent_id, assigned_at, last_claimed_by) "
                "VALUES (?, ?, ?, ?)",
                (norm_path, agent_id, time.time(), agent_id),
            )

    def get_notifications(
        self, since: float | None = None, exclude_agent: str | None = None, limit: int = 100,
    ) -> dict[str, Any]:
        return _cn.get_notifications(self._connect, since, exclude_agent, limit)

    def prune_notifications(
        self, max_age_seconds: float | None = None, max_entries: int | None = None,
    ) -> dict[str, Any]:
        return _cn.prune_notifications(self._connect, max_age_seconds, max_entries)

    # ------------------------------------------------------------------ #
    # Conflict Audit
    # ------------------------------------------------------------------ #

    def get_conflicts(
        self, document_path: str | None = None, agent_id: str | None = None, limit: int = 20,
    ) -> dict[str, Any]:
        norm_path = normalize_path(document_path, self._storage.project_root) if document_path else None
        conflicts = _cl.query_conflicts(self._connect, norm_path, agent_id, limit)
        return {"conflicts": conflicts}

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #

    def status(self) -> dict[str, Any]:
        now = time.time()
        with self._connect() as conn:
            counts = conn.execute("""
                SELECT
                    (SELECT COUNT(*) FROM agents WHERE status = 'active') AS agent_count,
                    (SELECT COUNT(*) FROM agents WHERE status = 'active' AND last_heartbeat > ?) AS active_count,
                    (SELECT COUNT(*) FROM document_locks) AS lock_count,
                    (SELECT COUNT(*) FROM change_notifications) AS notif_count,
                    (SELECT COUNT(*) FROM lock_conflicts) AS conflict_count,
                    (SELECT COUNT(*) FROM file_ownership) AS file_owner_count
            """, (now - 600.0,)).fetchone()
        return {
            "registered_agents": counts["agent_count"],
            "active_agents": counts["active_count"],
            "active_locks": counts["lock_count"],
            "pending_notifications": counts["notif_count"],
            "recent_conflicts": counts["conflict_count"],
            "owned_files": counts["file_owner_count"],
            "graph_loaded": _g.get_graph() is not None,
            "tools": len(TOOL_DISPATCH),
        }

    def get_contention_hotspots(self, limit: int = 10) -> dict[str, Any]:
        """Rank files by lock contention frequency from the conflict log."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT document_path, COUNT(*) AS conflict_count, "
                "GROUP_CONCAT(DISTINCT agent_a) AS agents_a, "
                "GROUP_CONCAT(DISTINCT agent_b) AS agents_b "
                "FROM lock_conflicts GROUP BY document_path "
                "ORDER BY conflict_count DESC LIMIT ?",
                (limit,),
            ).fetchall()
        hotspots = []
        for row in rows:
            agents_a = set(row["agents_a"].split(",")) if row["agents_a"] else set()
            agents_b = set(row["agents_b"].split(",")) if row["agents_b"] else set()
            all_agents = sorted(agents_a | agents_b)
            hotspots.append({
                "document_path": row["document_path"],
                "conflict_count": row["conflict_count"],
                "agents_involved": all_agents,
            })
        return {"hotspots": hotspots, "total": len(hotspots)}

    # ------------------------------------------------------------------ #
    # Graph & Visibility
    # ------------------------------------------------------------------ #

    def load_coordination_spec(self, path: str | None = None) -> dict[str, Any]:
        target = Path(path) if path else None
        if path and target and not target.is_file():
            return {"loaded": False, "error": f"Coordination spec not found: {path}"}
        return _g.load_coordination_spec_from_disk(self._connect, self._storage.project_root, target)

    def validate_graph(self) -> dict[str, Any]:
        return _g.validate_graph_tool()

    def scan_project(
        self, worktree_root: str | None = None, extensions: list[str] | None = None,
    ) -> dict[str, Any]:
        if extensions is not None and not extensions:
            return {"scanned": 0, "owned": 0, "error": "extensions list cannot be empty"}
        graph = _g.get_graph()
        return _scan.scan_project_tool(self._connect, self._storage.project_root, worktree_root, extensions, graph)

    def get_agent_status(self, agent_id: str) -> dict[str, Any]:
        return _v.get_agent_status_tool(self._connect, agent_id, self.get_lineage)

    def get_agent_tree(self, agent_id: str | None = None) -> dict[str, Any]:
        return _v.get_agent_tree_tool(self._connect, agent_id)

    def get_file_agent_map(self, agent_id: str | None = None) -> dict[str, Any]:
        return _v.get_file_agent_map_tool(self._connect, agent_id)

    def update_agent_status(self, agent_id: str, current_task: str | None = None, scope: list[str] | None = None) -> dict[str, Any]:
        return _v.update_agent_status_tool(self._connect, agent_id, current_task, scope)

    def run_assessment(
        self,
        suite_path: str,
        format: str = "markdown",
        graph_agent_id: str | None = None,
    ) -> dict[str, Any]:
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

        Unlike ``run_assessment``, which requires a hand-authored suite
        JSON, this reads live hook-recorded state (agents, change
        notifications, lineage) and synthesizes a trace suite via
        ``build_suite_from_db``. Scores are persisted and returned.

        Args:
            format: ``"markdown"`` (default) returns {report, scores};
                ``"json"`` returns the raw scoring result.
            graph_agent_id: optional filter to restrict scoring to
                traces where at least one register event uses this role.
            scope: ``"project"`` (default) filters to the engine's
                worktree root; ``"all"`` includes every agent in the DB.
        """
        graph = _g.get_graph()
        if graph is None:
            return {"error": "No coordination graph loaded — "
                             "call load_coordination_spec first"}
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

    # ------------------------------------------------------------------ #
    # Context bundle helper
    # ------------------------------------------------------------------ #

    def _context_bundle(self, agent_id: str, parent_id: str | None = None) -> dict[str, Any]:
        return build_context_bundle(
            connect_fn=self._connect,
            agent_id=agent_id,
            parent_id=parent_id,
            project_root=str(self._storage.project_root) if self._storage.project_root else os.getcwd(),
            graph_getter=_g.get_graph,
            list_agents_fn=_ar.list_agents,
            default_port=self.DEFAULT_PORT,
            descendants_fn=lambda: _ar.get_descendants_status(self._connect, agent_id),
        )
