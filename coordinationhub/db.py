"""SQLite schema, migrations, and connection pool for CoordinationHub.

Zero internal dependencies — uses only the Python standard library.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Callable

# Type alias for the connect function passed by callers
ConnectFn = Callable[[], sqlite3.Connection]


def _db_path(storage_dir: Path) -> Path:
    """Return the path to the coordination SQLite database."""
    return storage_dir / "coordination.db"


# ------------------------------------------------------------------ #
# Schema definition
# ------------------------------------------------------------------ #

_SCHEMAS = {
    "agents": """
        CREATE TABLE IF NOT EXISTS agents (
            agent_id        TEXT PRIMARY KEY,
            parent_id       TEXT,
            worktree_root   TEXT NOT NULL,
            pid             INTEGER,
            started_at      REAL NOT NULL,
            last_heartbeat  REAL NOT NULL,
            status          TEXT DEFAULT 'active',
            claude_agent_id TEXT
        )
    """,
    "lineage": """
        CREATE TABLE IF NOT EXISTS lineage (
            parent_id  TEXT NOT NULL,
            child_id    TEXT NOT NULL,
            spawned_at REAL NOT NULL,
            PRIMARY KEY (parent_id, child_id)
        )
    """,
    "document_locks": """
        CREATE TABLE IF NOT EXISTS document_locks (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            document_path  TEXT NOT NULL,
            locked_by      TEXT NOT NULL,
            locked_at      REAL NOT NULL,
            lock_ttl       REAL DEFAULT 300.0,
            lock_type      TEXT DEFAULT 'exclusive',
            region_start   INTEGER,
            region_end     INTEGER,
            worktree_root  TEXT
        )
    """,
    "lock_conflicts": """
        CREATE TABLE IF NOT EXISTS lock_conflicts (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            document_path TEXT NOT NULL,
            agent_a       TEXT NOT NULL,
            agent_b       TEXT NOT NULL,
            conflict_type TEXT NOT NULL,
            resolution    TEXT DEFAULT 'rejected',
            details_json  TEXT,
            created_at    REAL NOT NULL
        )
    """,
    "change_notifications": """
        CREATE TABLE IF NOT EXISTS change_notifications (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            document_path TEXT NOT NULL,
            change_type   TEXT NOT NULL,
            agent_id      TEXT NOT NULL,
            worktree_root TEXT,
            created_at    REAL NOT NULL
        )
    """,
    "agent_responsibilities": """
        CREATE TABLE IF NOT EXISTS agent_responsibilities (
            agent_id        TEXT PRIMARY KEY,
            graph_agent_id  TEXT,
            role            TEXT,
            model           TEXT,
            responsibilities TEXT,
            current_task    TEXT,
            updated_at      REAL NOT NULL
        )
    """,
    "pending_subagent_tasks": """
        CREATE TABLE IF NOT EXISTS pending_subagent_tasks (
            tool_use_id    TEXT PRIMARY KEY,
            session_id     TEXT NOT NULL,
            subagent_type  TEXT NOT NULL,
            description    TEXT,
            prompt         TEXT,
            created_at     REAL NOT NULL,
            consumed_at    REAL
        )
    """,
    "file_ownership": """
        CREATE TABLE IF NOT EXISTS file_ownership (
            document_path     TEXT PRIMARY KEY,
            assigned_agent_id TEXT NOT NULL,
            assigned_at      REAL NOT NULL,
            last_claimed_by  TEXT,
            task_description TEXT
        )
    """,
    "assessment_results": """
        CREATE TABLE IF NOT EXISTS assessment_results (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            suite_name      TEXT NOT NULL,
            metric          TEXT NOT NULL,
            score           REAL NOT NULL,
            details_json    TEXT,
            run_at          REAL NOT NULL
        )
    """,
    "descendant_registry": """
        CREATE TABLE IF NOT EXISTS descendant_registry (
            ancestor_id   TEXT NOT NULL,
            descendant_id TEXT NOT NULL,
            depth         INTEGER NOT NULL DEFAULT 1,
            registered_at REAL NOT NULL,
            PRIMARY KEY (ancestor_id, descendant_id)
        )
    """,
}

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status)",
    "CREATE INDEX IF NOT EXISTS idx_agents_parent ON agents(parent_id)",
    "CREATE INDEX IF NOT EXISTS idx_lineage_child ON lineage(child_id)",
    "CREATE INDEX IF NOT EXISTS idx_lineage_parent ON lineage(parent_id)",
    "CREATE INDEX IF NOT EXISTS idx_locks_path ON document_locks(document_path)",
    "CREATE INDEX IF NOT EXISTS idx_locks_locked_by ON document_locks(locked_by)",
    "CREATE INDEX IF NOT EXISTS idx_conflicts_doc ON lock_conflicts(document_path)",
    "CREATE INDEX IF NOT EXISTS idx_conflicts_time ON lock_conflicts(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_notif_time ON change_notifications(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_notif_agent ON change_notifications(agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_file_owner_agent ON file_ownership(assigned_agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_locks_expiry ON document_locks(document_path, locked_at, lock_ttl)",
    "CREATE INDEX IF NOT EXISTS idx_agents_claude_id ON agents(claude_agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_pending_subagent_session_type ON pending_subagent_tasks(session_id, subagent_type, consumed_at)",
    "CREATE INDEX IF NOT EXISTS idx_descendant_ancestor ON descendant_registry(ancestor_id)",
]


_CURRENT_SCHEMA_VERSION = 4


def _get_schema_version(conn: sqlite3.Connection) -> int:
    """Return the current schema version, or 0 if not yet tracked."""
    try:
        row = conn.execute(
            "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
        ).fetchone()
        return row["version"] if row else 0
    except sqlite3.OperationalError:
        return 0


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """Migrate document_locks from single-lock-per-file to multi-lock with regions.

    v1: document_path TEXT PRIMARY KEY (one lock per file)
    v2: id INTEGER PRIMARY KEY AUTOINCREMENT, region_start/region_end columns
    """
    # Check if old schema (document_path as PK, no id column)
    cols = [row[1] for row in conn.execute("PRAGMA table_info(document_locks)").fetchall()]
    if "region_start" in cols:
        return  # Already migrated

    conn.execute("ALTER TABLE document_locks RENAME TO _document_locks_v1")
    conn.execute(_SCHEMAS["document_locks"])
    conn.execute("""
        INSERT INTO document_locks (document_path, locked_by, locked_at, lock_ttl, lock_type, worktree_root)
        SELECT document_path, locked_by, locked_at, lock_ttl, lock_type, worktree_root
        FROM _document_locks_v1
    """)
    conn.execute("DROP TABLE _document_locks_v1")
    # Recreate indexes for new table
    conn.execute("CREATE INDEX IF NOT EXISTS idx_locks_path ON document_locks(document_path)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_locks_locked_by ON document_locks(locked_by)")


def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    """Add claude_agent_id column to agents table.

    Maps raw Claude Code agent hex IDs back to hub.cc.* child IDs so that
    PreToolUse hooks resolve the correct agent after SubagentStart registers.
    """
    cols = [row[1] for row in conn.execute("PRAGMA table_info(agents)").fetchall()]
    if "claude_agent_id" in cols:
        return  # Already migrated
    conn.execute("ALTER TABLE agents ADD COLUMN claude_agent_id TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agents_claude_id ON agents(claude_agent_id)")


_MIGRATIONS = {
    2: _migrate_v1_to_v2,
    3: _migrate_v2_to_v3,
    4: lambda conn: None,  # descendant_registry added via CREATE TABLE IF NOT EXISTS
}


def init_schema(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes if they don't exist. Run pending migrations.

    The recorded ``schema_version`` is advisory — each migration also
    checks actual table shapes via ``PRAGMA table_info`` and no-ops if
    already applied.  This tolerates DBs stamped by buggy older
    init_schema implementations that recorded a version without
    actually running the migrations.  On every ``init_schema`` call we:

    1. Ensure ``schema_version`` exists.
    2. Run ``CREATE TABLE IF NOT EXISTS`` for every table — fresh DBs
       get the latest shape, existing DBs are untouched.
    3. Unconditionally run every migration in version order — each one
       is idempotent, so this catches DBs where the version was stamped
       but the migration code never actually ran.
    4. Create all indexes (idempotent) against the now-current shape.
    5. Record ``_CURRENT_SCHEMA_VERSION``.
    """
    # Create version tracking table first
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version    INTEGER PRIMARY KEY,
            applied_at REAL NOT NULL
        )
    """)

    # Always create any tables that don't yet exist.  For fresh installs this
    # is everything; for legacy DBs it covers tables added after v1 (lineage,
    # lock_conflicts, change_notifications, file_ownership, etc.) that the
    # migrations do not create.  ``CREATE TABLE IF NOT EXISTS`` is a no-op
    # for tables that already exist with a different shape — those are
    # handled by the migration runner below.
    for sql in _SCHEMAS.values():
        conn.execute(sql)

    # Always run every migration in order — each one is idempotent (checks
    # ``PRAGMA table_info`` and skips work that is already applied).  This
    # catches DBs stamped with a version number by earlier buggy code
    # paths without the underlying migration actually running.
    for ver in sorted(_MIGRATIONS.keys()):
        _MIGRATIONS[ver](conn)

    # Indexes are idempotent (``CREATE INDEX IF NOT EXISTS``) and reference
    # columns from the latest schema, so they must run AFTER any pending
    # migration has added those columns.
    for idx_sql in _INDEXES:
        conn.execute(idx_sql)

    # Record current version — overwrites any stale/wrong earlier value.
    current = _get_schema_version(conn)
    if current < _CURRENT_SCHEMA_VERSION:
        import time
        conn.execute(
            "INSERT OR REPLACE INTO schema_version (version, applied_at) VALUES (?, ?)",
            (_CURRENT_SCHEMA_VERSION, time.time()),
        )


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
