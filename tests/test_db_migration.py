"""Tests for db.init_schema migration resilience (Review Fourteen).

Covers three scenarios that earlier init_schema implementations handled
incorrectly:

1. Pre-v0.3.3 legacy DB — tables exist but no ``schema_version`` row,
   ``document_locks`` still has ``document_path`` as PRIMARY KEY.
2. Stuck-version DB — ``schema_version`` stamped at the latest version
   but the underlying tables were never migrated (caused by a buggy
   earlier init_schema that stamped on the no-op fresh-install path).
3. Fresh install — no tables yet.

All three must converge to the latest schema with every column and index
present, and existing agent/lock rows must be preserved.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from coordinationhub.db import (
    _CURRENT_SCHEMA_VERSION,
    _create_connection,
    init_schema,
)


def _build_v1_db(path: Path) -> None:
    """Create a DB with the pre-v0.3.3 shape.

    * No ``schema_version`` table.
    * ``agents`` without ``raw_ide_id`` column.
    * ``document_locks`` with ``document_path`` as PRIMARY KEY (no
      ``id``, ``region_start``, or ``region_end``).
    """
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE agents ("
        "  agent_id TEXT PRIMARY KEY, parent_id TEXT,"
        "  worktree_root TEXT NOT NULL, pid INTEGER,"
        "  started_at REAL NOT NULL, last_heartbeat REAL NOT NULL,"
        "  status TEXT DEFAULT 'active'"
        ")"
    )
    conn.execute(
        "CREATE TABLE document_locks ("
        "  document_path TEXT PRIMARY KEY, locked_by TEXT NOT NULL,"
        "  locked_at REAL NOT NULL, lock_ttl REAL DEFAULT 300.0,"
        "  lock_type TEXT DEFAULT 'exclusive', worktree_root TEXT"
        ")"
    )
    conn.execute(
        "INSERT INTO agents (agent_id, worktree_root, started_at, last_heartbeat) "
        "VALUES ('hub.legacy', '/tmp', 1000.0, 1000.0)"
    )
    conn.execute(
        "INSERT INTO document_locks (document_path, locked_by, locked_at) "
        "VALUES ('/tmp/legacy.py', 'hub.legacy', 1000.0)"
    )
    conn.commit()
    conn.close()


def _cols(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


class TestLegacyMigration:
    def test_pre_schema_version_db_migrates(self, tmp_path: Path) -> None:
        """A v1 DB predating schema_version tracking must upgrade to v3."""
        db = tmp_path / "legacy.db"
        _build_v1_db(db)

        conn = _create_connection(db)
        init_schema(conn)
        conn.commit()

        agent_cols = _cols(conn, "agents")
        lock_cols = _cols(conn, "document_locks")
        assert "raw_ide_id" in agent_cols
        assert "region_start" in lock_cols
        assert "region_end" in lock_cols
        assert "id" in lock_cols

    def test_legacy_rows_preserved(self, tmp_path: Path) -> None:
        """Migration must preserve existing agent and lock rows."""
        db = tmp_path / "legacy.db"
        _build_v1_db(db)

        conn = _create_connection(db)
        init_schema(conn)
        conn.commit()

        agent = conn.execute(
            "SELECT agent_id FROM agents WHERE agent_id = 'hub.legacy'"
        ).fetchone()
        lock = conn.execute(
            "SELECT locked_by FROM document_locks WHERE document_path = '/tmp/legacy.py'"
        ).fetchone()
        assert agent is not None
        assert lock is not None
        assert lock["locked_by"] == "hub.legacy"

    def test_legacy_db_records_current_version(self, tmp_path: Path) -> None:
        db = tmp_path / "legacy.db"
        _build_v1_db(db)

        conn = _create_connection(db)
        init_schema(conn)
        conn.commit()

        row = conn.execute(
            "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        assert row["version"] == _CURRENT_SCHEMA_VERSION


class TestStuckVersionRecovery:
    """DBs where schema_version was stamped without migrations actually running.

    Earlier init_schema implementations had a buggy path: on an existing
    v1 DB, the "fresh install" branch would call ``CREATE TABLE IF NOT
    EXISTS`` (no-op) and then stamp the latest version.  The tables
    remained at v1 but ``schema_version`` claimed v3.  Review Fourteen
    found one such DB in the wild.  Subsequent init_schema calls must
    repair the schema regardless of the stamped version.
    """

    def test_stuck_v3_version_with_v1_tables_repairs(self, tmp_path: Path) -> None:
        db = tmp_path / "stuck.db"
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE schema_version ("
            "  version INTEGER PRIMARY KEY, applied_at REAL NOT NULL"
            ")"
        )
        conn.execute("INSERT INTO schema_version VALUES (3, 1.0)")
        conn.execute(
            "CREATE TABLE agents ("
            "  agent_id TEXT PRIMARY KEY, parent_id TEXT,"
            "  worktree_root TEXT NOT NULL, pid INTEGER,"
            "  started_at REAL NOT NULL, last_heartbeat REAL NOT NULL,"
            "  status TEXT DEFAULT 'active'"
            ")"
        )
        conn.execute(
            "CREATE TABLE document_locks ("
            "  document_path TEXT PRIMARY KEY, locked_by TEXT NOT NULL,"
            "  locked_at REAL NOT NULL, lock_ttl REAL DEFAULT 300.0,"
            "  lock_type TEXT DEFAULT 'exclusive', worktree_root TEXT"
            ")"
        )
        conn.commit()
        conn.close()

        conn = _create_connection(db)
        init_schema(conn)
        conn.commit()

        assert "raw_ide_id" in _cols(conn, "agents")
        assert "region_start" in _cols(conn, "document_locks")

    def test_repair_idempotent(self, tmp_path: Path) -> None:
        """Running init_schema twice on a repaired DB must be safe."""
        db = tmp_path / "repaired.db"
        conn = _create_connection(db)
        init_schema(conn)
        conn.commit()
        init_schema(conn)  # second call must not raise or duplicate anything
        conn.commit()

        # Exactly one schema_version row at the current version
        rows = conn.execute("SELECT version FROM schema_version").fetchall()
        assert len(rows) == 1
        assert rows[0]["version"] == _CURRENT_SCHEMA_VERSION


class TestScopeColumnMigration:
    """Review Nineteen: legacy DBs missing the `scope` column in agent_responsibilities.

    Migration v6 was a no-op (`lambda conn: None`) because the column was
    added via `CREATE TABLE IF NOT EXISTS`. Existing tables never got the
    column, causing `acquire_lock` to fail with "no such column: scope".
    """

    def test_legacy_db_without_scope_column_gets_migrated(self, tmp_path: Path) -> None:
        db = tmp_path / "no_scope.db"
        conn = sqlite3.connect(db)
        # Create agent_responsibilities exactly as it existed before scope
        conn.execute("""
            CREATE TABLE agent_responsibilities (
                agent_id TEXT PRIMARY KEY,
                graph_agent_id TEXT,
                role TEXT,
                model TEXT,
                responsibilities TEXT,
                current_task TEXT,
                updated_at REAL NOT NULL
            )
        """)
        conn.commit()
        conn.close()

        conn = _create_connection(db)
        init_schema(conn)
        conn.commit()

        assert "scope" in _cols(conn, "agent_responsibilities")

    def test_scope_migration_idempotent(self, tmp_path: Path) -> None:
        db = tmp_path / "scope_repaired.db"
        conn = _create_connection(db)
        init_schema(conn)
        conn.commit()
        assert "scope" in _cols(conn, "agent_responsibilities")

        init_schema(conn)  # second call must not raise
        conn.commit()
        assert "scope" in _cols(conn, "agent_responsibilities")


class TestFreshInstall:
    def test_fresh_db_creates_all_tables_and_columns(self, tmp_path: Path) -> None:
        db = tmp_path / "fresh.db"
        conn = _create_connection(db)
        init_schema(conn)
        conn.commit()

        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        required = {
            "agents", "lineage", "document_locks", "lock_conflicts",
            "change_notifications",
            "agent_responsibilities", "file_ownership", "assessment_results",
            "schema_version",
        }
        assert required <= tables

        # Columns added in later versions must be present on a fresh install
        assert "raw_ide_id" in _cols(conn, "agents")
        assert "region_start" in _cols(conn, "document_locks")
        assert "region_end" in _cols(conn, "document_locks")

    def test_fresh_db_has_no_vestigial_claude_agent_id(self, tmp_path: Path) -> None:
        """Regression test for T0.4: before the fix, init_schema on a fresh DB
        created `agents` with `raw_ide_id` (from _SCHEMAS), then replayed v3
        migration which unconditionally added `claude_agent_id`, then v21
        early-returned because `raw_ide_id` already existed — leaving a
        vestigial `claude_agent_id NULL` column. The fix: v3 skips when
        either `claude_agent_id` or `raw_ide_id` already exists.
        """
        db = tmp_path / "fresh.db"
        conn = _create_connection(db)
        init_schema(conn)
        conn.commit()

        agent_cols = _cols(conn, "agents")
        assert "raw_ide_id" in agent_cols
        assert "claude_agent_id" not in agent_cols, (
            f"v3 migration re-introduced vestigial claude_agent_id column. "
            f"agents columns: {agent_cols}"
        )

    def test_legacy_db_still_migrates_through_v3_and_v21(self, tmp_path: Path) -> None:
        """A DB that genuinely predates v3 (no raw_ide_id, no claude_agent_id)
        must still pick up claude_agent_id via v3, then have it renamed to
        raw_ide_id by v21. Ensures the v3 guard didn't break legacy DBs."""
        db = tmp_path / "legacy.db"
        conn = sqlite3.connect(db)
        # Pre-v3 agents table (no raw_ide_id, no claude_agent_id)
        conn.execute(
            "CREATE TABLE agents ("
            "  agent_id TEXT PRIMARY KEY, parent_id TEXT,"
            "  worktree_root TEXT NOT NULL, pid INTEGER,"
            "  started_at REAL NOT NULL, last_heartbeat REAL NOT NULL,"
            "  status TEXT DEFAULT 'active'"
            ")"
        )
        conn.execute(
            "INSERT INTO agents (agent_id, worktree_root, started_at, last_heartbeat) "
            "VALUES (?, ?, ?, ?)",
            ("hub.legacy.1", "/tmp", 1.0, 1.0),
        )
        conn.commit()
        conn.close()

        conn = _create_connection(db)
        init_schema(conn)
        conn.commit()

        agent_cols = _cols(conn, "agents")
        assert "raw_ide_id" in agent_cols
        assert "claude_agent_id" not in agent_cols
        # Existing row preserved
        rows = conn.execute(
            "SELECT agent_id FROM agents WHERE agent_id = ?",
            ("hub.legacy.1",),
        ).fetchall()
        assert len(rows) == 1

    def test_fresh_db_passes_agent_tree_query(self, tmp_path: Path) -> None:
        """Review Fourteen: ``agent-tree`` errored with 'no such column: region_start'.

        Reproduce the exact SELECT issued by ``get_agent_tree_tool`` to
        confirm a fresh schema supports it.
        """
        db = tmp_path / "fresh.db"
        conn = _create_connection(db)
        init_schema(conn)
        conn.commit()

        # Must not raise "no such column: region_start"
        conn.execute(
            "SELECT document_path, lock_type, region_start, region_end "
            "FROM document_locks WHERE locked_by = ? AND locked_at + lock_ttl > ?",
            ("hub.any", 0.0),
        ).fetchall()


class TestStatusCheckTriggers:
    """T4.2 / T4.5: BEFORE INSERT/UPDATE triggers reject invalid enum
    values on ``tasks.status`` and ``pending_tasks.status``. Installed
    by schema migration v26.
    """

    def test_tasks_status_trigger_rejects_bad_value_on_update(self, tmp_path):
        db = tmp_path / "t.db"
        conn = sqlite3.connect(db)
        init_schema(conn)
        # Register a prerequisite agent row + a task.
        conn.execute(
            "INSERT INTO agents (agent_id, status, last_heartbeat, worktree_root, started_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("p.1", "active", 0.0, "/tmp", 0.0),
        )
        conn.execute(
            "INSERT INTO tasks (id, parent_agent_id, description, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("t1", "p.1", "d", "pending", 0.0, 0.0),
        )
        conn.commit()
        # Bad transition must be aborted by the trigger.
        with pytest.raises(sqlite3.IntegrityError, match="tasks.status"):
            conn.execute("UPDATE tasks SET status = ? WHERE id = ?",
                         ("not_a_state", "t1"))
        # Valid transition still works.
        conn.execute("UPDATE tasks SET status = ? WHERE id = ?",
                     ("in_progress", "t1"))
        conn.commit()
        row = conn.execute("SELECT status FROM tasks WHERE id = ?", ("t1",)).fetchone()
        assert row[0] == "in_progress"

    def test_tasks_status_trigger_rejects_bad_value_on_insert(self, tmp_path):
        db = tmp_path / "t.db"
        conn = sqlite3.connect(db)
        init_schema(conn)
        conn.execute(
            "INSERT INTO agents (agent_id, status, last_heartbeat, worktree_root, started_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("p.1", "active", 0.0, "/tmp", 0.0),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError, match="tasks.status"):
            conn.execute(
                "INSERT INTO tasks (id, parent_agent_id, description, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("t1", "p.1", "d", "garbage", 0.0, 0.0),
            )

    def test_pending_tasks_status_trigger(self, tmp_path):
        db = tmp_path / "t.db"
        conn = sqlite3.connect(db)
        init_schema(conn)
        # Valid insert.
        conn.execute(
            "INSERT INTO pending_tasks (task_id, scope_id, status, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("s1", "p.1", "pending", 0.0),
        )
        conn.commit()
        # Invalid update blocked.
        with pytest.raises(sqlite3.IntegrityError, match="pending_tasks.status"):
            conn.execute(
                "UPDATE pending_tasks SET status = ? WHERE task_id = ?",
                ("bogus", "s1"),
            )
        # Valid transition still works.
        conn.execute(
            "UPDATE pending_tasks SET status = ? WHERE task_id = ?",
            ("registered", "s1"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT status FROM pending_tasks WHERE task_id = ?", ("s1",),
        ).fetchone()
        assert row[0] == "registered"

    def test_triggers_are_installed_on_fresh_db(self, tmp_path):
        """CREATE TRIGGER IF NOT EXISTS means fresh DBs get the triggers
        via init_schema; verify via sqlite_master that the four
        triggers are present after one-shot init."""
        db = tmp_path / "t.db"
        conn = sqlite3.connect(db)
        init_schema(conn)
        names = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            )
        }
        assert "trg_tasks_status_check_insert" in names
        assert "trg_tasks_status_check_update" in names
        assert "trg_pending_tasks_status_check_insert" in names
        assert "trg_pending_tasks_status_check_update" in names


class TestSchemaV27Cleanup:
    """v27 cleanup bundle: T0.4 tail / T1.4 / T1.7 / T4.6.

    Migration ``_migrate_v26_to_v27`` performs four idempotent steps:

    1. ``ALTER TABLE agents DROP COLUMN claude_agent_id`` if the
       vestigial column survived from a pre-T0.4-fix DB.
    2. Defence-in-depth ``UNIQUE`` index on
       ``document_locks(document_path, locked_by,
       COALESCE(region_start, -1), COALESCE(region_end, -1))`` after
       pruning duplicates.
    3. Defence-in-depth ``UNIQUE(task_id, attempt)`` on
       ``task_failures`` after pruning duplicates.
    4. ``DROP INDEX idx_locks_path`` (redundant with the partial
       UNIQUE index + ``idx_locks_locked_by``).
    """

    def test_duplicate_document_locks_pruned(self, tmp_path: Path) -> None:
        """A pre-existing duplicate (document_path, locked_by, region_*)
        row must be collapsed to one (highest id) before the UNIQUE
        index is created."""
        db = tmp_path / "dup_locks.db"
        conn = sqlite3.connect(db)
        # Bootstrap pre-v27 schema: stamp v26, then create the
        # document_locks shape that pre-dates the UNIQUE index. Inject
        # two duplicate rows.
        conn.execute(
            "CREATE TABLE schema_version ("
            "  version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)"
        )
        conn.execute("INSERT INTO schema_version VALUES (26, 1.0)")
        conn.execute(
            "CREATE TABLE document_locks ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  document_path TEXT NOT NULL, locked_by TEXT NOT NULL,"
            "  locked_at REAL NOT NULL, lock_ttl REAL DEFAULT 300.0,"
            "  lock_type TEXT DEFAULT 'exclusive',"
            "  region_start INTEGER, region_end INTEGER,"
            "  worktree_root TEXT)"
        )
        # Two duplicates on (path, agent, NULL, NULL) — both file-level
        conn.execute(
            "INSERT INTO document_locks (document_path, locked_by, locked_at) "
            "VALUES ('/x.py', 'a.1', 100.0)"
        )
        conn.execute(
            "INSERT INTO document_locks (document_path, locked_by, locked_at) "
            "VALUES ('/x.py', 'a.1', 200.0)"
        )
        # And a region-level duplicate pair on (1, 50)
        conn.execute(
            "INSERT INTO document_locks "
            "(document_path, locked_by, locked_at, region_start, region_end) "
            "VALUES ('/y.py', 'a.2', 100.0, 1, 50)"
        )
        conn.execute(
            "INSERT INTO document_locks "
            "(document_path, locked_by, locked_at, region_start, region_end) "
            "VALUES ('/y.py', 'a.2', 200.0, 1, 50)"
        )
        # And a non-duplicate (different region) — must survive
        conn.execute(
            "INSERT INTO document_locks "
            "(document_path, locked_by, locked_at, region_start, region_end) "
            "VALUES ('/y.py', 'a.2', 100.0, 60, 90)"
        )
        conn.commit()
        conn.close()

        conn = _create_connection(db)
        init_schema(conn)
        conn.commit()

        rows = conn.execute(
            "SELECT document_path, locked_by, region_start, region_end, locked_at "
            "FROM document_locks ORDER BY document_path, region_start"
        ).fetchall()
        assert len(rows) == 3, f"expected 3 surviving rows, got {len(rows)}: {[dict(r) for r in rows]}"
        # Highest-id row in each duplicate group is kept (locked_at = 200.0).
        x = [r for r in rows if r["document_path"] == "/x.py"]
        assert len(x) == 1
        assert x[0]["locked_at"] == 200.0
        y_region = [r for r in rows if r["document_path"] == "/y.py" and r["region_start"] == 1]
        assert len(y_region) == 1
        assert y_region[0]["locked_at"] == 200.0

    def test_duplicate_task_failures_pruned(self, tmp_path: Path) -> None:
        db = tmp_path / "dup_tf.db"
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE schema_version ("
            "  version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)"
        )
        conn.execute("INSERT INTO schema_version VALUES (26, 1.0)")
        conn.execute(
            "CREATE TABLE task_failures ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  task_id TEXT NOT NULL, error TEXT,"
            "  attempt INTEGER NOT NULL DEFAULT 1,"
            "  max_retries INTEGER NOT NULL DEFAULT 3,"
            "  first_attempt_at REAL NOT NULL,"
            "  last_attempt_at REAL NOT NULL,"
            "  dead_letter_at REAL, status TEXT DEFAULT 'failed')"
        )
        # Three rows on (task_id='t1', attempt=1)
        for ts in (100.0, 200.0, 300.0):
            conn.execute(
                "INSERT INTO task_failures "
                "(task_id, attempt, first_attempt_at, last_attempt_at) "
                "VALUES (?, ?, ?, ?)",
                ("t1", 1, ts, ts),
            )
        # Distinct attempt — must survive
        conn.execute(
            "INSERT INTO task_failures "
            "(task_id, attempt, first_attempt_at, last_attempt_at) "
            "VALUES (?, ?, ?, ?)",
            ("t1", 2, 400.0, 400.0),
        )
        conn.commit()
        conn.close()

        conn = _create_connection(db)
        init_schema(conn)
        conn.commit()

        rows = conn.execute(
            "SELECT task_id, attempt, last_attempt_at FROM task_failures "
            "ORDER BY attempt"
        ).fetchall()
        assert len(rows) == 2
        assert rows[0]["attempt"] == 1
        assert rows[0]["last_attempt_at"] == 300.0  # MAX(id) wins
        assert rows[1]["attempt"] == 2

    def test_idx_locks_path_dropped(self, tmp_path: Path) -> None:
        """v27 drops the redundant idx_locks_path index."""
        db = tmp_path / "drop_idx.db"
        conn = _create_connection(db)
        init_schema(conn)
        conn.commit()

        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        assert "idx_locks_path" not in names, (
            f"idx_locks_path should have been dropped at v27. "
            f"present indexes: {sorted(names)}"
        )
        # The replacement coverage indexes must be present.
        assert "idx_locks_locked_by" in names
        assert "idx_document_locks_unique" in names

    def test_claude_agent_id_dropped_when_present(self, tmp_path: Path) -> None:
        """Synthesise a pre-v21-style DB carrying claude_agent_id and
        confirm v27 drops it. Pre-v21 had both columns coexisting; v21
        renamed claude_agent_id -> raw_ide_id so post-v21 fresh DBs
        never had the column."""
        db = tmp_path / "vestigial.db"
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE schema_version ("
            "  version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)"
        )
        conn.execute("INSERT INTO schema_version VALUES (26, 1.0)")
        # Build agents with BOTH legacy claude_agent_id and modern
        # raw_ide_id, to mimic a DB stamped before T0.4 fix.
        conn.execute(
            "CREATE TABLE agents ("
            "  agent_id TEXT PRIMARY KEY, parent_id TEXT,"
            "  worktree_root TEXT NOT NULL, pid INTEGER,"
            "  started_at REAL NOT NULL, last_heartbeat REAL NOT NULL,"
            "  status TEXT DEFAULT 'active',"
            "  raw_ide_id TEXT, ide_vendor TEXT, claude_agent_id TEXT,"
            "  stop_requested_at REAL)"
        )
        conn.execute(
            "INSERT INTO agents (agent_id, worktree_root, started_at, last_heartbeat) "
            "VALUES ('hub.preT04', '/tmp', 1.0, 1.0)"
        )
        conn.commit()
        conn.close()

        conn = _create_connection(db)
        init_schema(conn)
        conn.commit()

        cols = _cols(conn, "agents")
        assert "claude_agent_id" not in cols, (
            f"v27 must drop claude_agent_id. agents columns: {cols}"
        )
        # Existing row preserved, raw_ide_id still present.
        assert "raw_ide_id" in cols
        rows = conn.execute(
            "SELECT agent_id FROM agents WHERE agent_id = ?", ("hub.preT04",)
        ).fetchall()
        assert len(rows) == 1

    def test_unique_index_rejects_duplicate_insert(self, tmp_path: Path) -> None:
        """Direct INSERT bypassing acquire_lock must hit the partial
        UNIQUE index and raise IntegrityError. This is the
        defence-in-depth guarantee of T1.4."""
        db = tmp_path / "unique_doc_locks.db"
        conn = _create_connection(db)
        init_schema(conn)
        conn.commit()

        # First insert: fine.
        conn.execute(
            "INSERT INTO document_locks "
            "(document_path, locked_by, locked_at, lock_ttl) "
            "VALUES (?, ?, ?, ?)",
            ("/p.py", "a.1", 100.0, 60.0),
        )
        conn.commit()
        # Same (document_path, locked_by, NULL region) — must be rejected.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO document_locks "
                "(document_path, locked_by, locked_at, lock_ttl) "
                "VALUES (?, ?, ?, ?)",
                ("/p.py", "a.1", 200.0, 60.0),
            )
            conn.commit()
        conn.rollback()
        # Distinct region succeeds.
        conn.execute(
            "INSERT INTO document_locks "
            "(document_path, locked_by, locked_at, lock_ttl, "
            " region_start, region_end) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("/p.py", "a.1", 200.0, 60.0, 1, 50),
        )
        conn.commit()
        # Same region must also be rejected.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO document_locks "
                "(document_path, locked_by, locked_at, lock_ttl, "
                " region_start, region_end) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("/p.py", "a.1", 300.0, 60.0, 1, 50),
            )
            conn.commit()
        conn.rollback()

    def test_task_failures_unique_rejects_duplicate(self, tmp_path: Path) -> None:
        """Direct INSERT bypassing record_task_failure must hit the
        UNIQUE(task_id, attempt) index."""
        db = tmp_path / "unique_tf.db"
        conn = _create_connection(db)
        init_schema(conn)
        conn.commit()
        conn.execute(
            "INSERT INTO task_failures "
            "(task_id, attempt, first_attempt_at, last_attempt_at) "
            "VALUES (?, ?, ?, ?)",
            ("t1", 1, 100.0, 100.0),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO task_failures "
                "(task_id, attempt, first_attempt_at, last_attempt_at) "
                "VALUES (?, ?, ?, ?)",
                ("t1", 1, 200.0, 200.0),
            )
            conn.commit()
        conn.rollback()
        # Different attempt is fine.
        conn.execute(
            "INSERT INTO task_failures "
            "(task_id, attempt, first_attempt_at, last_attempt_at) "
            "VALUES (?, ?, ?, ?)",
            ("t1", 2, 300.0, 300.0),
        )
        conn.commit()
