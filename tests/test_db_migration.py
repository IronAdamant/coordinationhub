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
