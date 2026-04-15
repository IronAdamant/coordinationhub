"""Monkey-patch connection row_factory to raise if rows are accessed after close.

This guarantees that every module eagerly serializes rows to dict before the
connection context manager exits, eliminating the ``sqlite3.ProgrammingError``
regression class identified in Phase 3 of the multi-agent consolidation plan.
"""

from __future__ import annotations

import sqlite3
import weakref
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest


class _SafeConnection:
    """Wraps a raw sqlite3.Connection so that rows know when it is closed."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._closed = False
        conn.row_factory = self._make_row_factory()

    def _make_row_factory(self):
        conn_ref = weakref.ref(self)

        def row_factory(cursor, row):
            return _SafeRow(cursor, row, conn_ref)

        return row_factory

    def close(self) -> None:
        self._closed = True
        self._conn.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)

    def __enter__(self) -> sqlite3.Connection:
        return self  # type: ignore[return-value]

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


class _SafeRow:
    """Mapping that looks like sqlite3.Row but raises if accessed post-close."""

    __slots__ = ("_mapping", "_list", "_conn_ref")

    def __init__(self, cursor, row, conn_ref):
        self._mapping = {desc[0]: value for desc, value in zip(cursor.description, row)}
        self._list = list(row)
        self._conn_ref = conn_ref

    def _check(self) -> None:
        conn = self._conn_ref()
        if conn is None or conn._closed:
            raise sqlite3.ProgrammingError("sqlite3.Row accessed after connection closed")

    def __getitem__(self, key: Any) -> Any:
        self._check()
        if isinstance(key, int):
            return self._list[key]
        return self._mapping[key]

    def keys(self):
        self._check()
        return self._mapping.keys()

    def __iter__(self):
        self._check()
        return iter(self._mapping)

    def __len__(self) -> int:
        self._check()
        return len(self._mapping)

    def __contains__(self, key: object) -> bool:
        self._check()
        return key in self._mapping


@contextmanager
def _safe_connect(path: str) -> Any:
    raw = sqlite3.connect(path)
    safe = _SafeConnection(raw)
    try:
        yield safe
    finally:
        safe._conn.commit()
        safe.close()


# ---------------------------------------------------------------------------
# Schema bootstrap (minimal subset so all modules can operate).
# ---------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE agents (
    agent_id TEXT PRIMARY KEY,
    status TEXT,
    parent_id TEXT,
    worktree_root TEXT,
    pid INTEGER,
    started_at REAL,
    last_heartbeat REAL,
    stop_requested_at REAL,
    claude_agent_id TEXT
);

CREATE TABLE agent_responsibilities (
    agent_id TEXT PRIMARY KEY,
    graph_agent_id TEXT,
    role TEXT,
    model TEXT,
    responsibilities TEXT,
    current_task TEXT,
    scope TEXT,
    updated_at REAL
);

CREATE TABLE document_locks (
    document_path TEXT PRIMARY KEY,
    locked_by TEXT,
    locked_at REAL,
    lock_ttl REAL,
    lock_type TEXT DEFAULT 'exclusive',
    region_start INTEGER,
    region_end INTEGER
);

CREATE TABLE change_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_path TEXT,
    change_type TEXT,
    agent_id TEXT,
    worktree_root TEXT,
    created_at REAL
);

CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_agent_id TEXT,
    to_agent_id TEXT,
    message_type TEXT,
    payload_json TEXT,
    created_at REAL,
    read_at REAL
);

CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    parent_agent_id TEXT,
    parent_task_id TEXT,
    description TEXT,
    assigned_agent_id TEXT,
    status TEXT DEFAULT 'pending',
    depends_on TEXT,
    priority INTEGER DEFAULT 0,
    summary TEXT,
    blocked_by TEXT,
    created_at REAL,
    updated_at REAL
);

CREATE TABLE pending_tasks (
    task_id TEXT PRIMARY KEY,
    scope_id TEXT,
    subagent_type TEXT,
    description TEXT,
    prompt TEXT,
    created_at REAL,
    consumed_at REAL,
    status TEXT,
    source TEXT
);

CREATE TABLE task_failures (
    task_id TEXT,
    error TEXT,
    attempt INTEGER,
    max_retries INTEGER,
    first_attempt_at REAL,
    last_attempt_at REAL,
    dead_letter_at REAL,
    status TEXT,
    PRIMARY KEY (task_id, attempt)
);

CREATE TABLE work_intent (
    agent_id TEXT PRIMARY KEY,
    document_path TEXT,
    intent TEXT,
    declared_at REAL,
    ttl REAL
);

CREATE TABLE file_ownership (
    document_path TEXT PRIMARY KEY,
    assigned_agent_id TEXT,
    task_description TEXT,
    assigned_at REAL
);

CREATE TABLE lineage (
    parent_id TEXT,
    child_id TEXT PRIMARY KEY
);

CREATE TABLE descendant_registry (
    ancestor_id TEXT,
    descendant_id TEXT,
    depth INTEGER,
    registered_at REAL,
    PRIMARY KEY (ancestor_id, descendant_id)
);

CREATE TABLE agent_dependencies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dependent_agent_id TEXT,
    depends_on_agent_id TEXT,
    condition TEXT DEFAULT 'task_completed',
    depends_on_task_id TEXT,
    satisfied INTEGER DEFAULT 0,
    created_at REAL,
    satisfied_at REAL
);

CREATE TABLE broadcasts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_agent_id TEXT,
    document_path TEXT,
    message TEXT,
    created_at REAL,
    ttl REAL,
    expires_at REAL,
    expected_count INTEGER
);

CREATE TABLE broadcast_acks (
    broadcast_id INTEGER,
    agent_id TEXT,
    acknowledged_at REAL,
    PRIMARY KEY (broadcast_id, agent_id)
);

CREATE TABLE handoffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_agent_id TEXT,
    to_agents TEXT,
    document_path TEXT,
    handoff_type TEXT,
    status TEXT,
    created_at REAL,
    acknowledged_at REAL,
    completed_at REAL
);

CREATE TABLE handoff_acks (
    handoff_id INTEGER,
    agent_id TEXT,
    acknowledged_at REAL,
    PRIMARY KEY (handoff_id, agent_id)
);

CREATE TABLE coordinator_leases (
    lease_name TEXT PRIMARY KEY,
    holder_id TEXT,
    acquired_at REAL,
    ttl REAL,
    expires_at REAL
);

CREATE TABLE coordination_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT,
    agent_id TEXT,
    payload TEXT,
    created_at REAL
);
"""


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    path = str(tmp_path / "test.db")
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()
    return path


# ---------------------------------------------------------------------------
# Representative exercises for every module touched in Phase 3.
# ---------------------------------------------------------------------------


def test_agent_registry(db_path: str) -> None:
    from coordinationhub import agent_registry

    agent_registry.register_agent(lambda: _safe_connect(db_path), "a1", worktree_root="/tmp")
    agent_registry.register_agent(lambda: _safe_connect(db_path), "a2", worktree_root="/tmp", parent_id="a1")
    agent_registry.heartbeat(lambda: _safe_connect(db_path), "a1")
    assert agent_registry.get_descendants_status(lambda: _safe_connect(db_path), "a1")
    agent_registry.deregister_agent(lambda: _safe_connect(db_path), "a2")


def test_agent_status(db_path: str) -> None:
    from coordinationhub import agent_registry, agent_status

    agent_registry.register_agent(lambda: _safe_connect(db_path), "hub.1", worktree_root="/tmp")
    agent_registry.register_agent(lambda: _safe_connect(db_path), "hub.1.0", worktree_root="/tmp", parent_id="hub.1")
    agent_status.update_agent_status_tool(lambda: _safe_connect(db_path), "hub.1", current_task="testing")
    result = agent_status.get_agent_status_tool(
        lambda: _safe_connect(db_path), "hub.1",
        lineage_fn=lambda aid: agent_registry.get_lineage(lambda: _safe_connect(db_path), aid),
    )
    assert result["agent_id"] == "hub.1"
    tree = agent_status.get_agent_tree_tool(lambda: _safe_connect(db_path), agent_id="hub.1")
    assert "root" in tree
    fmap = agent_status.get_file_agent_map_tool(lambda: _safe_connect(db_path))
    assert "files" in fmap


def test_broadcasts(db_path: str) -> None:
    from coordinationhub import broadcasts

    broadcasts.record_broadcast(lambda: _safe_connect(db_path), "a1", None, "hello", ttl=60.0, expected_count=1)
    status = broadcasts.get_broadcast_status(lambda: _safe_connect(db_path), broadcast_id=1)
    assert status["found"] is True
    assert broadcasts.get_broadcasts(lambda: _safe_connect(db_path), from_agent_id="a1")


def test_context_bundle(db_path: str) -> None:
    from coordinationhub import agent_registry, context

    agent_registry.register_agent(lambda: _safe_connect(db_path), "a1", worktree_root="/tmp")
    bundle = context.build_context_bundle(
        connect_fn=lambda: _safe_connect(db_path),
        agent_id="a1",
        parent_id=None,
        project_root="/tmp",
        graph_getter=lambda: None,
        list_agents_fn=lambda _connect, active_only=True, stale_timeout=600.0: [
            {"agent_id": "a1", "status": "active", "last_heartbeat": 0.0}
        ],
        default_port=8080,
    )
    assert bundle["agent_id"] == "a1"


def test_dependencies(db_path: str) -> None:
    from coordinationhub import dependencies

    dependencies.declare_dependency(lambda: _safe_connect(db_path), "a2", "a1")
    unsatisfied = dependencies.check_dependencies(lambda: _safe_connect(db_path), "a2")
    assert len(unsatisfied) == 1


def test_handoffs(db_path: str) -> None:
    from coordinationhub import handoffs

    handoffs.record_handoff(lambda: _safe_connect(db_path), "a1", ["a2"], document_path=None, handoff_type="scope_transfer")
    assert handoffs.get_handoffs(lambda: _safe_connect(db_path))


def test_leases(db_path: str) -> None:
    from coordinationhub import leases

    with _safe_connect(db_path) as conn:
        assert leases.acquire_lease(conn, "COORDINATOR_LEADER", "a1", 10.0) is True
        holder = leases.get_lease_holder(conn, "COORDINATOR_LEADER")
        assert holder is not None
        assert leases.refresh_lease(conn, "COORDINATOR_LEADER", "a1") is True
        assert leases.is_lease_expired(conn, "COORDINATOR_LEADER") is False
        assert leases.release_lease(conn, "COORDINATOR_LEADER", "a1") is True
        assert leases.claim_leadership(conn, "COORDINATOR_LEADER", "a1", 10.0) is True


def test_messages(db_path: str) -> None:
    from coordinationhub import messages

    messages.send_message(lambda: _safe_connect(db_path), "a1", "a2", "ping", {"x": 1})
    msgs = messages.get_messages(lambda: _safe_connect(db_path), "a2")
    assert len(msgs) == 1
    assert msgs[0]["payload"]["x"] == 1
    assert messages.count_unread(lambda: _safe_connect(db_path), "a2") == 1
    messages.mark_messages_read(lambda: _safe_connect(db_path), "a2")


def test_notifications(db_path: str) -> None:
    from coordinationhub import notifications

    notifications.notify_change(lambda: _safe_connect(db_path), "/f.py", "modified", "a1")
    result = notifications.get_notifications(lambda: _safe_connect(db_path))
    assert len(result["notifications"]) == 1
    notifications.prune_notifications(lambda: _safe_connect(db_path), max_age_seconds=1.0)


def test_scan(db_path: str, tmp_path: Path) -> None:
    from coordinationhub import scan, agent_status

    scan.scan_project_tool(lambda: _safe_connect(db_path), project_root=tmp_path, extensions=[".py"])
    fmap = agent_status.get_file_agent_map_tool(lambda: _safe_connect(db_path))
    assert "files" in fmap


def test_spawner(db_path: str) -> None:
    from coordinationhub import spawner

    spawner.stash_pending_spawn(lambda: _safe_connect(db_path), "s1", "p1", "Explore")
    consumed = spawner.consume_pending_spawn(lambda: _safe_connect(db_path), "p1", "Explore")
    assert consumed is not None
    spawner.stash_pending_spawn(lambda: _safe_connect(db_path), "s2", "p1", "Explore")
    reported = spawner.report_subagent_spawned(lambda: _safe_connect(db_path), "p1", "Explore", "c1")
    assert reported["reported"] is True


def test_task_failures(db_path: str) -> None:
    from coordinationhub import task_failures, tasks

    tasks.create_task(lambda: _safe_connect(db_path), "t1", "parent", "do it")
    task_failures.record_task_failure(lambda: _safe_connect(db_path), "t1", error="oops")
    history = task_failures.get_task_failure_history(lambda: _safe_connect(db_path), "t1")
    assert len(history) == 1
    dlq = task_failures.get_dead_letter_tasks(lambda: _safe_connect(db_path))
    # not dead-letter yet (attempt 1 < max_retries 3)
    assert not dlq


def test_tasks(db_path: str) -> None:
    from coordinationhub import tasks

    tasks.create_task(lambda: _safe_connect(db_path), "t1", "parent", "job")
    assert tasks.get_task(lambda: _safe_connect(db_path), "t1") is not None
    assert tasks.get_all_tasks(lambda: _safe_connect(db_path))


def test_work_intent(db_path: str) -> None:
    from coordinationhub import work_intent

    work_intent.upsert_intent(lambda: _safe_connect(db_path), "a1", "/x.py", "edit")
    intents = work_intent.get_live_intents(lambda: _safe_connect(db_path))
    assert len(intents) == 1
    conflicts = work_intent.check_intent_conflict(lambda: _safe_connect(db_path), "/x.py", exclude_agent_id="a1")
    assert not conflicts
    work_intent.clear_intent(lambda: _safe_connect(db_path), "a1")
