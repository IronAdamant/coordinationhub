"""Storage backend for CoordinationHub — SQLite pool, path resolution, lifecycle.

Exposes a single ``CoordinationStorage`` class that owns the SQLite connection pool
and all schema initialisation. Both ``core.CoordinationEngine`` and CLI entry
points depend on this module — it has no internal dependencies on any other
coordinationhub sub-module.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

from . import db as _db


class CoordinationStorage:
    """Owns the SQLite connection pool and storage lifecycle.

    Thread-safe: the underlying ``ConnectionPool`` gives each thread its own
    reused WAL-mode connection. Call ``start()`` before use and ``close()`` on
    shutdown.
    """

    def __init__(
        self,
        storage_dir: Path | None = None,
        project_root: Path | None = None,
        namespace: str = "hub",
    ) -> None:
        self._namespace = namespace
        self._project_root = project_root
        # T6.16: resolve cwd once at init. Agents registering later will
        # read ``effective_worktree_root`` rather than calling
        # ``os.getcwd()`` themselves — a hub that chdirs mid-run would
        # otherwise hand out inconsistent worktree roots to different
        # agents.
        self._effective_worktree_root: Path = (
            Path(project_root).resolve() if project_root is not None else Path.cwd().resolve()
        )
        self._storage_dir = self._resolve_storage_dir(storage_dir)
        self._pool: _db.ConnectionPool | None = None
        self._db_path: Path | None = None
        self._seq_lock = threading.Lock()
        self._seq_counters: dict[str, int] = {}

    # ------------------------------------------------------------------ #
    # Storage path
    # ------------------------------------------------------------------ #

    def _resolve_storage_dir(self, storage_dir: Path | str | None) -> Path:
        if storage_dir is not None:
            return Path(storage_dir).resolve()
        if self._project_root is not None:
            base = self._project_root / ".coordinationhub"
            base.mkdir(parents=True, exist_ok=True)
            return base
        return Path.home() / ".coordinationhub"

    @property
    def project_root(self) -> Path | None:
        return self._project_root

    @property
    def effective_worktree_root(self) -> Path:
        """The effective worktree root resolved once at init.

        Equals ``project_root`` when set, otherwise the cwd at engine
        construction time. Safe to consult even if the process later
        ``chdir``s — the value is frozen (T6.16).
        """
        return self._effective_worktree_root

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """Create the storage directory, open the connection pool, and init schemas."""
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._storage_dir / "coordination.db"
        self._pool = _db.ConnectionPool(self._db_path)
        _db.set_pool(self._pool)
        with self._pool.connect() as conn:
            _db.init_schema(conn)

    def close(self) -> None:
        """Checkpoint the WAL and close the connection pool."""
        if self._pool is not None:
            with self._pool.connect() as conn:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            _db.clear_pool()
            self._pool = None

    def _connect(self) -> sqlite3.Connection:
        """Return a connection from the pool. Raises RuntimeError if not started."""
        if self._pool is None:
            raise RuntimeError("Storage not started. Call start() first.")
        return self._pool.connect()

    def read_only_connection(self) -> sqlite3.Connection:
        """Return a direct read-only SQLite connection via WAL URI.

        Bypasses the thread-local writer pool entirely. Safe for concurrent
        reads — SQLite WAL mode allows multiple readers without blocking the
        writer. Use this for read-replica commands that don't need write access.
        """
        if self._db_path is None:
            raise RuntimeError("Storage not started. Call start() first.")
        conn = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------------ #
    # Agent ID generation
    # ------------------------------------------------------------------ #

    def _next_seq(self, prefix: str, conn: sqlite3.Connection) -> int:
        """Return the next available sequence number for prefix by
        examining existing agent rows whose id is ``{prefix}.{n}``.

        The suffix is extracted numerically (CAST) rather than sorted
        lexicographically. A lex sort orders `...9` > `...10`, so the
        old implementation wrapped back to seq 10 after creating 10
        agents, causing colliding agent_ids on the 11th registration.
        """
        base = prefix.rstrip(".")
        # Extract the portion after the last dot and cast to int.
        # substr(agent_id, length(base) + 2) strips "{base}." leaving the tail.
        row = conn.execute(
            "SELECT MAX(CAST(substr(agent_id, ? + 2) AS INTEGER)) AS max_seq "
            "FROM agents WHERE agent_id LIKE ? || '.%' "
            "AND substr(agent_id, ? + 2) GLOB '[0-9]*'",
            (len(base), base, len(base)),
        ).fetchone()
        if row is None or row["max_seq"] is None:
            return 0
        return int(row["max_seq"]) + 1

    def _next_seq_atomic(self, prefix: str, conn: sqlite3.Connection) -> int:
        """Return the next sequence number for *prefix*, using an in-memory
        counter seeded from the DB on first access. Must be called under
        ``_seq_lock``.
        """
        if prefix not in self._seq_counters:
            self._seq_counters[prefix] = self._next_seq(prefix, conn)
        else:
            self._seq_counters[prefix] += 1
        return self._seq_counters[prefix]

    def generate_agent_id(self, parent_id: str | None = None) -> str:
        """Generate a unique agent ID.

        Root agents: ``{namespace}.{PID}.{sequence}``.
        Child agents: ``{parent_id}.{sequence}``.

        Thread-safe: serialized via ``_seq_lock`` with in-memory counters
        so IDs are unique even before the agent is registered.
        """
        with self._seq_lock:
            pid = os.getpid()
            prefix = f"{self._namespace}.{pid}"
            with self._connect() as conn:
                if parent_id is None:
                    seq = self._next_seq_atomic(prefix, conn)
                    return f"{prefix}.{seq}"
                row = conn.execute(
                    "SELECT agent_id FROM agents WHERE agent_id = ?", (parent_id,)
                ).fetchone()
                if not row:
                    raise ValueError(f"Parent agent not found: {parent_id}")
                seq = self._next_seq_atomic(f"{parent_id}.", conn)
                return f"{parent_id}.{seq}"
