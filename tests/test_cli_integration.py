"""Integration tests for CLI command handlers.

Each test exercises the full stack: argparse Namespace → command function
→ CoordinationEngine → SQLite.  This closes the coverage gap on cli_*.py.
"""

from __future__ import annotations

import io
import json
import tempfile
from contextlib import redirect_stdout
from types import SimpleNamespace

import pytest

from coordinationhub.cli_agents import (
    cmd_register,
    cmd_heartbeat,
    cmd_deregister,
    cmd_list_agents,
)
from coordinationhub.cli_locks import (
    cmd_acquire_lock,
    cmd_release_lock,
    cmd_lock_status,
)
from coordinationhub.cli_tasks import (
    cmd_create_task,
    cmd_assign_task,
    cmd_update_task_status,
    cmd_query_tasks,
)
from coordinationhub.cli_agents import cmd_status
from coordinationhub.core import CoordinationEngine


def _args(**kwargs) -> SimpleNamespace:
    """Build a argparse-like namespace with sensible defaults."""
    defaults = {
        "json_output": False,
        "storage_dir": None,
        "project_root": None,
        "namespace": "hub",
        "include_stale": True,
        "stale_timeout": 600.0,
        "minimal": True,
        "worktree_root": None,
        "graph_agent_id": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestAgentLifecycleCommands:
    def test_register_agent(self, tmp_path):
        args = _args(
            storage_dir=str(tmp_path),
            agent_id="hub.test.1",
            parent_id=None,
            graph_agent_id=None,
            worktree_root=None,
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_register(args)
        assert "Registered: hub.test.1" in buf.getvalue()

    def test_heartbeat_and_list_agents(self, tmp_path):
        # Register first
        args = _args(storage_dir=str(tmp_path), agent_id="hub.beat.1", parent_id=None)
        cmd_register(args)

        # Heartbeat
        args = _args(storage_dir=str(tmp_path), agent_id="hub.beat.1")
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_heartbeat(args)
        assert "hub.beat.1" in buf.getvalue()

        # List agents (JSON)
        args = _args(storage_dir=str(tmp_path), json_output=True, include_stale=False)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_list_agents(args)
        data = json.loads(buf.getvalue())
        assert any(a["agent_id"] == "hub.beat.1" for a in data["agents"])

    def test_deregister_agent(self, tmp_path):
        # Register then deregister
        args = _args(storage_dir=str(tmp_path), agent_id="hub.die.1", parent_id=None)
        cmd_register(args)

        args = _args(storage_dir=str(tmp_path), agent_id="hub.die.1")
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_deregister(args)
        assert "Deregistered: hub.die.1" in buf.getvalue()


class TestLockCommands:
    def test_acquire_and_release_lock(self, tmp_path):
        engine = CoordinationEngine(storage_dir=tmp_path)
        engine.start()
        engine.register_agent("hub.locker")
        engine.close()

        args = _args(
            storage_dir=str(tmp_path),
            agent_id="hub.locker",
            document_path="src/main.py",
            lock_type="exclusive",
            ttl=60,
            force=False,
            region_start=None,
            region_end=None,
            retry=False,
            max_retries=5,
            backoff_ms=100,
            timeout_ms=5000,
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_acquire_lock(args)
        assert "LOCKED: src/main.py" in buf.getvalue()

        args = _args(
            storage_dir=str(tmp_path),
            agent_id="hub.locker",
            document_path="src/main.py",
            region_start=None,
            region_end=None,
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_release_lock(args)
        assert "RELEASED: src/main.py" in buf.getvalue()

    def test_lock_status_json(self, tmp_path):
        engine = CoordinationEngine(storage_dir=tmp_path)
        engine.start()
        engine.register_agent("hub.locker")
        engine.acquire_lock("file.py", "hub.locker", "exclusive", 60)
        engine.close()

        args = _args(
            storage_dir=str(tmp_path),
            document_path="file.py",
            json_output=True,
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_lock_status(args)
        data = json.loads(buf.getvalue())
        assert data["locked"] is True
        assert data["locked_by"] == "hub.locker"


class TestTaskCommands:
    def test_create_task(self, tmp_path):
        engine = CoordinationEngine(storage_dir=tmp_path)
        engine.start()
        engine.register_agent("hub.parent")
        engine.close()

        args = _args(
            storage_dir=str(tmp_path),
            task_id="task.cli.1",
            parent_agent_id="hub.parent",
            description="CLI test task",
            priority=3,
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_create_task(args)
        out = buf.getvalue()
        assert "Task created: task.cli.1" in out
        assert "Priority: 3" in out

    def test_assign_and_update_task(self, tmp_path):
        engine = CoordinationEngine(storage_dir=tmp_path)
        engine.start()
        engine.register_agent("hub.parent")
        engine.create_task("task.cli.2", "hub.parent", "desc")
        engine.close()

        args = _args(
            storage_dir=str(tmp_path),
            task_id="task.cli.2",
            assigned_agent_id="hub.worker",
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_assign_task(args)
        assert "Task assigned: task.cli.2 → hub.worker" in buf.getvalue()

        args = _args(
            storage_dir=str(tmp_path),
            task_id="task.cli.2",
            status="completed",
            summary="All done",
            blocked_by=None,
            error=None,
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_update_task_status(args)
        assert "Task updated: task.cli.2 → completed" in buf.getvalue()

    def test_query_tasks_json(self, tmp_path):
        engine = CoordinationEngine(storage_dir=tmp_path)
        engine.start()
        engine.register_agent("hub.parent")
        engine.create_task("task.q.1", "hub.parent", "query test")
        engine.close()

        args = _args(
            storage_dir=str(tmp_path),
            query_type="all",
            json_output=True,
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_query_tasks(args)
        data = json.loads(buf.getvalue())
        assert data["count"] >= 1
        assert any(t["id"] == "task.q.1" for t in data["tasks"])

    def test_query_tasks_via_argparse_namespace(self, tmp_path):
        """Regression test for T0.1: cmd_query_tasks must read args.query_type,
        not args.action. The test above uses a hand-built SimpleNamespace, which
        masked the bug. This test goes through the real parser to guarantee the
        attribute name matches what argparse produces."""
        from coordinationhub.cli_parser import create_parser

        engine = CoordinationEngine(storage_dir=tmp_path)
        engine.start()
        engine.register_agent("hub.parent")
        engine.create_task("task.q.2", "hub.parent", "argparse test")
        engine.close()

        parser = create_parser()
        args = parser.parse_args([
            "query-tasks", "all",
            "--storage-dir", str(tmp_path),
            "--json",
        ])
        assert hasattr(args, "query_type"), "parser must produce query_type attribute"
        assert args.query_type == "all"
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_query_tasks(args)
        data = json.loads(buf.getvalue())
        assert any(t["id"] == "task.q.2" for t in data["tasks"])


class TestLeaseCommands:
    """Regression tests for T0.2: ha-dashboard, get-leader, leader-status.

    All three called manage_leases(action='get') which required an unused
    agent_id, plus ha-dashboard read a non-existent 'leases' key. The fix
    makes agent_id optional for the 'get' action and reshapes ha-dashboard
    to read the singleton 'leader' key.
    """

    def test_ha_dashboard_runs_without_error(self, tmp_path):
        from coordinationhub.cli_leases import cmd_ha_dashboard

        engine = CoordinationEngine(storage_dir=tmp_path)
        engine.start()
        engine.register_agent("hub.leader.1")
        engine.manage_leases(action="acquire", agent_id="hub.leader.1", ttl=60.0)
        engine.close()

        args = _args(storage_dir=str(tmp_path), json_output=True)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_ha_dashboard(args)
        data = json.loads(buf.getvalue())
        assert data["leader"] is not None
        assert data["leader"]["holder_id"] == "hub.leader.1"

    def test_ha_dashboard_no_leader(self, tmp_path):
        from coordinationhub.cli_leases import cmd_ha_dashboard

        args = _args(storage_dir=str(tmp_path), json_output=False)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_ha_dashboard(args)
        assert "No active coordinator lease" in buf.getvalue()

    def test_get_leader_no_agent_id(self, tmp_path):
        from coordinationhub.cli_leases import cmd_get_leader

        args = _args(storage_dir=str(tmp_path), json_output=True)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_get_leader(args)
        data = json.loads(buf.getvalue())
        assert "leader" in data

    def test_leader_status_no_agent_id(self, tmp_path):
        from coordinationhub.cli_leases import cmd_leader_status

        args = _args(storage_dir=str(tmp_path), json_output=False)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_leader_status(args)
        assert "No active leader" in buf.getvalue()

    def test_manage_leases_get_does_not_require_agent_id(self, tmp_path):
        engine = CoordinationEngine(storage_dir=tmp_path)
        engine.start()
        result = engine.manage_leases(action="get")
        assert "leader" in result
        engine.close()

    def test_manage_leases_acquire_requires_agent_id(self, tmp_path):
        engine = CoordinationEngine(storage_dir=tmp_path)
        engine.start()
        result = engine.manage_leases(action="acquire")
        assert "error" in result
        assert "agent_id" in result["error"]
        engine.close()

    def test_acquire_lease_repeats_on_same_connection(self, tmp_path):
        """Regression test for T0.3: acquire_lease on a connection where the
        row already exists must not wedge the connection.

        Before the fix: bare INSERT raised IntegrityError, which left the
        connection in in_transaction=True. The subsequent BEGIN IMMEDIATE
        raised OperationalError('cannot start a transaction within a
        transaction'), caught by the bare except, returning False forever.
        After the fix: conn.rollback() clears the implicit tx state.
        """
        from coordinationhub import leases as _leases

        engine = CoordinationEngine(storage_dir=tmp_path)
        engine.start()
        engine.register_agent("hub.leader.1")
        engine.register_agent("hub.leader.2")
        try:
            # First acquire creates the row via the fast-path INSERT.
            ok1 = engine.manage_leases(action="acquire", agent_id="hub.leader.1", ttl=60.0)
            assert ok1["acquired"] is True

            # Same holder re-acquires — must NOT wedge.
            ok2 = engine.manage_leases(action="acquire", agent_id="hub.leader.1", ttl=60.0)
            assert ok2["acquired"] is True

            # Different holder attempts acquire while valid lease held — must
            # return clean False (not a wedged OperationalError masking).
            ok3 = engine.manage_leases(action="acquire", agent_id="hub.leader.2", ttl=60.0)
            assert ok3["acquired"] is False
            assert ok3.get("holder") is not None
            assert ok3["holder"]["holder_id"] == "hub.leader.1"

            # Original holder refreshes — must continue to work.
            ok4 = engine.manage_leases(action="refresh", agent_id="hub.leader.1")
            assert ok4["refreshed"] is True
        finally:
            engine.close()

    def test_acquire_lease_connection_clean_after_integrity_error(self, tmp_path):
        """Direct primitive-level test: after a failed INSERT, the connection
        must be usable for BEGIN IMMEDIATE immediately."""
        import sqlite3
        from coordinationhub import leases as _leases

        engine = CoordinationEngine(storage_dir=tmp_path)
        engine.start()
        try:
            conn = engine._connect()
            # First acquire populates the row.
            assert _leases.acquire_lease(conn, "TEST_LEASE", "holder.1", 60.0) is True
            # Second acquire by same holder goes through the row-exists branch.
            # Before the fix, this hit BEGIN-inside-tx and returned False
            # incorrectly. After the fix, returns True (same holder = refresh).
            assert _leases.acquire_lease(conn, "TEST_LEASE", "holder.1", 60.0) is True
            # Connection state is clean.
            assert conn.in_transaction is False
        finally:
            engine.close()

    def test_acquire_lease_timestamp_sampled_after_begin_wait(self, tmp_path):
        """T1.5 regression: when BEGIN IMMEDIATE has to wait for another
        writer, acquired_at must be sampled AFTER the wait completes, not
        before. Otherwise the stored expires_at is earlier than the
        caller's effective window.

        Technique: patch time.time() to record every call. Under contention
        we expect two samples (one at entry, one post-BEGIN). The UPDATE's
        acquired_at must equal the second sample, not the first.
        """
        import sqlite3
        import time as _time
        from coordinationhub import leases as _leases

        engine = CoordinationEngine(storage_dir=tmp_path)
        engine.start()
        try:
            conn = engine._connect()
            # Seed the row so acquire goes through the BEGIN IMMEDIATE branch.
            assert _leases.acquire_lease(conn, "T", "holder.1", 10.0) is True

            # Record successive time.time() samples; monkey-patch the module.
            samples: list[float] = []
            real_time = _time.time

            def fake_time():
                t = real_time()
                samples.append(t)
                return t

            _leases.time.time = fake_time
            try:
                # Same holder re-acquires — goes through the BEGIN-IMMEDIATE
                # path with fresh sampling.
                assert _leases.acquire_lease(conn, "T", "holder.1", 10.0) is True
            finally:
                _leases.time.time = real_time

            # There should be at least 2 samples: one before BEGIN IMMEDIATE
            # (for the fast-path INSERT that fails) and one after BEGIN
            # IMMEDIATE (the T1.5 re-sample). The stored acquired_at should
            # equal the second sample.
            assert len(samples) >= 2, f"Expected ≥2 time samples, got {samples}"

            stored = conn.execute(
                "SELECT acquired_at FROM coordinator_leases WHERE lease_name = 'T'"
            ).fetchone()["acquired_at"]
            # acquired_at should match the last sample (post-BEGIN), not the first.
            assert abs(stored - samples[-1]) < 0.001, (
                f"Stored acquired_at {stored} should match post-BEGIN sample "
                f"{samples[-1]}, not pre-BEGIN {samples[0]}. T1.5 regression."
            )
        finally:
            engine.close()


class TestStatusCommand:
    def test_status_json(self, tmp_path):
        args = _args(storage_dir=str(tmp_path), json_output=True)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_status(args)
        data = json.loads(buf.getvalue())
        assert "active_agents" in data
        assert "active_locks" in data
