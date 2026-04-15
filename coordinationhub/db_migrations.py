"""Schema-version tracking, migration functions, and the ``init_schema`` driver.

The recorded ``schema_version`` is advisory — each migration also checks
actual table shapes via ``PRAGMA table_info`` and no-ops if already
applied.  This tolerates DBs stamped by buggy older init_schema
implementations that recorded a version without actually running the
migrations.
"""

from __future__ import annotations

import sqlite3
import time

from .db_schemas import _SCHEMAS, _INDEXES


_CURRENT_SCHEMA_VERSION = 20


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
    cols = [row[1] for row in conn.execute("PRAGMA table_info(document_locks)").fetchall()]
    if "region_start" in cols:
        return

    conn.execute("ALTER TABLE document_locks RENAME TO _document_locks_v1")
    conn.execute(_SCHEMAS["document_locks"])
    conn.execute("""
        INSERT INTO document_locks (document_path, locked_by, locked_at, lock_ttl, lock_type, worktree_root)
        SELECT document_path, locked_by, locked_at, lock_ttl, lock_type, worktree_root
        FROM _document_locks_v1
    """)
    conn.execute("DROP TABLE _document_locks_v1")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_locks_path ON document_locks(document_path)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_locks_locked_by ON document_locks(locked_by)")


def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    """Add claude_agent_id column to agents table.

    Maps raw Claude Code agent hex IDs back to hub.cc.* child IDs so that
    PreToolUse hooks resolve the correct agent after SubagentStart registers.
    """
    cols = [row[1] for row in conn.execute("PRAGMA table_info(agents)").fetchall()]
    if "claude_agent_id" in cols:
        return
    conn.execute("ALTER TABLE agents ADD COLUMN claude_agent_id TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agents_claude_id ON agents(claude_agent_id)")


def _migrate_v10_to_v11(conn: sqlite3.Connection) -> None:
    """Add parent_task_id column to tasks table for subtask hierarchy."""
    cols = [row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()]
    if "parent_task_id" in cols:
        return
    conn.execute("ALTER TABLE tasks ADD COLUMN parent_task_id TEXT")


def _migrate_v11_to_v12(conn: sqlite3.Connection) -> None:
    """Add priority column to tasks table."""
    cols = [row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()]
    if "priority" in cols:
        return
    conn.execute("ALTER TABLE tasks ADD COLUMN priority INTEGER DEFAULT 0")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority DESC, created_at ASC)")


def _migrate_v12_to_v13(conn: sqlite3.Connection) -> None:
    """Add task_failures table for dead letter queue."""
    cols = [row[1] for row in conn.execute("PRAGMA table_info(task_failures)").fetchall()]
    if "task_id" in cols:
        return
    conn.execute(_SCHEMAS["task_failures"])
    conn.execute("CREATE INDEX IF NOT EXISTS idx_task_failures_task ON task_failures(task_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_task_failures_status ON task_failures(status)")


def _migrate_v13_to_v14(conn: sqlite3.Connection) -> None:
    """Add coordinator_leases table for HA coordinator leadership."""
    cols = [row[1] for row in conn.execute("PRAGMA table_info(coordinator_leases)").fetchall()]
    if "lease_name" in cols:
        return
    conn.execute(_SCHEMAS["coordinator_leases"])
    conn.execute("CREATE INDEX IF NOT EXISTS idx_leases_expires ON coordinator_leases(expires_at)")


def _migrate_v14_to_v15(conn: sqlite3.Connection) -> None:
    """Add pending_spawner_tasks table for HA spawner sub-agent registry."""
    cols = [row[1] for row in conn.execute("PRAGMA table_info(pending_spawner_tasks)").fetchall()]
    if "id" in cols:
        return
    conn.execute(_SCHEMAS["pending_spawner_tasks"])
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_spawner_parent_type "
        "ON pending_spawner_tasks(parent_agent_id, subagent_type, status)"
    )


def _migrate_v15_to_v16(conn: sqlite3.Connection) -> None:
    """Add stop_requested_at column to agents table for spawner deregistration requests."""
    cols = [row[1] for row in conn.execute("PRAGMA table_info(agents)").fetchall()]
    if "stop_requested_at" in cols:
        return
    conn.execute("ALTER TABLE agents ADD COLUMN stop_requested_at REAL")


def _migrate_v16_to_v17(conn: sqlite3.Connection) -> None:
    """Add scope column to agent_responsibilities for legacy databases.

    The column was originally added via CREATE TABLE IF NOT EXISTS, which
    is a no-op for existing tables. This migration ensures existing DBs
    get the column via ALTER TABLE.
    """
    cols = [row[1] for row in conn.execute("PRAGMA table_info(agent_responsibilities)").fetchall()]
    if "scope" in cols:
        return
    conn.execute("ALTER TABLE agent_responsibilities ADD COLUMN scope TEXT")


def _migrate_v17_to_v18(conn: sqlite3.Connection) -> None:
    """Add source column to pending_spawner_tasks and broadcasts tables.

    Makes sub-agent spawn tracking agnostic to the spawning system
    (Claude Code, Kimi CLI, Cursor, etc.) and adds broadcast
    acknowledgment tracking for delivery confirmation.
    """
    cols = [row[1] for row in conn.execute("PRAGMA table_info(pending_spawner_tasks)").fetchall()]
    if "source" not in cols:
        conn.execute("ALTER TABLE pending_spawner_tasks ADD COLUMN source TEXT DEFAULT 'external'")

    tables = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "broadcasts" not in tables:
        conn.execute(_SCHEMAS["broadcasts"])
    if "broadcast_acks" not in tables:
        conn.execute(_SCHEMAS["broadcast_acks"])


def _migrate_v18_to_v19(conn: sqlite3.Connection) -> None:
    """Add expected_count column to broadcasts table.

    Allows get_broadcast_status to report how many acknowledgments
    are still pending for explicit-ack broadcasts.
    """
    cols = [row[1] for row in conn.execute("PRAGMA table_info(broadcasts)").fetchall()]
    if "expected_count" not in cols:
        conn.execute("ALTER TABLE broadcasts ADD COLUMN expected_count INTEGER DEFAULT 0")


def _migrate_v19_to_v20(conn: sqlite3.Connection) -> None:
    """Merge pending_subagent_tasks and pending_spawner_tasks into pending_tasks.

    Unifies the two similar tables into a single pending_tasks table.
    """
    tables = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "pending_tasks" in tables:
        return

    conn.execute(_SCHEMAS["pending_tasks"])

    if "pending_subagent_tasks" in tables:
        conn.execute("""
            INSERT INTO pending_tasks
            (task_id, scope_id, subagent_type, description, prompt, created_at, consumed_at, status, source)
            SELECT
                tool_use_id,
                session_id,
                subagent_type,
                description,
                prompt,
                created_at,
                consumed_at,
                CASE WHEN consumed_at IS NULL THEN 'pending' ELSE 'consumed' END,
                'external'
            FROM pending_subagent_tasks
        """)
        conn.execute("DROP TABLE pending_subagent_tasks")

    if "pending_spawner_tasks" in tables:
        conn.execute("""
            INSERT INTO pending_tasks
            (task_id, scope_id, subagent_type, description, prompt, created_at, consumed_at, status, source)
            SELECT
                id,
                parent_agent_id,
                subagent_type,
                description,
                prompt,
                created_at,
                consumed_at,
                status,
                source
            FROM pending_spawner_tasks
        """)
        conn.execute("DROP TABLE pending_spawner_tasks")


_MIGRATIONS = {
    2: _migrate_v1_to_v2,
    3: _migrate_v2_to_v3,
    4: lambda conn: None,  # descendant_registry added via CREATE TABLE IF NOT EXISTS
    5: lambda conn: None,  # messages table added via CREATE TABLE IF NOT EXISTS
    6: lambda conn: None,  # scope column added via CREATE TABLE IF NOT EXISTS (legacy no-op)
    7: lambda conn: None,  # tasks table added via CREATE TABLE IF NOT EXISTS
    8: lambda conn: None,  # work_intent table added via CREATE TABLE IF NOT EXISTS
    9: lambda conn: None,  # handoffs + handoff_acks tables added via CREATE TABLE IF NOT EXISTS
    10: lambda conn: None,  # agent_dependencies table added via CREATE TABLE IF NOT EXISTS
    11: _migrate_v10_to_v11,  # parent_task_id column added via ALTER TABLE
    12: _migrate_v11_to_v12,  # priority column added via ALTER TABLE
    13: _migrate_v12_to_v13,  # task_failures table added
    14: _migrate_v13_to_v14,  # coordinator_leases table added
    15: _migrate_v14_to_v15,  # pending_spawner_tasks table added
    16: _migrate_v15_to_v16,  # stop_requested_at column added
    17: _migrate_v16_to_v17,  # scope column added to agent_responsibilities
    18: _migrate_v17_to_v18,  # source column + broadcasts tables
    19: _migrate_v18_to_v19,  # expected_count column on broadcasts
    20: _migrate_v19_to_v20,  # merge pending_subagent_tasks and pending_spawner_tasks
}


def init_schema(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes if they don't exist. Run pending migrations.

    On every call we:

    1. Ensure ``schema_version`` exists.
    2. Run ``CREATE TABLE IF NOT EXISTS`` for every table — fresh DBs
       get the latest shape, existing DBs are untouched.
    3. Unconditionally run every migration in version order — each one
       is idempotent, so this catches DBs where the version was stamped
       but the migration code never actually ran.
    4. Create all indexes (idempotent) against the now-current shape.
    5. Record ``_CURRENT_SCHEMA_VERSION``.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version    INTEGER PRIMARY KEY,
            applied_at REAL NOT NULL
        )
    """)

    for sql in _SCHEMAS.values():
        conn.execute(sql)

    for ver in sorted(_MIGRATIONS.keys()):
        _MIGRATIONS[ver](conn)

    for idx_sql in _INDEXES:
        conn.execute(idx_sql)

    current = _get_schema_version(conn)
    if current < _CURRENT_SCHEMA_VERSION:
        conn.execute(
            "INSERT OR REPLACE INTO schema_version (version, applied_at) VALUES (?, ?)",
            (_CURRENT_SCHEMA_VERSION, time.time()),
        )
