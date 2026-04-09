"""CoordinationEngine — core business logic for CoordinationHub v0.3.1.

Wires together the storage backend, agent_registry, lock_ops, conflict_log,
notifications, graph loading, and visibility helpers.
Project-root detection and path normalization live in paths.py.
Zero third-party dependencies.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from . import agent_registry as _ar
from . import conflict_log as _cl
from . import notifications as _cn
from . import lock_ops as _lo
from . import graphs as _g
from . import visibility as _v
from . import assessment as _assess
from ._storage import CoordinationStorage
from .context import build_context_bundle
from .dispatch import TOOL_DISPATCH
from .paths import detect_project_root, normalize_path


class CoordinationEngine:
    """Main coordinator. Manages agent identity, document locking, graph loading,
    file ownership tracking, and change notifications. Thread-safe via SQLite WAL.

    The storage layer (``CoordinationStorage``) is created on ``start()`` and
    owns the SQLite connection pool. Call ``start()`` before any tool calls and
    ``close()`` on shutdown.
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
    # Lifecycle — delegate to storage
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        self._storage.start()
        _g.load_coordination_spec_from_disk(self._connect, self._storage.project_root)

    def close(self) -> None:
        self._storage.close()

    def _connect(self) -> sqlite3.Connection:
        return self._storage._connect()

    # ------------------------------------------------------------------ #
    # Agent ID generation — delegate to storage
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
    ) -> dict[str, Any]:
        worktree = worktree_root or (
            str(self._storage.project_root) if self._storage.project_root else os.getcwd()
        )
        _ar.register_agent(self._connect, agent_id, worktree, parent_id)
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
                    _v.store_responsibilities(
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

    # ------------------------------------------------------------------ #
    # Document Locking
    # ------------------------------------------------------------------ #

    def acquire_lock(
        self, document_path: str, agent_id: str,
        lock_type: str = "exclusive", ttl: float = DEFAULT_TTL, force: bool = False,
    ) -> dict[str, Any]:
        now = time.time()
        norm_path = normalize_path(document_path, self._storage.project_root)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM document_locks WHERE document_path = ?", (norm_path,)
            ).fetchone()
            if row is not None:
                expired = now > row["locked_at"] + row["lock_ttl"]
                if row["locked_by"] == agent_id:
                    conn.execute(
                        "UPDATE document_locks SET locked_at = ?, lock_ttl = ?, lock_type = ? "
                        "WHERE document_path = ?",
                        (now, ttl, lock_type, norm_path),
                    )
                    return {"acquired": True, "document_path": norm_path, "locked_by": agent_id, "expires_at": now + ttl}
                if not expired and not force:
                    return {
                        "acquired": False, "locked_by": row["locked_by"],
                        "locked_at": row["locked_at"], "expires_at": row["locked_at"] + row["lock_ttl"],
                        "worktree": row["worktree_root"],
                    }
                _cl.record_conflict(self._connect, norm_path, row["locked_by"], agent_id,
                                    "lock_stolen", resolution="force_overwritten")
                conn.execute(
                    "UPDATE document_locks SET locked_by = ?, locked_at = ?, lock_ttl = ?, "
                    "lock_type = ?, worktree_root = ? WHERE document_path = ?",
                    (agent_id, now, ttl, lock_type, str(self._storage.project_root), norm_path),
                )
                return {"acquired": True, "document_path": norm_path, "locked_by": agent_id, "expires_at": now + ttl}
            try:
                conn.execute(
                    "INSERT INTO document_locks (document_path, locked_by, locked_at, lock_ttl, lock_type, worktree_root) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (norm_path, agent_id, now, ttl, lock_type, str(self._storage.project_root)),
                )
                return {"acquired": True, "document_path": norm_path, "locked_by": agent_id, "expires_at": now + ttl}
            except sqlite3.IntegrityError:
                # Another thread inserted first — re-read and treat as contested
                row = conn.execute(
                    "SELECT * FROM document_locks WHERE document_path = ?", (norm_path,)
                ).fetchone()
                if row and row["locked_by"] == agent_id:
                    return {"acquired": True, "document_path": norm_path, "locked_by": agent_id, "expires_at": now + ttl}
                if row:
                    return {
                        "acquired": False, "locked_by": row["locked_by"],
                        "locked_at": row["locked_at"], "expires_at": row["locked_at"] + row["lock_ttl"],
                        "worktree": row["worktree_root"],
                    }
                return {"acquired": False, "locked_by": "unknown"}

    def release_lock(self, document_path: str, agent_id: str) -> dict[str, Any]:
        norm_path = normalize_path(document_path, self._storage.project_root)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT locked_by FROM document_locks WHERE document_path = ?", (norm_path,)
            ).fetchone()
            if row is None:
                return {"released": False, "reason": "not_locked"}
            if row["locked_by"] != agent_id:
                return {"released": False, "reason": "not_owner"}
            conn.execute("DELETE FROM document_locks WHERE document_path = ?", (norm_path,))
            return {"released": True}

    def refresh_lock(self, document_path: str, agent_id: str, ttl: float = DEFAULT_TTL) -> dict[str, Any]:
        norm_path = normalize_path(document_path, self._storage.project_root)
        with self._connect() as conn:
            result = _lo.refresh_lock(conn, "document_locks", norm_path, agent_id, ttl, "not_locked")
        return result

    def get_lock_status(self, document_path: str) -> dict[str, Any]:
        norm_path = normalize_path(document_path, self._storage.project_root)
        now = time.time()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM document_locks WHERE document_path = ?", (norm_path,)
            ).fetchone()
            if row is None:
                return {"locked": False}
            if now > row["locked_at"] + row["lock_ttl"]:
                conn.execute("DELETE FROM document_locks WHERE document_path = ?", (norm_path,))
                return {"locked": False}
            return {
                "locked": True, "locked_by": row["locked_by"],
                "locked_at": row["locked_at"], "expires_at": row["locked_at"] + row["lock_ttl"],
                "worktree": row["worktree_root"],
            }

    def list_locks(self, agent_id: str | None = None) -> dict[str, Any]:
        """List all active (non-expired) locks, optionally filtered by agent."""
        now = time.time()
        with self._connect() as conn:
            if agent_id:
                rows = conn.execute(
                    "SELECT * FROM document_locks WHERE locked_by = ? AND locked_at + lock_ttl > ?",
                    (agent_id, now),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM document_locks WHERE locked_at + lock_ttl > ?",
                    (now,),
                ).fetchall()
        locks = []
        for row in rows:
            locks.append({
                "document_path": row["document_path"],
                "locked_by": row["locked_by"],
                "locked_at": row["locked_at"],
                "expires_at": row["locked_at"] + row["lock_ttl"],
                "lock_type": row["lock_type"],
                "worktree": row["worktree_root"],
            })
        return {"locks": locks, "count": len(locks)}

    def release_agent_locks(self, agent_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            result = _lo.release_agent_locks(conn, "document_locks", agent_id, delete=True)
        return result

    def reap_expired_locks(self) -> dict[str, Any]:
        with self._connect() as conn:
            result = _lo.reap_expired_locks(conn, "document_locks")
        return result

    def reap_stale_agents(self, timeout: float = 600.0) -> dict[str, Any]:
        result = _ar.reap_stale_agents(self._connect, timeout)
        with self._connect() as conn:
            stale_rows = conn.execute(
                "SELECT agent_id FROM agents WHERE status = 'stopped'"
            ).fetchall()
            if stale_rows:
                stale_ids = [r["agent_id"] for r in stale_rows]
                placeholders = ",".join("?" * len(stale_ids))
                cursor = conn.execute(
                    f"DELETE FROM document_locks WHERE locked_by IN ({placeholders})", stale_ids,
                )
                result["locks_released"] = cursor.rowcount
        return result

    # ------------------------------------------------------------------ #
    # Coordination Actions
    # ------------------------------------------------------------------ #

    def broadcast(
        self, agent_id: str, document_path: str | None = None, ttl: float = 30.0,
    ) -> dict[str, Any]:
        """Announce an intention to siblings and check for lock conflicts.

        ``broadcast`` does not store or forward messages — it only:
        1. Identifies live sibling agents (active within 60s).
        2. If ``document_path`` is provided, checks whether any live sibling holds
           a conflicting lock on that path.

        Returns which siblings are live and any lock conflicts. The calling agent is
        responsible for deciding what to do with that information.
        """
        siblings = _ar.get_siblings(self._connect, agent_id)
        acknowledged_by: list[str] = []
        conflicts: list[dict[str, Any]] = []
        now = time.time()
        for sib in siblings:
            if now - sib.get("last_heartbeat", 0) <= 60.0:
                acknowledged_by.append(sib["agent_id"])
        if document_path and acknowledged_by:
            norm_path = normalize_path(document_path, self._storage.project_root)
            placeholders = ",".join("?" * len(acknowledged_by))
            with self._connect() as conn:
                lock_rows = conn.execute(
                    f"SELECT locked_by FROM document_locks WHERE document_path = ? AND locked_by IN ({placeholders})",
                    [norm_path] + acknowledged_by,
                ).fetchall()
                for row in lock_rows:
                    if row["locked_by"] != agent_id:
                        conflicts.append({"document_path": document_path, "locked_by": row["locked_by"]})
        return {"acknowledged_by": acknowledged_by, "conflicts": conflicts}

    def wait_for_locks(
        self, document_paths: list[str], agent_id: str, timeout_s: float = 60.0,
    ) -> dict[str, Any]:
        start = time.time()
        released: list[str] = []
        timed_out: list[str] = []
        for path in document_paths:
            norm_path = normalize_path(path, self._storage.project_root)
            remaining = timeout_s - (time.time() - start)
            if remaining <= 0:
                timed_out.append(norm_path)
                continue
            poll_start = time.time()
            poll_interval = 2.0
            while time.time() - poll_start < remaining:
                status = self.get_lock_status(norm_path)
                if not status.get("locked", False) or status.get("locked_by") == agent_id:
                    released.append(norm_path)
                    break
                time.sleep(min(poll_interval, remaining - (time.time() - poll_start)))
            else:
                timed_out.append(norm_path)
        return {"released": released, "timed_out": timed_out}

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

    # ------------------------------------------------------------------ #
    # Graph & Visibility Tools
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
        return _v.scan_project_tool(self._connect, self._storage.project_root, worktree_root, extensions, graph)

    def get_agent_status(self, agent_id: str) -> dict[str, Any]:
        return _v.get_agent_status_tool(self._connect, agent_id, self.get_lineage)

    def get_agent_tree(self, agent_id: str | None = None) -> dict[str, Any]:
        return _v.get_agent_tree_tool(self._connect, agent_id)

    def get_file_agent_map(self, agent_id: str | None = None) -> dict[str, Any]:
        return _v.get_file_agent_map_tool(self._connect, agent_id)

    def update_agent_status(self, agent_id: str, current_task: str) -> dict[str, Any]:
        return _v.update_agent_status_tool(self._connect, agent_id, current_task)

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
        )
