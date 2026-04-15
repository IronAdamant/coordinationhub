"""Locking and coordination methods for CoordinationEngine.

Extracted from core.py to keep each module under 500 LOC.
Uses mixin pattern — CoordinationEngine inherits from this.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from . import conflict_log as _cl
from . import lock_ops as _lo
from . import agent_registry as _ar
from . import work_intent as _wi
from . import handoffs as _handoffs
from . import messages as _msg
from . import broadcasts as _bc
from . import notifications as _cn
from .paths import normalize_path


class LockingMixin:
    """Mixin providing document locking and coordination methods.

    Expects the host class to provide:
    - ``_connect() -> sqlite3.Connection``
    - ``_storage.project_root``
    - ``DEFAULT_TTL``
    """

    DEFAULT_TTL = 300.0

    def acquire_lock(
        self, document_path: str, agent_id: str,
        lock_type: str = "exclusive", ttl: float = DEFAULT_TTL, force: bool = False,
        region_start: int | None = None, region_end: int | None = None,
        retry: bool = False, max_retries: int = 5, backoff_ms: float = 100.0, timeout_ms: float = 5000.0,
    ) -> dict[str, Any]:
        """Acquire a lock with optional retry and exponential backoff.

        Args:
            retry: If True, retry on lock contention with exponential backoff.
            max_retries: Maximum number of retries (default 5).
            backoff_ms: Starting backoff in milliseconds (default 100ms).
            timeout_ms: Total timeout in milliseconds (default 5000ms).
        """
        norm_path = normalize_path(document_path, self._storage.project_root)
        worktree = str(self._storage.project_root)
        start_time = time.time()
        attempt = 0
        current_backoff_ms = backoff_ms

        while True:
            now = time.time()
            conn = self._connect()
            conn.execute("BEGIN IMMEDIATE")
            try:
                own = _lo.find_own_lock(conn, "document_locks", norm_path, agent_id, region_start, region_end)
                if own is not None:
                    conn.execute(
                        "UPDATE document_locks SET locked_at = ?, lock_ttl = ?, lock_type = ? WHERE id = ?",
                        (now, ttl, lock_type, own["id"]),
                    )
                    conn.execute("COMMIT")
                    self._lock_cache.refresh_lock(
                        norm_path, agent_id, now, ttl, lock_type,
                        region_start=region_start, region_end=region_end,
                    )
                    return {"acquired": True, "document_path": norm_path, "locked_by": agent_id,
                            "expires_at": now + ttl, "region_start": region_start, "region_end": region_end,
                            "attempts": attempt + 1}

                conflicts = _lo.find_conflicting_locks(
                    conn, "document_locks", norm_path, agent_id, lock_type, region_start, region_end,
                )
                if conflicts and not force:
                    conn.execute("COMMIT")
                    first = conflicts[0]
                    result = {
                        "acquired": False, "locked_by": first["locked_by"],
                        "locked_at": first["locked_at"], "expires_at": first["locked_at"] + first["lock_ttl"],
                        "worktree": first["worktree_root"],
                        "conflicts": [{"locked_by": c["locked_by"], "region_start": c["region_start"],
                                       "region_end": c["region_end"]} for c in conflicts],
                        "attempt": attempt + 1,
                    }

                    # Retry logic
                    if retry and attempt < max_retries:
                        elapsed_ms = (time.time() - start_time) * 1000
                        if elapsed_ms + current_backoff_ms <= timeout_ms:
                            time.sleep(current_backoff_ms / 1000.0)
                            current_backoff_ms *= 2  # Exponential backoff
                            attempt += 1
                            continue
                    return result
                if conflicts and force:
                    for c in conflicts:
                        _cl.record_conflict(self._connect, norm_path, c["locked_by"], agent_id,
                                            "lock_stolen", resolution="force_overwritten")
                        conn.execute("DELETE FROM document_locks WHERE id = ?", (c["id"],))

                conn.execute(
                    "INSERT INTO document_locks (document_path, locked_by, locked_at, lock_ttl, "
                    "lock_type, region_start, region_end, worktree_root) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (norm_path, agent_id, now, ttl, lock_type, region_start, region_end, worktree),
                )

                # Check scope BEFORE commit so ROLLBACK is valid if violated
                scope_result = self._check_scope_violation(conn, norm_path, agent_id)
                if scope_result is not None:
                    conn.execute("ROLLBACK")
                    return {
                        "acquired": False,
                        "error": "scope_violation",
                        "scope_violation": scope_result,
                        "attempts": attempt + 1,
                    }

                conn.execute("COMMIT")
                self._lock_cache.add_lock(
                    {
                        "document_path": norm_path,
                        "locked_by": agent_id,
                        "locked_at": now,
                        "lock_ttl": ttl,
                        "lock_type": lock_type,
                        "region_start": region_start,
                        "region_end": region_end,
                        "worktree_root": worktree,
                    }
                )
                _cn.notify_change(self._connect, norm_path, "locked", agent_id, worktree)
                self._publish_event(
                    "lock.acquired",
                    {
                        "document_path": norm_path,
                        "agent_id": agent_id,
                        "lock_type": lock_type,
                        "region_start": region_start,
                        "region_end": region_end,
                    },
                )

                # Check file_ownership for boundary crossing (warning only)
                ownership_warning = self._check_ownership_boundary(conn, norm_path, agent_id)
                result = {"acquired": True, "document_path": norm_path, "locked_by": agent_id,
                          "expires_at": now + ttl, "region_start": region_start, "region_end": region_end,
                          "attempts": attempt + 1}
                if ownership_warning:
                    result["ownership_warning"] = ownership_warning
                # Check work_intent for cooperative proximity warning (not a denial)
                proximity_warning = self._check_work_intent_conflict(conn, norm_path, agent_id)
                if proximity_warning:
                    result["proximity_warning"] = proximity_warning
                return result
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.OperationalError as e:
                    if "no transaction is active" not in str(e):
                        raise
                raise

    def _check_scope_violation(
        self, conn, norm_path: str, agent_id: str,
    ) -> dict[str, Any] | None:
        """Check if agent's declared scope covers the file path.

        If the agent has a scope and the path is outside it, returns a
        scope_violation dict to deny the lock. Returns None if scope is
        satisfied or agent has no scope.
        """
        row = conn.execute(
            "SELECT scope FROM agent_responsibilities WHERE agent_id = ?",
            (agent_id,),
        ).fetchone()
        if row is None or not row["scope"]:
            return None
        import json
        try:
            scope_paths = json.loads(row["scope"])
        except (json.JSONDecodeError, TypeError):
            return None
        if not scope_paths:
            return None
        # Check if norm_path starts with any scope prefix
        for scope_prefix in scope_paths:
            norm_scope = normalize_path(scope_prefix, self._storage.project_root)
            if norm_path.startswith(norm_scope) or norm_path == norm_scope.rstrip("/"):
                return None
        return {
            "declared_scope": scope_paths,
            "attempted_path": norm_path,
            "message": f"Agent {agent_id} declared scope {scope_paths} but attempted to lock {norm_path}",
        }

    def _check_ownership_boundary(
        self, conn, norm_path: str, agent_id: str,
    ) -> dict[str, Any] | None:
        """Check if agent is locking a file owned by another agent.

        Records a boundary_crossing conflict and notification (warning only).
        Returns warning dict or None.
        """
        row = conn.execute(
            "SELECT assigned_agent_id FROM file_ownership WHERE document_path = ?",
            (norm_path,),
        ).fetchone()
        if row is None or row["assigned_agent_id"] == agent_id:
            return None
        owner = row["assigned_agent_id"]
        _cl.record_conflict(
            self._connect, norm_path, owner, agent_id,
            "boundary_crossing", resolution="allowed",
            details={"message": f"Agent {agent_id} locked file owned by {owner}"},
        )
        from . import notifications as _cn
        _cn.notify_change(
            self._connect, norm_path, "boundary_crossing", agent_id,
            str(self._storage.project_root),
        )
        return {"owned_by": owner, "message": f"File is assigned to {owner} in file_ownership"}

    def _check_work_intent_conflict(
        self, conn, norm_path: str, agent_id: str,
    ) -> dict[str, Any] | None:
        """Check if another agent has a live intent for this file.

        Returns a proximity_warning dict (cooperative signal, not a denial).
        Only populated when lock acquisition would otherwise succeed.
        """
        conflicts = _wi.check_intent_conflict(self._connect, norm_path, agent_id)
        if not conflicts:
            return None
        return {
            "conflicting_agents": [{"agent_id": c["agent_id"], "intent": c["intent"]}
                                   for c in conflicts],
            "message": f"Agents {[c['agent_id'] for c in conflicts]} "
                       f"have declared intent to work on {norm_path}",
        }

    def release_lock(
        self, document_path: str, agent_id: str,
        region_start: int | None = None, region_end: int | None = None,
    ) -> dict[str, Any]:
        norm_path = normalize_path(document_path, self._storage.project_root)
        with self._connect() as conn:
            if region_start is not None:
                rows = conn.execute(
                    "SELECT id, locked_by FROM document_locks WHERE document_path = ? "
                    "AND region_start = ? AND region_end = ?",
                    (norm_path, region_start, region_end),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, locked_by FROM document_locks WHERE document_path = ? "
                    "AND region_start IS NULL",
                    (norm_path,),
                ).fetchall()
            if not rows:
                return {"released": False, "reason": "not_locked"}
            owned = [r for r in rows if r["locked_by"] == agent_id]
            if not owned:
                return {"released": False, "reason": "not_owner"}
            for r in owned:
                conn.execute("DELETE FROM document_locks WHERE id = ?", (r["id"],))
            _cn.notify_change(self._connect, norm_path, "unlocked", agent_id, str(self._storage.project_root))
            self._lock_cache.remove_lock(norm_path, agent_id, region_start, region_end)
            self._publish_event(
                "lock.released",
                {
                    "document_path": norm_path,
                    "agent_id": agent_id,
                    "region_start": region_start,
                    "region_end": region_end,
                },
            )
            return {"released": True, "count": len(owned)}

    def refresh_lock(
        self, document_path: str, agent_id: str, ttl: float = DEFAULT_TTL,
        region_start: int | None = None, region_end: int | None = None,
    ) -> dict[str, Any]:
        norm_path = normalize_path(document_path, self._storage.project_root)
        now = time.time()
        with self._connect() as conn:
            result = _lo.refresh_lock(
                conn, "document_locks", norm_path, agent_id, ttl, "not_locked",
                region_start=region_start, region_end=region_end,
            )
        if result.get("refreshed"):
            self._lock_cache.refresh_lock(
                norm_path, agent_id, now, ttl, result.get("lock_type", "exclusive"),
                region_start=region_start, region_end=region_end,
            )
        return result

    def get_lock_status(self, document_path: str) -> dict[str, Any]:
        norm_path = normalize_path(document_path, self._storage.project_root)
        now = time.time()
        return self._lock_cache.get_status(norm_path, now)

    def list_locks(self, agent_id: str | None = None) -> dict[str, Any]:
        """List all active (non-expired) locks, optionally filtered by agent."""
        now = time.time()
        locks = self._lock_cache.list_active(now, agent_id)
        return {"locks": locks, "count": len(locks)}

    def admin_locks(
        self,
        action: str,
        agent_id: str | None = None,
        grace_seconds: float = 0.0,
        timeout: float = 600.0,
    ) -> dict[str, Any]:
        """Administrative lock operations: release_by_agent, reap_expired, reap_stale."""
        if action == "release_by_agent":
            if not agent_id:
                return {"error": "agent_id is required for release_by_agent"}
            with self._connect() as conn:
                result = _lo.release_agent_locks(conn, "document_locks", agent_id, delete=True)
            removed = self._lock_cache.remove_by_agent(agent_id)
            if removed:
                self._publish_event(
                    "lock.released",
                    {"agent_id": agent_id, "bulk": True, "count": removed},
                )
            return result

        if action == "reap_expired":
            with self._connect() as conn:
                result = _lo.reap_expired_locks(
                    conn, "document_locks",
                    agent_grace_seconds=grace_seconds,
                )
            now = time.time()
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM document_locks WHERE locked_at + lock_ttl >= ?",
                    (now,),
                ).fetchall()
                self._lock_cache.warm([dict(r) for r in rows])
            return result

        if action == "reap_stale":
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
                    for sid in stale_ids:
                        self._lock_cache.remove_by_agent(sid)
            return result

        return {"error": f"Unknown action: {action}"}

    # Backward compatibility aliases
    def release_agent_locks(self, agent_id: str) -> dict[str, Any]:
        return self.admin_locks("release_by_agent", agent_id=agent_id)

    def reap_expired_locks(self, agent_grace_seconds: float = 0.0) -> dict[str, Any]:
        return self.admin_locks("reap_expired", grace_seconds=agent_grace_seconds)

    def reap_stale_agents(self, timeout: float = 600.0) -> dict[str, Any]:
        return self.admin_locks("reap_stale", timeout=timeout)

    def broadcast(
        self, agent_id: str, document_path: str | None = None, ttl: float = 30.0,
        handoff_targets: list[str] | None = None,
        require_ack: bool = False, message: str | None = None,
    ) -> dict[str, Any]:
        """Announce an intention to siblings, or perform a formal multi-recipient handoff.

        When handoff_targets is provided, acts as a formal handoff: records to the
        handoffs table and sends handoff messages to each target agent.

        When require_ack is True, creates a trackable broadcast record and sends
        acknowledgment request messages to each live sibling. Recipients must call
        acknowledge_broadcast to confirm receipt.
        """
        if handoff_targets:
            return self._handoff(agent_id, handoff_targets, document_path)

        siblings = _ar.get_siblings(self._connect, agent_id)
        now = time.time()
        live_siblings = [s for s in siblings if now - s.get("last_heartbeat", 0) <= ttl]

        if require_ack and live_siblings:
            result = _bc.record_broadcast(
                self._connect, agent_id, document_path, message, ttl, len(live_siblings),
            )
            broadcast_id = result["broadcast_id"]
            for sib in live_siblings:
                _msg.send_message(
                    self._connect, agent_id, sib["agent_id"], "broadcast_ack_request",
                    {"broadcast_id": broadcast_id, "document_path": document_path, "message": message},
                )
            self._publish_event(
                "broadcast.created",
                {
                    "broadcast_id": broadcast_id,
                    "agent_id": agent_id,
                    "document_path": document_path,
                    "pending_acks": [s["agent_id"] for s in live_siblings],
                },
            )
            return {
                "broadcast_id": broadcast_id,
                "acknowledged_by": [],
                "pending_acks": [s["agent_id"] for s in live_siblings],
                "conflicts": [],
            }

        acknowledged_by: list[str] = []
        conflicts: list[dict[str, Any]] = []
        sibling_ids = [s["agent_id"] for s in live_siblings]
        if document_path and sibling_ids:
            norm_path = normalize_path(document_path, self._storage.project_root)
            placeholders = ",".join("?" * len(sibling_ids))
            with self._connect() as conn:
                lock_rows = conn.execute(
                    f"SELECT locked_by FROM document_locks WHERE document_path = ? AND locked_by IN ({placeholders})",
                    [norm_path] + sibling_ids,
                ).fetchall()
                for row in lock_rows:
                    if row["locked_by"] != agent_id:
                        conflicts.append({"document_path": document_path, "locked_by": row["locked_by"]})
        return {"acknowledged_by": acknowledged_by, "conflicts": conflicts}

    def acknowledge_broadcast(
        self, broadcast_id: int, agent_id: str,
    ) -> dict[str, Any]:
        """Acknowledge receipt of a broadcast."""
        result = _bc.acknowledge_broadcast(self._connect, broadcast_id, agent_id)
        if result.get("acknowledged"):
            self._publish_event(
                "broadcast.ack",
                {"broadcast_id": broadcast_id, "agent_id": agent_id},
            )
        return result

    def get_broadcast_status(
        self, broadcast_id: int,
    ) -> dict[str, Any]:
        """Get the current acknowledgment status for a broadcast."""
        return _bc.get_broadcast_status(self._connect, broadcast_id)

    def wait_for_broadcast_acks(
        self, broadcast_id: int, timeout_s: float = 30.0,
    ) -> dict[str, Any]:
        """Wait until all expected acknowledgments are received or timeout expires.

        Uses the event bus for low-latency notification and falls back to the
        SQLite event journal for cross-process synchronization.
        Returns the final broadcast status, including acknowledged_by and pending_acks.
        """
        start = time.time()
        status = self.get_broadcast_status(broadcast_id)
        if not status.get("found"):
            return {"timed_out": True, "reason": "not_found"}

        if status.get("expires_at", 0) < time.time():
            return {
                "timed_out": True,
                "reason": "expired",
                "acknowledged_by": status.get("acknowledged_by", []),
            }

        expected = status.get("expected_count", 0)
        if expected <= 0:
            return {"timed_out": False, "acknowledged_by": status.get("acknowledged_by", [])}

        acked = set(status.get("acknowledged_by", []))
        while len(acked) < expected:
            elapsed = time.time() - start
            if elapsed >= timeout_s:
                break
            event = self._hybrid_wait(
                ["broadcast.ack"],
                filter_fn=lambda e: e.get("broadcast_id") == broadcast_id,
                timeout=timeout_s - elapsed,
            )
            if event is None:
                break
            acked.add(event.get("agent_id"))

        return {
            "timed_out": len(acked) < expected,
            "acknowledged_by": list(acked),
        }

    def _handoff(
        self, agent_id: str, to_agents: list[str],
        document_path: str | None = None, handoff_type: str = "scope_transfer",
    ) -> dict[str, Any]:
        """Formal multi-recipient handoff."""
        result = _handoffs.record_handoff(
            self._connect, agent_id, to_agents, document_path, handoff_type,
        )
        handoff_id = result["handoff_id"]
        # Send handoff messages to each target agent
        for target in to_agents:
            _msg.send_message(
                self._connect, agent_id, target, "handoff",
                {"handoff_id": handoff_id, "document_path": document_path,
                 "handoff_type": handoff_type},
            )
        self._publish_event(
            "handoff.created",
            {"handoff_id": handoff_id, "from_agent_id": agent_id,
             "to_agents": to_agents, "document_path": document_path,
             "handoff_type": handoff_type},
        )
        return {
            "handoff_id": handoff_id, "to_agents": to_agents,
            "document_path": document_path, "handoff_type": handoff_type,
        }

    def wait_for_locks(
        self, document_paths: list[str], agent_id: str, timeout_s: float = 60.0,
    ) -> dict[str, Any]:
        start = time.time()
        paths_set = {normalize_path(p, self._storage.project_root) for p in document_paths}
        released: list[str] = []

        # Fast-path: check which paths are already unlocked
        for path in list(paths_set):
            status = self.get_lock_status(path)
            if not status.get("locked", False) or status.get("locked_by") == agent_id:
                released.append(path)
                paths_set.remove(path)

        while paths_set:
            elapsed = time.time() - start
            if elapsed >= timeout_s:
                break
            event = self._hybrid_wait(
                ["lock.released"],
                filter_fn=lambda e: e.get("document_path") in paths_set,
                timeout=timeout_s - elapsed,
            )
            if event is None:
                break
            released.append(event["document_path"])
            paths_set.remove(event["document_path"])

        return {"released": released, "timed_out": list(paths_set)}
