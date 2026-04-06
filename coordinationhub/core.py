"""CoordinationEngine — core business logic for CoordinationHub.

Wires together db, agent_registry, lock_ops, conflict_log, notifications.
Project-root detection, path normalization, and all MCP tools.
Zero third-party dependencies.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from . import agent_registry as _ar
from . import conflict_log as _cl
from . import db as _db
from . import notifications as _cn
from . import lock_ops as _lo
from .schemas import TOOL_DISPATCH
from .schemas import TOOL_DISPATCH


# ------------------------------------------------------------------ #
# Project root detection (same logic as Stele/Chisel)
# ------------------------------------------------------------------ #

def _detect_project_root(cwd: str | Path | None = None) -> Path | None:
    """Walk up from CWD looking for .git (file or dir). Returns None if not in a repo."""
    if cwd is None:
        cwd = Path.cwd()
    else:
        cwd = Path(cwd).resolve()

    path = cwd
    for _ in range(256):  # safety limit
        if (path / ".git").exists():
            return path
        parent = path.parent
        if parent == path:
            break
        path = parent
    return None


def _normalize_path(path: str, project_root: Path | None) -> str:
    """Normalize a document path to project-relative. External paths stay absolute."""
    p = Path(path).resolve()
    norm = p.as_posix().replace("\\", "/")

    if project_root is not None:
        try:
            rel = p.relative_to(project_root.resolve())
            return rel.as_posix().replace("\\", "/")
        except ValueError:
            pass

    return norm


# ------------------------------------------------------------------ #
# CoordinationEngine
# ------------------------------------------------------------------ #

class CoordinationEngine:
    """The main coordinator.

    Manages agent identity, document locking, conflict logging,
    and change notifications. Thread-safe via SQLite WAL mode.
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
        self._namespace = namespace
        self._project_root = project_root or _detect_project_root()
        self._storage_dir = self._resolve_storage_dir(storage_dir)
        self._pool: _db.ConnectionPool | None = None

    def _resolve_storage_dir(self, storage_dir: Path | str | None) -> Path:
        """Resolve storage directory, defaulting to <project_root>/.coordinationhub/."""
        if storage_dir is not None:
            return Path(storage_dir).resolve()
        if self._project_root is not None:
            base = self._project_root / ".coordinationhub"
            base.mkdir(parents=True, exist_ok=True)
            return base
        # Fallback to ~/.coordinationhub/
        return Path.home() / ".coordinationhub"

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """Initialize the database and connection pool."""
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        db_path = self._storage_dir / "coordination.db"
        self._pool = _db.ConnectionPool(db_path)
        _db.set_pool(self._pool)
        with self._pool.connect() as conn:
            _db.init_schema(conn)
            _ar.init_agents_table(self._pool.connect)
            _cn.init_notifications_table(self._pool.connect)

    def close(self) -> None:
        """Shut down the engine."""
        if self._pool is not None:
            with self._pool.connect() as conn:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            _db.clear_pool()
            self._pool = None

    def _connect(self) -> sqlite3.Connection:
        """Return a connection from the pool."""
        if self._pool is None:
            raise RuntimeError("Engine not started. Call start() first.")
        return self._pool.connect()

    # ------------------------------------------------------------------ #
    # Agent ID generation
    # ------------------------------------------------------------------ #

    def _next_seq(self, prefix: str, conn: sqlite3.Connection) -> int:
        """Find the next sequence number for a given agent ID prefix.

        Handles both root-agent prefixes ('hub.PID') and child prefixes
        ('parent.child.'). The trailing-dot normalization prevents double-dot
        LIKE patterns when the caller passes 'parent.child.' with a trailing dot.
        """
        base = prefix.rstrip(".")
        row = conn.execute(
            f"SELECT agent_id FROM agents WHERE agent_id LIKE ? || '.%' ORDER BY agent_id DESC LIMIT 1",
            (base,),
        ).fetchone()
        if row:
            return int(row["agent_id"].rsplit(".", 1)[-1]) + 1
        return 0

    def generate_agent_id(self, parent_id: str | None = None) -> str:
        """Generate a new unique agent ID.

        If parent_id is None: root agent under this namespace + PID.
        If parent_id is set: child under parent.
        """
        pid = os.getpid()
        prefix = f"{self._namespace}.{pid}"
        with self._connect() as conn:
            if parent_id is None:
                seq = self._next_seq(prefix, conn)
                return f"{prefix}.{seq}"
            else:
                row = conn.execute(
                    "SELECT agent_id FROM agents WHERE agent_id = ?",
                    (parent_id,),
                ).fetchone()
                if not row:
                    raise ValueError(f"Parent agent not found: {parent_id}")
                child_prefix = f"{parent_id}."
                seq = self._next_seq(child_prefix, conn)
                return f"{parent_id}.{seq}"

    # ------------------------------------------------------------------ #
    # Identity & Registration
    # ------------------------------------------------------------------ #

    def register_agent(
        self,
        agent_id: str,
        parent_id: str | None = None,
        worktree_root: str | None = None,
    ) -> dict[str, Any]:
        """Register agent and return coordination context bundle."""
        worktree = worktree_root or str(self._project_root) if self._project_root else os.getcwd()
        _ar.register_agent(self._connect, agent_id, worktree, parent_id)

        # Record lineage
        if parent_id is not None:
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO lineage (parent_id, child_id, spawned_at) VALUES (?, ?, ?)",
                    (parent_id, agent_id, time.time()),
                )

        return self._context_bundle(agent_id, parent_id)

    def heartbeat(self, agent_id: str) -> dict[str, Any]:
        """Update heartbeat timestamp for a registered agent.

        Call at least every HEARTBEAT_INTERVAL seconds to stay active.
        Lock reaping is handled separately by reap_expired_locks().
        """
        updated = _ar.heartbeat(self._connect, agent_id)
        return {
            "updated": updated.get("updated", False),
            "next_heartbeat_in": self.HEARTBEAT_INTERVAL,
        }

    def deregister_agent(self, agent_id: str) -> dict[str, Any]:
        """Deregister agent, orphan children, release locks."""
        result = _ar.deregister_agent(self._connect, agent_id)

        # Release locks
        with self._connect() as conn:
            lock_result = _lo.release_agent_locks(conn, "document_locks", agent_id, delete=True)

        result["locks_released"] = lock_result.get("released", 0)
        return result

    def list_agents(
        self,
        active_only: bool = True,
        stale_timeout: float = 600.0,
    ) -> dict[str, Any]:
        """List all registered agents."""
        agents = _ar.list_agents(self._connect, active_only, stale_timeout)
        return {"agents": agents}

    def get_lineage(self, agent_id: str) -> dict[str, Any]:
        """Get ancestors and descendants of an agent."""
        return _ar.get_lineage(self._connect, agent_id)

    def get_siblings(self, agent_id: str) -> dict[str, Any]:
        """Get agents that share the same parent."""
        siblings = _ar.get_siblings(self._connect, agent_id)
        return {"siblings": siblings}

    # ------------------------------------------------------------------ #
    # Document Locking
    # ------------------------------------------------------------------ #

    def _try_refresh_lock(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
        norm_path: str,
        agent_id: str,
        ttl: float,
        lock_type: str,
        now: float,
    ) -> dict[str, Any] | None:
        """If row is owned by agent_id, refresh and return result. None otherwise."""
        if row["locked_by"] != agent_id:
            return None
        conn.execute(
            "UPDATE document_locks SET locked_at = ?, lock_ttl = ?, lock_type = ? "
            "WHERE document_path = ?",
            (now, ttl, lock_type, norm_path),
        )
        return {
            "acquired": True,
            "document_path": norm_path,
            "locked_by": agent_id,
            "expires_at": now + ttl,
        }

    def _handle_contested_lock(
        self,
        row: sqlite3.Row,
    ) -> dict[str, Any]:
        """Return contested result for a non-owned, non-expired lock."""
        return {
            "acquired": False,
            "locked_by": row["locked_by"],
            "locked_at": row["locked_at"],
            "expires_at": row["locked_at"] + row["lock_ttl"],
            "worktree": row["worktree_root"],
        }

    def _steal_lock(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
        norm_path: str,
        agent_id: str,
        ttl: float,
        lock_type: str,
        now: float,
    ) -> dict[str, Any]:
        """Record conflict and take over an existing lock."""
        _cl.record_conflict(
            self._connect,
            norm_path,
            row["locked_by"],
            agent_id,
            "lock_stolen",
            resolution="force_overwritten",
        )
        conn.execute(
            "UPDATE document_locks SET locked_by = ?, locked_at = ?, lock_ttl = ?, "
            "lock_type = ?, worktree_root = ? WHERE document_path = ?",
            (agent_id, now, ttl, lock_type, str(self._project_root), norm_path),
        )
        return {
            "acquired": True,
            "document_path": norm_path,
            "locked_by": agent_id,
            "expires_at": now + ttl,
        }

    def _insert_new_lock(
        self,
        conn: sqlite3.Connection,
        norm_path: str,
        agent_id: str,
        ttl: float,
        lock_type: str,
        now: float,
    ) -> dict[str, Any]:
        """Insert a brand-new lock."""
        conn.execute(
            """
            INSERT INTO document_locks
            (document_path, locked_by, locked_at, lock_ttl, lock_type, worktree_root)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (norm_path, agent_id, now, ttl, lock_type, str(self._project_root)),
        )
        return {
            "acquired": True,
            "document_path": norm_path,
            "locked_by": agent_id,
            "expires_at": now + ttl,
        }

    def acquire_lock(
        self,
        document_path: str,
        agent_id: str,
        lock_type: str = "exclusive",
        ttl: float = DEFAULT_TTL,
        force: bool = False,
    ) -> dict[str, Any]:
        """Acquire a lock on a document path."""
        now = time.time()
        norm_path = _normalize_path(document_path, self._project_root)

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM document_locks WHERE document_path = ?",
                (norm_path,),
            ).fetchone()

            if row is not None:
                locked_at = row["locked_at"]
                current_ttl = row["lock_ttl"]
                expired = now > locked_at + current_ttl

                # Case 1: we own it — refresh
                refreshed = self._try_refresh_lock(conn, row, norm_path, agent_id, ttl, lock_type, now)
                if refreshed is not None:
                    return refreshed

                # Case 2: contested (not ours, not expired, not forcing)
                if not expired and not force:
                    return self._handle_contested_lock(row)

                # Case 3: steal (force or expired)
                return self._steal_lock(conn, row, norm_path, agent_id, ttl, lock_type, now)

            # Case 4: new lock
            return self._insert_new_lock(conn, norm_path, agent_id, ttl, lock_type, now)

    def release_lock(self, document_path: str, agent_id: str) -> dict[str, Any]:
        """Release a held lock. Only the owner can release."""
        norm_path = _normalize_path(document_path, self._project_root)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT locked_by FROM document_locks WHERE document_path = ?",
                (norm_path,),
            ).fetchone()
            if row is None:
                return {"released": False, "reason": "not_locked"}
            if row["locked_by"] != agent_id:
                return {"released": False, "reason": "not_owner"}
            conn.execute(
                "DELETE FROM document_locks WHERE document_path = ?",
                (norm_path,),
            )
            return {"released": True}

    def refresh_lock(
        self,
        document_path: str,
        agent_id: str,
        ttl: float = DEFAULT_TTL,
    ) -> dict[str, Any]:
        """Extend a lock's TTL without releasing it."""
        norm_path = _normalize_path(document_path, self._project_root)
        with self._connect() as conn:
            result = _lo.refresh_lock(
                conn, "document_locks", norm_path, agent_id, ttl, "not_locked"
            )
        return result

    def get_lock_status(self, document_path: str) -> dict[str, Any]:
        """Check if a document is currently locked."""
        norm_path = _normalize_path(document_path, self._project_root)
        now = time.time()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM document_locks WHERE document_path = ?",
                (norm_path,),
            ).fetchone()
            if row is None:
                return {"locked": False}

            locked_at = row["locked_at"]
            ttl = row["lock_ttl"]
            if now > locked_at + ttl:
                conn.execute(
                    "DELETE FROM document_locks WHERE document_path = ?",
                    (norm_path,),
                )
                return {"locked": False}

            return {
                "locked": True,
                "locked_by": row["locked_by"],
                "locked_at": locked_at,
                "expires_at": locked_at + ttl,
                "worktree": row["worktree_root"],
            }

    def release_agent_locks(self, agent_id: str) -> dict[str, Any]:
        """Release all locks held by a given agent."""
        with self._connect() as conn:
            result = _lo.release_agent_locks(conn, "document_locks", agent_id, delete=True)
        return result

    def reap_expired_locks(self) -> dict[str, Any]:
        """Clear all expired locks."""
        with self._connect() as conn:
            result = _lo.reap_expired_locks(conn, "document_locks")
        return result

    def reap_stale_agents(self, timeout: float = 600.0) -> dict[str, Any]:
        """Mark stale agents as stopped and release their locks in a single batch."""
        result = _ar.reap_stale_agents(self._connect, timeout)

        with self._connect() as conn:
            stale_rows = conn.execute(
                "SELECT agent_id FROM agents WHERE status = 'stopped'"
            ).fetchall()
            if stale_rows:
                stale_ids = [r["agent_id"] for r in stale_rows]
                placeholders = ",".join("?" * len(stale_ids))
                cursor = conn.execute(
                    f"DELETE FROM document_locks WHERE locked_by IN ({placeholders})",
                    stale_ids,
                )
                result["locks_released"] = cursor.rowcount

        return result

    # ------------------------------------------------------------------ #
    # Coordination Actions
    # ------------------------------------------------------------------ #

    def broadcast(
        self,
        agent_id: str,
        document_path: str | None = None,
        ttl: float = 30.0,
    ) -> dict[str, Any]:
        """Announce an intention to siblings before taking an action.

        Returns which siblings are live and any lock conflicts on the given path.
        Note: `message` and `action` parameters were previously accepted but never
        stored or forwarded — they are removed to avoid confusion.
        """
        siblings = _ar.get_siblings(self._connect, agent_id)
        acknowledged_by: list[str] = []
        conflicts: list[dict[str, Any]] = []
        now = time.time()

        for sib in siblings:
            last_hb = sib.get("last_heartbeat", 0)
            if now - last_hb > 60.0:
                continue  # Stale sibling
            acknowledged_by.append(sib["agent_id"])

        if document_path and acknowledged_by:
            norm_path = _normalize_path(document_path, self._project_root)
            # Batch-query all acknowledged siblings' locks in one SQL call
            placeholders = ",".join("?" * len(acknowledged_by))
            with self._connect() as conn:
                lock_rows = conn.execute(
                    f"SELECT locked_by FROM document_locks "
                    f"WHERE document_path = ? AND locked_by IN ({placeholders})",
                    [norm_path] + acknowledged_by,
                ).fetchall()
                for row in lock_rows:
                    if row["locked_by"] != agent_id:
                        conflicts.append({
                            "document_path": document_path,
                            "locked_by": row["locked_by"],
                        })

        return {
            "acknowledged_by": acknowledged_by,
            "conflicts": conflicts,
        }

    def wait_for_locks(
        self,
        document_paths: list[str],
        agent_id: str,
        timeout_s: float = 60.0,
    ) -> dict[str, Any]:
        """Poll until specified locks are released or timeout."""
        start = time.time()
        released: list[str] = []
        timed_out: list[str] = []

        for path in document_paths:
            norm_path = _normalize_path(path, self._project_root)
            remaining = timeout_s - (time.time() - start)
            if remaining <= 0:
                timed_out.append(norm_path)
                continue

            poll_start = time.time()
            poll_interval = 2.0
            while time.time() - poll_start < remaining:
                status = self.get_lock_status(norm_path)
                if not status.get("locked", False):
                    released.append(norm_path)
                    break
                if status.get("locked_by") == agent_id:
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
        self,
        document_path: str,
        change_type: str,
        agent_id: str,
    ) -> dict[str, Any]:
        """Record a change event for other agents to poll."""
        norm_path = _normalize_path(document_path, self._project_root)
        return _cn.notify_change(
            self._connect,
            norm_path,
            change_type,
            agent_id,
            str(self._project_root),
        )

    def get_notifications(
        self,
        since: float | None = None,
        exclude_agent: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Poll for changes since a timestamp."""
        return _cn.get_notifications(self._connect, since, exclude_agent, limit)

    def prune_notifications(
        self,
        max_age_seconds: float | None = None,
        max_entries: int | None = None,
    ) -> dict[str, Any]:
        """Clean up old notifications."""
        return _cn.prune_notifications(
            self._connect, max_age_seconds, max_entries
        )

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
        norm_path = None
        if document_path:
            norm_path = _normalize_path(document_path, self._project_root)
        conflicts = _cl.query_conflicts(self._connect, norm_path, agent_id, limit)
        return {"conflicts": conflicts}

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #

    def status(self) -> dict[str, Any]:
        """Get a summary of the coordination system state."""
        now = time.time()
        with self._connect() as conn:
            # Single query for all table counts
            counts = conn.execute("""
                SELECT
                    (SELECT COUNT(*) FROM agents WHERE status = 'active') AS agent_count,
                    (SELECT COUNT(*) FROM agents WHERE status = 'active' AND last_heartbeat > ?) AS active_count,
                    (SELECT COUNT(*) FROM document_locks) AS lock_count,
                    (SELECT COUNT(*) FROM change_notifications) AS notif_count,
                    (SELECT COUNT(*) FROM lock_conflicts) AS conflict_count
            """, (now - 600.0,)).fetchone()

        return {
            "registered_agents": counts["agent_count"],
            "active_agents": counts["active_count"],
            "active_locks": counts["lock_count"],
            "pending_notifications": counts["notif_count"],
            "recent_conflicts": counts["conflict_count"],
            "tools": len(TOOL_DISPATCH),
        }

    # ------------------------------------------------------------------ #
    # Context bundle helper
    # ------------------------------------------------------------------ #

    def _context_bundle(self, agent_id: str, parent_id: str | None = None) -> dict[str, Any]:
        """Build the coordination context bundle for an agent.

        parent_id is stored in the bundle so sub-agents have correct lineage
        without needing a second database lookup.
        """
        agents = _ar.list_agents(self._connect, active_only=True, stale_timeout=600.0)

        with self._connect() as conn:
            locks = conn.execute(
                "SELECT document_path, locked_by, locked_at, lock_ttl FROM document_locks"
            ).fetchall()
            active_locks = []
            now = time.time()
            for row in locks:
                if now <= row["locked_at"] + row["lock_ttl"]:
                    active_locks.append({
                        "document_path": row["document_path"],
                        "locked_by": row["locked_by"],
                        "expires_at": row["locked_at"] + row["lock_ttl"],
                    })

            since = now - 300
            notifs = conn.execute(
                "SELECT document_path, change_type, agent_id, created_at "
                "FROM change_notifications WHERE created_at > ? ORDER BY created_at DESC LIMIT 20",
                (since,),
            ).fetchall()

        return {
            "agent_id": agent_id,
            "parent_id": parent_id,
            "worktree_root": str(self._project_root) if self._project_root else os.getcwd(),
            "registered_agents": [
                {"agent_id": a["agent_id"], "status": a["status"], "last_heartbeat": a["last_heartbeat"]}
                for a in agents
            ],
            "active_locks": active_locks,
            "pending_notifications": [dict(n) for n in notifs],
            "coordination_urls": {
                "coordinationhub": os.environ.get("COORDINATIONHUB_COORDINATION_URL", f"http://localhost:{self.DEFAULT_PORT}"),
                "stele": os.environ.get("COORDINATIONHUB_STELE_URL", "http://localhost:9876"),
                "chisel": os.environ.get("COORDINATIONHUB_CHISEL_URL", "http://localhost:8377"),
                "trammel": os.environ.get("COORDINATIONHUB_TRAMMEL_URL", "http://localhost:8737"),
            },
        }
