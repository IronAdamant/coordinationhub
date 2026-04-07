"""Storage backend for CoordinationHub — SQLite pool, path resolution, lifecycle.

Exposes a single ``CoordinationStorage`` class that owns the SQLite connection pool
and all schema initialisation. Both ``core.CoordinationEngine`` and CLI entry
points depend on this module — it has no internal dependencies on any other
coordinationhub sub-module.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from . import db as _db
from . import notifications as _cn
from . import assessment as _assess

if TYPE_CHECKING:
    import graphs as _g


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
        self._storage_dir = self._resolve_storage_dir(storage_dir)
        self._pool: _db.ConnectionPool | None = None

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

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """Create the storage directory, open the connection pool, and init schemas."""
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        db_path = self._storage_dir / "coordination.db"
        self._pool = _db.ConnectionPool(db_path)
        _db.set_pool(self._pool)
        with self._pool.connect() as conn:
            _db.init_schema(conn)
            _cn.init_notifications_table(self._pool.connect)
            _assess.init_assessment_table(conn)

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

    # ------------------------------------------------------------------ #
    # Agent ID generation
    # ------------------------------------------------------------------ #

    def _next_seq(self, prefix: str, conn: sqlite3.Connection) -> int:
        base = prefix.rstrip(".")
        row = conn.execute(
            f"SELECT agent_id FROM agents WHERE agent_id LIKE ? || '.%' ORDER BY agent_id DESC LIMIT 1",
            (base,),
        ).fetchone()
        if row:
            return int(row["agent_id"].rsplit(".", 1)[-1]) + 1
        return 0

    def generate_agent_id(self, parent_id: str | None = None) -> str:
        """Generate a unique agent ID.

        Root agents: ``{namespace}.{PID}.{sequence}``.
        Child agents: ``{parent_id}.{sequence}``.
        """
        pid = os.getpid()
        prefix = f"{self._namespace}.{pid}"
        with self._connect() as conn:
            if parent_id is None:
                seq = self._next_seq(prefix, conn)
                return f"{prefix}.{seq}"
            row = conn.execute(
                "SELECT agent_id FROM agents WHERE agent_id = ?", (parent_id,)
            ).fetchone()
            if not row:
                raise ValueError(f"Parent agent not found: {parent_id}")
            seq = self._next_seq(f"{parent_id}.", conn)
            return f"{parent_id}.{seq}"
