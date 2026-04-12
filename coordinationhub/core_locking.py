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
                            conn.close()
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
                conn.execute("COMMIT")

                # Check scope enforcement before committing the lock
                scope_result = self._check_scope_violation(conn, norm_path, agent_id)
                if scope_result is not None:
                    conn.execute("ROLLBACK")
                    return {
                        "acquired": False,
                        "error": "scope_violation",
                        "scope_violation": scope_result,
                        "attempts": attempt + 1,
                    }
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
                conn.execute("ROLLBACK")
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
            if norm_path.startswith(scope_prefix) or norm_path == scope_prefix.rstrip("/"):
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
            return {"released": True, "count": len(owned)}

    def refresh_lock(
        self, document_path: str, agent_id: str, ttl: float = DEFAULT_TTL,
        region_start: int | None = None, region_end: int | None = None,
    ) -> dict[str, Any]:
        norm_path = normalize_path(document_path, self._storage.project_root)
        with self._connect() as conn:
            result = _lo.refresh_lock(
                conn, "document_locks", norm_path, agent_id, ttl, "not_locked",
                region_start=region_start, region_end=region_end,
            )
        return result

    def get_lock_status(self, document_path: str) -> dict[str, Any]:
        norm_path = normalize_path(document_path, self._storage.project_root)
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM document_locks WHERE document_path = ? AND locked_at + lock_ttl < ?",
                (norm_path, now),
            )
            rows = conn.execute(
                "SELECT * FROM document_locks WHERE document_path = ?", (norm_path,)
            ).fetchall()
            if not rows:
                return {"locked": False}
            locks = []
            for row in rows:
                locks.append({
                    "locked_by": row["locked_by"], "locked_at": row["locked_at"],
                    "expires_at": row["locked_at"] + row["lock_ttl"],
                    "lock_type": row["lock_type"],
                    "region_start": row["region_start"], "region_end": row["region_end"],
                    "worktree": row["worktree_root"],
                })
            if len(locks) == 1:
                return {"locked": True, **locks[0]}
            return {"locked": True, "holders": locks}

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
                "region_start": row["region_start"],
                "region_end": row["region_end"],
                "worktree": row["worktree_root"],
            })
        return {"locks": locks, "count": len(locks)}

    def release_agent_locks(self, agent_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            result = _lo.release_agent_locks(conn, "document_locks", agent_id, delete=True)
        return result

    def reap_expired_locks(self, agent_grace_seconds: float = 0.0) -> dict[str, Any]:
        """Clear expired locks.

        The name is historical — this is more accurately "reconcile expired
        locks."  When *agent_grace_seconds* > 0 (the hook calls with 120.0),
        expired locks held by agents with a recent heartbeat are
        **implicitly refreshed** instead of deleted.  The TTL acts as a
        fallback for crashed agents, not a hard deadline for active ones.

        With ``agent_grace_seconds=0`` (the default, used by the MCP tool),
        behavior is strict: all expired locks are deleted.

        Returns ``{"reaped": N}`` where N is the count of locks actually
        deleted (not refreshed).
        """
        with self._connect() as conn:
            result = _lo.reap_expired_locks(
                conn, "document_locks",
                agent_grace_seconds=agent_grace_seconds,
            )
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

    def broadcast(
        self, agent_id: str, document_path: str | None = None, ttl: float = 30.0,
        handoff_targets: list[str] | None = None,
    ) -> dict[str, Any]:
        """Announce an intention to siblings, or perform a formal multi-recipient handoff.

        When handoff_targets is provided, acts as a formal handoff: records to the
        handoffs table and sends handoff messages to each target agent.
        """
        if handoff_targets:
            return self._handoff(agent_id, handoff_targets, document_path)
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
        return {
            "handoff_id": handoff_id, "to_agents": to_agents,
            "document_path": document_path, "handoff_type": handoff_type,
        }

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
