"""Change subsystem — change notifications, file ownership, conflict audit, status.

T6.22 seventh step: extracted out of ``core_change.ChangeMixin`` into
a standalone class. Coupling audit confirmed ChangeMixin had zero
cross-mixin method calls; it relied on four pieces of engine state —
``_connect``, ``_publish_event``, ``_hybrid_wait``, and
``_storage.project_root`` (for ``normalize_path`` in ``notify_change``,
``claim_file_ownership``, and ``get_conflicts``) — which are now
injected as constructor dependencies. Same path-normalization shape as
:class:`WorkIntent` (commit ``3d1bd48``): ``project_root_getter`` is a
callable so a replica produced by ``read_only_engine`` picks up its
own storage root without a rebind. The three infra callables follow
the :class:`Spawner` / :class:`Messaging` / :class:`Handoff` pattern
(commits ``1ee46c6``, ``d9f84d3``, ``ded641d``). See commits
``b4a3e6b`` (Lease) and ``d6c8796`` (Dependency) for the other
extractions in this series. This continues breaking the god-object
inheritance chain on ``CoordinationEngine`` without changing
observable behaviour.

Delegates to: notifications (notifications.py), conflict_log (conflict_log.py).
Direct SQL for status() and get_contention_hotspots().
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from . import notifications as _cn
from . import conflict_log as _cl
from .dispatch import TOOL_DISPATCH
from .paths import normalize_path


class Change:
    """Change awareness, file ownership, notifications, conflict audit, and status.

    Constructed by :class:`CoordinationEngine` and exposed as
    ``engine._change``. The engine keeps facade methods for each
    public operation so the existing tool API is preserved.
    """

    DEFAULT_TTL = 300.0

    def __init__(
        self,
        connect_fn: Callable[[], Any],
        publish_event_fn: Callable[[str, dict[str, Any]], None],
        hybrid_wait_fn: Callable[..., dict[str, Any] | None],
        project_root_getter: Callable[[], Path | None],
    ) -> None:
        self._connect = connect_fn
        self._publish_event = publish_event_fn
        self._hybrid_wait = hybrid_wait_fn
        self._project_root_getter = project_root_getter

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
        project_root = self._project_root_getter()
        norm_path = normalize_path(document_path, project_root)
        result = _cn.notify_change(
            self._connect, norm_path, change_type, agent_id,
            str(project_root),
        )
        # T6.6: include notification_id in the event payload so long-poll
        # waiters can resume from a monotonic cursor instead of a
        # drift-prone timestamp. T7.20: created_at is now actually
        # populated by the primitive, so the ``or time.time()`` fallback
        # is no longer reachable; kept as a belt-and-braces default.
        self._publish_event(
            "notification.created",
            {
                "document_path": norm_path,
                "change_type": change_type,
                "agent_id": agent_id,
                "created_at": result.get("created_at") or time.time(),
                "notification_id": result.get("notification_id"),
            },
        )
        return result

    def claim_file_ownership(self, document_path: str, agent_id: str) -> None:
        """Assign file ownership on first write (INSERT OR IGNORE).

        Subsequent writes by other agents do not overwrite.
        scan_project can reassign based on graph roles later.
        """
        norm_path = normalize_path(document_path, self._project_root_getter())
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
        agent_id: str | None = None,
        timeout_s: float = 0.0,
        poll_interval_s: float = 2.0,
        prune_max_age_seconds: float | None = None,
        prune_max_entries: int | None = None,
    ) -> dict[str, Any]:
        """Get change notifications, optionally filtered by time and agent.

        If timeout_s > 0, long-polls for new notifications instead of returning
        immediately. This replaces the old wait_for_notifications tool.
        If prune_max_age_seconds or prune_max_entries is provided, also prunes
        old notifications before returning (replaces prune_notifications).
        """
        if prune_max_age_seconds is not None or prune_max_entries is not None:
            _cn.prune_notifications(self._connect, prune_max_age_seconds, prune_max_entries)
        if timeout_s > 0:
            event = self._hybrid_wait(
                ["notification.created"],
                filter_fn=lambda e: e.get("agent_id") != exclude_agent if exclude_agent else True,
                timeout=timeout_s,
            )
            if event is None:
                return {"notifications": [], "timed_out": True}
            # T6.6: use the monotonic notification_id cursor instead of
            # the pre-fix 1-second backwards timestamp window. Subtract
            # one so the event's own row is included in the fetch.
            notification_id = event.get("notification_id")
            if notification_id is not None:
                result = _cn.get_notifications(
                    self._connect,
                    since_id=notification_id - 1,
                    exclude_agent=exclude_agent,
                    limit=limit,
                )
            else:
                # Legacy event (pre-T6.6 publisher): fall back to the
                # timestamp path. Still better than a missed wake-up.
                since_val = event.get("created_at", time.time()) - 1.0
                result = _cn.get_notifications(
                    self._connect, since=since_val, exclude_agent=exclude_agent, limit=limit,
                )
            result["timed_out"] = False
            return result
        return _cn.get_notifications(self._connect, since, exclude_agent, limit)

    def prune_notifications(
        self,
        max_age_seconds: float | None = None,
        max_entries: int | None = None,
    ) -> dict[str, Any]:
        """Clean up old notifications."""
        return _cn.prune_notifications(self._connect, max_age_seconds, max_entries)

    def wait_for_notifications(
        self,
        agent_id: str,
        timeout_s: float = 30.0,
        poll_interval_s: float = 2.0,
        exclude_agent: str | None = None,
    ) -> dict[str, Any]:
        """Long-poll for new notifications until one arrives or timeout expires.

        Uses the event bus for low-latency notification, then fetches the
        matching notification batch from the database.
        Returns {"notifications": [...], "timed_out": False} when new notifications arrive,
        or {"notifications": [], "timed_out": True} if timeout expires.
        """
        start = time.time()
        event = self._hybrid_wait(
            ["notification.created"],
            filter_fn=lambda e: e.get("agent_id") != exclude_agent if exclude_agent else True,
            timeout=timeout_s,
        )
        if event is None:
            return {"notifications": [], "timed_out": True}

        # Fetch the batch including the triggering event and any that arrived just after.
        since = event.get("created_at", start) - 1.0
        result = _cn.get_notifications(
            self._connect, since=since, exclude_agent=exclude_agent, limit=100
        )
        result["timed_out"] = False
        return result

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
            normalize_path(document_path, self._project_root_getter())
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
        from .plugins.graph import graphs as _g
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
