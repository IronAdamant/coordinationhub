"""SQLite connection pool and public re-exports for CoordinationHub.

Schema definitions live in :mod:`db_schemas` (pure data) and migration
functions + the ``init_schema`` driver live in :mod:`db_migrations`.
This module owns the thread-local connection pool and re-exports the
pieces that the rest of the package (and tests) depends on.

Zero internal dependencies beyond its two sibling modules.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Callable

from .db_schemas import _SCHEMAS, _INDEXES
from .db_migrations import (
    _CURRENT_SCHEMA_VERSION,
    _MIGRATIONS,
    _get_schema_version,
    init_schema,
)

# Type alias for the connect function passed by callers
ConnectFn = Callable[[], sqlite3.Connection]


def _db_path(storage_dir: Path) -> Path:
    """Return the path to the coordination SQLite database."""
    return storage_dir / "coordination.db"


# ------------------------------------------------------------------ #
# Connection pool (thread-local, max 1 conn per thread, reused)
# ------------------------------------------------------------------ #

class ConnectionPool:
    """Thread-local SQLite connection pool.

    Each thread gets exactly one connection, reused across calls.
    This eliminates the overhead of opening/closing connections per
    method call (~70 opens eliminated on a typical workload).

    The pool is created by StorageBackend and used within a `with db.connect()
    as conn:` context manager pattern.
    """

    __slots__ = ("_local", "_db_path")

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._local = threading.local()

    def connect(self) -> sqlite3.Connection:
        """Return this thread's connection, creating if needed."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.execute("SELECT 1")
            except sqlite3.DatabaseError:
                # T7.24: catch the full DatabaseError hierarchy so a
                # corruption indicator (NotADatabaseError, generic
                # DatabaseError) drops the cached connection instead
                # of propagating. The narrower pair (ProgrammingError,
                # OperationalError) missed corruption shapes.
                conn = None
        if conn is None:
            conn = _create_connection(self._db_path)
            self._local.conn = conn
        return conn

    def close_all(self) -> None:
        """Close this thread's connection if open."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None


def _create_connection(db_path: Path) -> sqlite3.Connection:
    """Create a WAL-mode SQLite connection with proper pragmas and row factory."""
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA cache_size=-8000")     # 8MB page cache
    conn.execute("PRAGMA mmap_size=67108864")   # 64MB memory-mapped I/O
    conn.row_factory = sqlite3.Row
    return conn


# ------------------------------------------------------------------ #
# Module-level connect helper (pool-aware, for use in sub-modules)
# ------------------------------------------------------------------ #

# The active pool is set by StorageBackend.__init__ and cleared on close.
# Sub-modules (agent_registry, lock_ops, etc.) receive a connect() callable
# from their caller, so they never need to import this directly.
_pool: ConnectionPool | None = None


def set_pool(pool: ConnectionPool) -> None:
    """Set the module-level connection pool (called by StorageBackend)."""
    global _pool
    _pool = pool


def clear_pool() -> None:
    """Clear the module-level pool (called on shutdown)."""
    global _pool
    if _pool is not None:
        _pool.close_all()
        _pool = None


def connect() -> sqlite3.Connection:
    """Return a connection from the active pool. Must only be called within a StorageBackend context."""
    if _pool is None:
        raise RuntimeError("No active connection pool. Initialize StorageBackend first.")
    return _pool.connect()


__all__ = [
    "ConnectFn",
    "ConnectionPool",
    "init_schema",
    "set_pool",
    "clear_pool",
    "connect",
    "_SCHEMAS",
    "_INDEXES",
    "_CURRENT_SCHEMA_VERSION",
    "_MIGRATIONS",
    "_get_schema_version",
]
