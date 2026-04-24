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


_CURRENT_SCHEMA_VERSION = 26


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

    Historical migration: mapped raw Claude Code agent hex IDs back to
    hub.cc.* child IDs so that PreToolUse hooks could resolve the correct
    agent after SubagentStart registered.

    Superseded by v21 which renamed claude_agent_id → raw_ide_id for
    vendor neutrality. On fresh installs the current _SCHEMAS["agents"]
    shape already includes raw_ide_id, so this migration must skip to
    avoid re-introducing the vestigial claude_agent_id column that v21
    would then leave orphaned (v21 returns early when raw_ide_id exists).
    """
    cols = [row[1] for row in conn.execute("PRAGMA table_info(agents)").fetchall()]
    if "claude_agent_id" in cols or "raw_ide_id" in cols:
        return
    conn.execute("ALTER TABLE agents ADD COLUMN claude_agent_id TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agents_claude_id ON agents(claude_agent_id)")


def _migrate_v25_to_v26(conn: sqlite3.Connection) -> None:
    """Install BEFORE INSERT/UPDATE triggers enforcing status enums on
    ``tasks`` and ``pending_tasks`` (T4.2, T4.5).

    SQLite cannot add CHECK constraints in place without a full table
    rebuild. Triggers deliver the same guarantee (reject writes with an
    invalid enum value) without the rebuild risk, and they're
    idempotent via ``CREATE TRIGGER IF NOT EXISTS``.

    tasks.status enum matches ``tasks.py::_VALID_TASK_STATUSES``.
    pending_tasks.status covers the four states produced by the
    spawner pipeline: pending (stashed), registered (child_agent_id
    attached), consumed (legacy synonym for registered), expired
    (cancelled or TTL-reaped).
    """
    # CREATE TRIGGER IF NOT EXISTS is idempotent. Each trigger is
    # issued as a separate ``execute`` (not ``executescript``) because
    # the migration driver wraps this call in BEGIN / COMMIT and
    # ``executescript`` implicitly commits, which breaks the outer
    # transaction boundary.
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_tasks_status_check_insert
        BEFORE INSERT ON tasks
        FOR EACH ROW
        WHEN NEW.status NOT IN (
            'pending', 'in_progress', 'completed', 'blocked',
            'failed', 'dead_letter'
        )
        BEGIN
            SELECT RAISE(ABORT, 'tasks.status: invalid enum value');
        END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_tasks_status_check_update
        BEFORE UPDATE OF status ON tasks
        FOR EACH ROW
        WHEN NEW.status NOT IN (
            'pending', 'in_progress', 'completed', 'blocked',
            'failed', 'dead_letter'
        )
        BEGIN
            SELECT RAISE(ABORT, 'tasks.status: invalid enum value');
        END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_pending_tasks_status_check_insert
        BEFORE INSERT ON pending_tasks
        FOR EACH ROW
        WHEN NEW.status NOT IN (
            'pending', 'registered', 'consumed', 'expired'
        )
        BEGIN
            SELECT RAISE(ABORT, 'pending_tasks.status: invalid enum value');
        END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_pending_tasks_status_check_update
        BEFORE UPDATE OF status ON pending_tasks
        FOR EACH ROW
        WHEN NEW.status NOT IN (
            'pending', 'registered', 'consumed', 'expired'
        )
        BEGIN
            SELECT RAISE(ABORT, 'pending_tasks.status: invalid enum value');
        END
    """)


def _migrate_v24_to_v25(conn: sqlite3.Connection) -> None:
    """Add ``error`` column to ``tasks`` (T6.39).

    Pre-fix, ``update_task_status`` accepted an ``error`` arg but only
    forwarded it to the task-failure record when ``status=='failed'``.
    Callers transitioning to ``blocked`` or ``in_progress`` had no place
    to record diagnostic context, so the error string was silently
    dropped. This column lets every status transition carry its own
    diagnostic message.
    """
    cols = [row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()]
    if "error" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN error TEXT")


def _migrate_v23_to_v24(conn: sqlite3.Connection) -> None:
    """Add ``ide_vendor`` column to ``agents`` and a uniqueness index on
    (raw_ide_id, ide_vendor).

    T3.12: raw IDE agent IDs aren't vendor-globally unique; two
    different IDEs happening to share an id shape would collide on a
    naked UNIQUE(raw_ide_id). Adding a vendor namespace lets us
    enforce "one active hub-agent per raw-id per vendor" without
    cross-vendor false positives.
    """
    cols = [row[1] for row in conn.execute("PRAGMA table_info(agents)").fetchall()]
    if "ide_vendor" not in cols:
        conn.execute("ALTER TABLE agents ADD COLUMN ide_vendor TEXT")
    # Unique index on the (raw_ide_id, ide_vendor) pair. Partial so
    # rows without raw_ide_id (local-only agents) aren't forced to
    # share a single slot.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_agents_raw_ide_pair "
        "ON agents(raw_ide_id, ide_vendor) "
        "WHERE raw_ide_id IS NOT NULL"
    )


def _migrate_v22_to_v23(conn: sqlite3.Connection) -> None:
    """Relax work_intent PK from (agent_id) to (agent_id, document_path).

    T1.16: prior to v23 an agent could only declare intent on one file —
    declaring on file B silently erased intent on file A. The new PK
    allows multiple live intents per agent, so cross-file conflict
    detection can actually work.

    SQLite cannot drop a PK constraint in-place; rebuild the table.
    """
    # PRAGMA index_list returns PK constraints too — check if the PK is
    # already compound by inspecting PRAGMA index_info of the implicit
    # 'sqlite_autoindex_work_intent_*' index.
    idx_rows = conn.execute("PRAGMA index_list(work_intent)").fetchall()
    if idx_rows:
        for idx in idx_rows:
            # idx columns: seq, name, unique, origin ('pk'/'c'/'u')
            if (len(idx) > 3 and idx[3] == "pk") or "autoindex" in idx[1]:
                info = conn.execute(f"PRAGMA index_info('{idx[1]}')").fetchall()
                # info rows: seqno, cid, name — count how many PK columns
                if len(info) >= 2:
                    return  # already compound PK
    # Check the table exists at all (handles pristine DBs that already
    # got the new shape via _SCHEMAS).
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "work_intent" not in tables:
        conn.execute(_SCHEMAS["work_intent"])
        return
    conn.execute("ALTER TABLE work_intent RENAME TO _work_intent_v22")
    conn.execute(_SCHEMAS["work_intent"])
    conn.execute(
        """
        INSERT OR IGNORE INTO work_intent
        (agent_id, document_path, intent, declared_at, ttl)
        SELECT agent_id, document_path, intent, declared_at, ttl
        FROM _work_intent_v22
        """
    )
    conn.execute("DROP TABLE _work_intent_v22")


def _migrate_v21_to_v22(conn: sqlite3.Connection) -> None:
    """Add broadcast_targets table (T1.11).

    Pre-fix, ``broadcasts.expected_count`` was a scalar and pending_acks
    could not be computed — get_broadcast_status always returned an empty
    list. This table snapshots the exact set of sibling agents a broadcast
    was delivered to, so pending_acks = targets - acks and late-joiners
    are explicitly excluded.
    """
    tables = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "broadcast_targets" not in tables:
        conn.execute(_SCHEMAS["broadcast_targets"])


def _migrate_v20_to_v21(conn: sqlite3.Connection) -> None:
    """Rename claude_agent_id column to raw_ide_id for vendor neutrality.

    The column stores the raw IDE-specific agent ID (e.g. Claude Code hex ID,
    Kimi CLI session ID, etc.). Renaming makes the schema vendor-neutral.
    """
    cols = [row[1] for row in conn.execute("PRAGMA table_info(agents)").fetchall()]
    if "raw_ide_id" in cols:
        return
    if "claude_agent_id" in cols:
        conn.execute("ALTER TABLE agents RENAME COLUMN claude_agent_id TO raw_ide_id")
        conn.execute("DROP INDEX IF EXISTS idx_agents_claude_id")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agents_raw_ide_id ON agents(raw_ide_id)")
    else:
        conn.execute("ALTER TABLE agents ADD COLUMN raw_ide_id TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agents_raw_ide_id ON agents(raw_ide_id)")


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
    (Kimi CLI, Cursor, etc.) and adds broadcast
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


# T7.30: single shared no-op used for migrations where the table was
# added via CREATE TABLE IF NOT EXISTS in _SCHEMAS (i.e. fresh installs
# got it at init, and existing DBs never needed a backfill).
def _noop_migration(conn: sqlite3.Connection) -> None:  # pragma: no cover
    return None


_MIGRATIONS = {
    2: _migrate_v1_to_v2,
    3: _migrate_v2_to_v3,
    4: _noop_migration,  # descendant_registry added via CREATE TABLE IF NOT EXISTS
    5: _noop_migration,  # messages table added via CREATE TABLE IF NOT EXISTS
    6: _noop_migration,  # scope column added via CREATE TABLE IF NOT EXISTS (legacy no-op)
    7: _noop_migration,  # tasks table added via CREATE TABLE IF NOT EXISTS
    8: _noop_migration,  # work_intent table added via CREATE TABLE IF NOT EXISTS
    9: _noop_migration,  # handoffs + handoff_acks tables added via CREATE TABLE IF NOT EXISTS
    10: _noop_migration,  # agent_dependencies table added via CREATE TABLE IF NOT EXISTS
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
    21: _migrate_v20_to_v21,  # rename claude_agent_id -> raw_ide_id
    22: _migrate_v21_to_v22,  # broadcast_targets table (T1.11)
    23: _migrate_v22_to_v23,  # work_intent PK: (agent_id, document_path) (T1.16)
    24: _migrate_v23_to_v24,  # agents.ide_vendor + unique(raw_ide_id, ide_vendor) (T3.12)
    25: _migrate_v24_to_v25,  # tasks.error column (T6.39)
    26: _migrate_v25_to_v26,  # tasks/pending_tasks status CHECK triggers (T4.2, T4.5)
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

    # T4.9: wrap each migration in an explicit BEGIN/COMMIT with
    # rollback-on-exception so a failed CREATE/INSERT/DROP sequence
    # (e.g. partial v1->v2) doesn't leave a ``_document_locks_v1``
    # half-rebuilt table stranded. Every migration is idempotent, so
    # re-running a rolled-back migration after a fix is safe.
    import logging as _logging
    _log = _logging.getLogger(__name__)
    for ver in sorted(_MIGRATIONS.keys()):
        migrate = _MIGRATIONS[ver]
        try:
            conn.execute("BEGIN")
            migrate(conn)
            conn.execute("COMMIT")
        except Exception as exc:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            _log.error(
                "migration v%d failed: %s; rolling back and re-raising", ver, exc,
            )
            raise

    for idx_sql in _INDEXES:
        conn.execute(idx_sql)

    current = _get_schema_version(conn)
    if current < _CURRENT_SCHEMA_VERSION:
        conn.execute(
            "INSERT OR REPLACE INTO schema_version (version, applied_at) VALUES (?, ?)",
            (_CURRENT_SCHEMA_VERSION, time.time()),
        )
