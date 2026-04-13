"""ChangeMixin — change notifications, file ownership, conflict audit, status.

Expects the host class to provide:
    self._connect()     — callable returning a sqlite3 connection
    self._storage        — CoordinationStorage instance (provides project_root)

Delegates to: notifications (notifications.py), conflict_log (conflict_log.py)
Direct SQL for status() and get_contention_hotspots().

Also provides claim_file_ownership and notify_change which use normalize_path
from paths.py.
"""

from __future__ import annotations

import time
from typing import Any

from . import notifications as _cn
from . import conflict_log as _cl
from .dispatch import TOOL_DISPATCH
from .paths import normalize_path


class ChangeMixin:
    """Change awareness, file ownership, notifications, conflict audit, and status."""

    DEFAULT_TTL = 300.0

    # ------------------------------------------------------------------ #
    # Change Awareness
    # ------------------------------------------------------------------ #

    def notify_change(
        self,
        document_path: str,
        change_type: str,
        agent_id: str,
    ) -> dict[str, Any]:
        """Record a change event for downstream agents to poll."""
        norm_path = normalize_path(document_path, self._storage.project_root)
        return _cn.notify_change(
            self._connect, norm_path, change_type, agent_id,
            str(self._storage.project_root),
        )

    def claim_file_ownership(self, document_path: str, agent_id: str) -> None:
        """Assign file ownership on first write (INSERT OR IGNORE).

        Subsequent writes by other agents do not overwrite.
        scan_project can reassign based on graph roles later.
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
        self,
        since: float | None = None,
        exclude_agent: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Get change notifications, optionally filtered by time and agent."""
        return _cn.get_notifications(self._connect, since, exclude_agent, limit)

    def prune_notifications(
        self,
        max_age_seconds: float | None = None,
        max_entries: int | None = None,
    ) -> dict[str, Any]:
        """Clean up old notifications."""
        return _cn.prune_notifications(self._connect, max_age_seconds, max_entries)

    # ------------------------------------------------------------------ #
    # Conflict Audit
    # ------------------------------------------------------------------ #

    def get_conflicts(
        self,
        document_path: str | None = None,
        agent_id: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Query the conflict log."""
        norm_path = (
            normalize_path(document_path, self._storage.project_root)
            if document_path else None
        )
        conflicts = _cl.query_conflicts(self._connect, norm_path, agent_id, limit)
        return {"conflicts": conflicts}

    def get_contention_hotspots(self, limit: int = 10) -> dict[str, Any]:
        """Rank files by lock contention frequency from the conflict log."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT document_path, COUNT(*) AS conflict_count,
                          GROUP_CONCAT(DISTINCT agent_a) AS agents_a,
                          GROUP_CONCAT(DISTINCT agent_b) AS agents_b
                   FROM lock_conflicts GROUP BY document_path
                   ORDER BY conflict_count DESC LIMIT ?""",
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
    # Status
    # ------------------------------------------------------------------ #

    def status(self) -> dict[str, Any]:
        """Return a snapshot of coordination system state."""
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
        from . import graphs as _g
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