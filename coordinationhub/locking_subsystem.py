"""Locking subsystem — document lock acquire/release/refresh/list/admin.

T6.22 tenth step: extracted out of ``core_locking.LockingMixin`` into a
standalone class. Coupling audit confirmed LockingMixin had zero
cross-mixin method calls, zero ``_hybrid_wait`` calls, three
``_publish_event`` calls (``lock.acquired`` on acquire, ``lock.released``
on release and bulk release-by-agent), and stateful access to the
shared ``LockCache`` instance plus ``_storage.project_root`` for path
normalization.

**Shared ``_lock_cache`` ownership.** Unlike prior extractions where the
subsystem owned its own state, the :class:`LockCache` instance lives on
the engine (created in ``CoordinationEngine.__init__`` and warmed in
``start()``) and is passed into this subsystem as a shared reference.
The engine still exposes ``self._lock_cache`` because:

- Two remaining mixins on the MRO (:class:`BroadcastMixin` and
  :class:`IdentityMixin`) reach into locking via ``self.`` lookups
  (``self.get_lock_status`` from ``wait_for_locks``, and
  ``self.release_agent_locks`` from ``deregister_agent``). Those calls
  keep resolving via the engine's facade methods, which delegate here.
- ``CoordinationEngine.start()`` warms the cache directly from SQLite.
  Keeping the cache on the engine avoids a redundant indirection.

Path access follows the closure pattern from :class:`WorkIntent`,
:class:`Change`, and :class:`Visibility` (commits ``3d1bd48``,
``e0c21a8``, ``64c3ff4``): ``project_root_getter`` is a callable so a
replica produced by ``read_only_engine`` picks up its own storage root
without a rebind.

See commits ``1ee46c6`` (Spawner), ``3d1bd48`` (WorkIntent),
``b4a3e6b`` (Lease), ``d6c8796`` (Dependency), ``d9f84d3`` (Messaging),
``ded641d`` (Handoff), ``e0c21a8`` (Change), ``8182c7a`` (Task), and
``64c3ff4`` (Visibility) for the nine prior extractions in this series.

Delegates to: lock_ops (lock_ops.py), conflict_log (conflict_log.py),
agent_registry (agent_registry.py), work_intent (work_intent.py),
notifications (notifications.py), lock_cache (lock_cache.py — instance
owned by the engine).
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

from . import conflict_log as _cl
from . import lock_ops as _lo
from . import agent_registry as _ar
from . import work_intent as _wi
from . import notifications as _cn
from .lock_cache import LockCache
from .paths import normalize_path


class Locking:
    """Document locking — acquire/release/refresh/list/admin.

    Constructed by :class:`CoordinationEngine` and exposed as
    ``engine._locking``. The engine keeps facade methods for each
    public operation so the existing tool API is preserved and so the
    remaining mixins (:class:`BroadcastMixin`, :class:`IdentityMixin`)
    keep resolving ``self.get_lock_status`` / ``self.release_agent_locks``
    via the engine MRO.
    """

    # T6.23: renamed from DEFAULT_TTL to disambiguate from
    # LeaseMixin.DEFAULT_TTL (10s). Both attrs would otherwise land on
    # the same engine. DEFAULT_TTL kept as a back-compat alias.
    DEFAULT_LOCK_TTL = 300.0
    DEFAULT_TTL = DEFAULT_LOCK_TTL  # legacy alias

    def __init__(
        self,
        connect_fn: Callable[[], Any],
        publish_event_fn: Callable[[str, dict[str, Any]], None],
        lock_cache: LockCache,
        project_root_getter: Callable[[], Path | None],
    ) -> None:
        self._connect = connect_fn
        self._publish_event = publish_event_fn
        # Shared instance: the engine owns it (warmed in ``start()``)
        # and may be read by other call sites via ``self._lock_cache``.
        self._lock_cache = lock_cache
        self._project_root_getter = project_root_getter

    def acquire_lock(
        self, document_path: str, agent_id: str,
        lock_type: str = "exclusive", ttl: float = DEFAULT_LOCK_TTL, force: bool = False,
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
        project_root = self._project_root_getter()
        norm_path = normalize_path(document_path, project_root)
        worktree = str(project_root)
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
                    # T6.31: log every denied acquire (not just force-steals).
                    # Joins the outer BEGIN IMMEDIATE tx so the log write
                    # and the denial are atomic. Pre-fix get_conflicts only
                    # saw forced-steal events; normal contention was invisible.
                    for c in conflicts:
                        _lo.record_conflict(
                            conn, "lock_conflicts", norm_path,
                            c["locked_by"], agent_id,
                            "denied", resolution="rejected",
                        )
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

                    if retry and attempt < max_retries:
                        elapsed_ms = (time.time() - start_time) * 1000
                        # T3.26: apply a 0.5x..1.5x random jitter to the
                        # backoff so N contending agents don't retry in
                        # synchronized waves.
                        import random as _random
                        jittered_ms = current_backoff_ms * _random.uniform(0.5, 1.5)
                        if elapsed_ms + jittered_ms <= timeout_ms:
                            time.sleep(jittered_ms / 1000.0)
                            current_backoff_ms *= 2
                            attempt += 1
                            continue
                    return result
                if conflicts and force:
                    for c in conflicts:
                        # T1.1: use the lock_ops primitive directly so the conflict
                        # log write joins the outer BEGIN IMMEDIATE transaction
                        # instead of `conflict_log.record_conflict` opening a
                        # nested `with connect()` that would commit the outer
                        # transaction mid-flight (creating a race window where
                        # another force-stealer could start BEGIN IMMEDIATE
                        # between our SELECT and our DELETE, producing duplicate
                        # lock rows on the same path).
                        _lo.record_conflict(
                            conn, "lock_conflicts", norm_path,
                            c["locked_by"], agent_id,
                            "lock_stolen", resolution="force_overwritten",
                        )
                        conn.execute("DELETE FROM document_locks WHERE id = ?", (c["id"],))

                conn.execute(
                    "INSERT INTO document_locks (document_path, locked_by, locked_at, lock_ttl, "
                    "lock_type, region_start, region_end, worktree_root) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (norm_path, agent_id, now, ttl, lock_type, region_start, region_end, worktree),
                )

                # Scope check must happen before commit so ROLLBACK is valid if violated
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

                ownership_warning = self._check_ownership_boundary(conn, norm_path, agent_id)
                result = {"acquired": True, "document_path": norm_path, "locked_by": agent_id,
                          "expires_at": now + ttl, "region_start": region_start, "region_end": region_end,
                          "attempts": attempt + 1}
                if ownership_warning:
                    result["ownership_warning"] = ownership_warning
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
        project_root = self._project_root_getter()
        for scope_prefix in scope_paths:
            norm_scope = normalize_path(scope_prefix, project_root)
            # T3.23: compare path components rather than using raw
            # startswith. Pre-fix ``docs/security`` matched scope
            # ``docs/sec`` (character prefix, not path prefix). The
            # canonical boundary is a trailing slash — compare with it
            # appended so ``docs/sec`` only matches ``docs/sec`` and
            # ``docs/sec/*``.
            if norm_path == norm_scope.rstrip("/"):
                return None
            scope_with_slash = norm_scope.rstrip("/") + "/"
            if norm_path.startswith(scope_with_slash):
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
        _cn.notify_change(
            self._connect, norm_path, "boundary_crossing", agent_id,
            str(self._project_root_getter()),
        )
        return {"owned_by": owner, "message": f"File is assigned to {owner} in file_ownership"}

    def _check_work_intent_conflict(
        self, conn, norm_path: str, agent_id: str,
        requesting_intent: str | None = None,
    ) -> dict[str, Any] | None:
        """Check if another agent has a live intent for this file.

        Returns a proximity_warning dict (cooperative signal, not a denial).
        Only populated when lock acquisition would otherwise succeed.

        T1.16: passing ``requesting_intent`` lets read-only acquires filter
        out peer readers from the warning. Lock acquisition (the default
        call path) is always a write-class access so leaving it None
        preserves the strictest semantics.
        """
        conflicts = _wi.check_intent_conflict(
            self._connect, norm_path, agent_id,
            requesting_intent=requesting_intent,
        )
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
        norm_path = normalize_path(document_path, self._project_root_getter())
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
            _cn.notify_change(
                self._connect, norm_path, "unlocked", agent_id,
                str(self._project_root_getter()),
            )
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
        self, document_path: str, agent_id: str, ttl: float = DEFAULT_LOCK_TTL,
        region_start: int | None = None, region_end: int | None = None,
    ) -> dict[str, Any]:
        norm_path = normalize_path(document_path, self._project_root_getter())
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
        norm_path = normalize_path(document_path, self._project_root_getter())
        now = time.time()
        return self._lock_cache.get_status(norm_path, now)

    def list_locks(
        self,
        agent_id: str | None = None,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """List all active (non-expired) locks, optionally filtered by agent.

        T6.33: ``force_refresh=True`` re-warms the in-memory lock cache
        from SQLite under BEGIN IMMEDIATE before reading. Use when a
        caller suspects the cache has desynced from the DB — the regular
        acquire/release/reap paths keep the cache in sync, but any future
        regression or out-of-band DB write is recoverable without a
        process restart.
        """
        now = time.time()
        if force_refresh:
            conn = self._connect()
            conn.execute("BEGIN IMMEDIATE")
            try:
                rows = conn.execute(
                    "SELECT * FROM document_locks WHERE locked_at + lock_ttl >= ?",
                    (now,),
                ).fetchall()
                self._lock_cache.warm([dict(r) for r in rows])
                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.OperationalError as e:
                    if "no transaction is active" not in str(e):
                        raise
                raise
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
                result = _lo.release_agent_locks(conn, "document_locks", agent_id)
            removed = self._lock_cache.remove_by_agent(agent_id)
            if removed:
                self._publish_event(
                    "lock.released",
                    {"agent_id": agent_id, "bulk": True, "count": removed},
                )
            return result

        if action == "reap_expired":
            # T1.3: reap + warm must be atomic vs. concurrent acquire_lock.
            # If DELETE commits and the SELECT+warm happens in a separate
            # transaction, a concurrent acquire_lock can insert a new row
            # and call add_lock on the cache BEFORE warm(); then warm()
            # rebuilds from a snapshot that excludes the new row, wiping
            # it from the cache even though the DB holds it.
            #
            # Fix: wrap DELETE + SELECT + warm in one BEGIN IMMEDIATE so a
            # concurrent acquirer is serialised behind us. Because warm
            # runs while we still hold the writer lock, no add_lock can
            # interleave between our post-delete SELECT and warm().
            conn = self._connect()
            conn.execute("BEGIN IMMEDIATE")
            try:
                result = _lo.reap_expired_locks(
                    conn, "document_locks",
                    agent_grace_seconds=grace_seconds,
                )
                now = time.time()
                rows = conn.execute(
                    "SELECT * FROM document_locks WHERE locked_at + lock_ttl >= ?",
                    (now,),
                ).fetchall()
                self._lock_cache.warm([dict(r) for r in rows])
                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.OperationalError as e:
                    if "no transaction is active" not in str(e):
                        raise
                raise
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
