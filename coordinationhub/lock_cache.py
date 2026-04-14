"""In-memory lock cache for CoordinationHub.

Mirrors the ``document_locks`` table in memory to eliminate SQLite reads
for ``get_lock_status`` and ``list_locks``. Writes still go to SQLite
for durability; the cache is updated after every successful mutation.

Zero external dependencies.
"""

from __future__ import annotations

import threading
from typing import Any


class LockCache:
    """Thread-safe in-memory cache of active document locks."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # key = normalized document_path, value = list of lock entries
        self._locks: dict[str, list[dict[str, Any]]] = {}

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _entry_key(self, entry: dict[str, Any]) -> str:
        return f"{entry['locked_by']}:{entry.get('region_start')}:{entry.get('region_end')}"

    # ------------------------------------------------------------------ #
    # Mutations
    # ------------------------------------------------------------------ #

    def warm(self, rows: list[dict[str, Any]]) -> None:
        """Populate the cache from a query result."""
        with self._lock:
            self._locks.clear()
            for row in rows:
                path = row["document_path"]
                entry = {
                    "locked_by": row["locked_by"],
                    "locked_at": row["locked_at"],
                    "lock_ttl": row["lock_ttl"],
                    "lock_type": row["lock_type"],
                    "region_start": row.get("region_start"),
                    "region_end": row.get("region_end"),
                    "worktree_root": row.get("worktree_root"),
                }
                self._locks.setdefault(path, []).append(entry)

    def add_lock(self, entry: dict[str, Any]) -> None:
        """Insert or update a lock entry."""
        path = entry["document_path"]
        with self._lock:
            entries = self._locks.setdefault(path, [])
            key = self._entry_key(entry)
            for e in entries:
                if self._entry_key(e) == key:
                    e.update(entry)
                    return
            entries.append(entry.copy())

    def remove_lock(
        self,
        document_path: str,
        agent_id: str,
        region_start: int | None = None,
        region_end: int | None = None,
    ) -> bool:
        """Remove a specific lock. Returns True if something was removed."""
        with self._lock:
            entries = self._locks.get(document_path, [])
            before = len(entries)
            entries = [
                e
                for e in entries
                if not (
                    e["locked_by"] == agent_id
                    and e.get("region_start") == region_start
                    and e.get("region_end") == region_end
                )
            ]
            if len(entries) < before:
                if entries:
                    self._locks[document_path] = entries
                else:
                    self._locks.pop(document_path, None)
                return True
            return False

    def remove_by_agent(self, agent_id: str) -> int:
        """Remove all locks held by an agent. Returns count removed."""
        removed = 0
        with self._lock:
            for path in list(self._locks):
                before = len(self._locks[path])
                self._locks[path] = [e for e in self._locks[path] if e["locked_by"] != agent_id]
                after = len(self._locks[path])
                removed += before - after
                if not self._locks[path]:
                    self._locks.pop(path, None)
        return removed

    def refresh_lock(
        self,
        document_path: str,
        agent_id: str,
        locked_at: float,
        lock_ttl: float,
        lock_type: str,
        region_start: int | None = None,
        region_end: int | None = None,
    ) -> bool:
        """Update the TTL/locked_at for an existing lock."""
        with self._lock:
            for e in self._locks.get(document_path, []):
                if (
                    e["locked_by"] == agent_id
                    and e.get("region_start") == region_start
                    and e.get("region_end") == region_end
                ):
                    e["locked_at"] = locked_at
                    e["lock_ttl"] = lock_ttl
                    e["lock_type"] = lock_type
                    return True
            return False

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #

    def get_status(self, document_path: str, now: float) -> dict[str, Any]:
        """Return lock status for a path, identical format to SQLite query."""
        with self._lock:
            entries = self._locks.get(document_path, [])
            active = [e for e in entries if e["locked_at"] + e["lock_ttl"] >= now]
            if not active:
                self._locks.pop(document_path, None)
                return {"locked": False}
            self._locks[document_path] = active
            locks = []
            for e in active:
                locks.append({
                    "locked_by": e["locked_by"],
                    "locked_at": e["locked_at"],
                    "expires_at": e["locked_at"] + e["lock_ttl"],
                    "lock_type": e["lock_type"],
                    "region_start": e.get("region_start"),
                    "region_end": e.get("region_end"),
                    "worktree": e.get("worktree_root"),
                })
            if len(locks) == 1:
                return {"locked": True, **locks[0]}
            return {"locked": True, "holders": locks}

    def list_active(self, now: float, agent_id: str | None = None) -> list[dict[str, Any]]:
        """Return all active locks, optionally filtered by agent."""
        locks: list[dict[str, Any]] = []
        with self._lock:
            for path, entries in list(self._locks.items()):
                active = [e for e in entries if e["locked_at"] + e["lock_ttl"] >= now]
                if not active:
                    self._locks.pop(path, None)
                    continue
                self._locks[path] = active
                for e in active:
                    if agent_id is None or e["locked_by"] == agent_id:
                        locks.append({
                            "document_path": path,
                            "locked_by": e["locked_by"],
                            "locked_at": e["locked_at"],
                            "expires_at": e["locked_at"] + e["lock_ttl"],
                            "lock_type": e["lock_type"],
                            "region_start": e.get("region_start"),
                            "region_end": e.get("region_end"),
                            "worktree": e.get("worktree_root"),
                        })
        return locks

    def list_conflicting_locks(
        self,
        document_path: str,
        agent_id: str,
        lock_type: str,
        region_start: int | None = None,
        region_end: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return conflicting locks for an acquire attempt.

        Mirrors the logic in ``lock_ops.find_conflicting_locks``:
        - exclusive locks conflict with any overlapping lock
        - shared locks conflict only with overlapping exclusive locks
        """
        conflicts: list[dict[str, Any]] = []
        with self._lock:
            for e in self._locks.get(document_path, []):
                if e["locked_by"] == agent_id:
                    continue
                # Region overlap check
                if region_start is not None or e.get("region_start") is not None:
                    es = e.get("region_start") or 0
                    ee = e.get("region_end") or float("inf")
                    rs = region_start or 0
                    re = region_end or float("inf")
                    if ee < rs or re < es:
                        continue
                # shared vs exclusive semantics
                if lock_type == "shared" and e["lock_type"] == "shared":
                    continue
                conflicts.append(e.copy())
        return conflicts
