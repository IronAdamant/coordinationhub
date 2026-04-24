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
            # T6.30: acquire_lease now returns expires_at on success, None
            # on failure. Truthy-on-success preserves the boolean semantics.
            assert _leases.acquire_lease(conn, "TEST_LEASE", "holder.1", 60.0) is not None
            # Second acquire by same holder goes through the row-exists branch.
            # Before the fix, this hit BEGIN-inside-tx and returned False
            # incorrectly. After the fix, returns expires_at (same holder = refresh).
            assert _leases.acquire_lease(conn, "TEST_LEASE", "holder.1", 60.0) is not None
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
            # T6.30: acquire_lease returns expires_at on success.
            assert _leases.acquire_lease(conn, "T", "holder.1", 10.0) is not None

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
                # T6.30: acquire_lease returns expires_at on success.
                assert _leases.acquire_lease(conn, "T", "holder.1", 10.0) is not None
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


class TestCliRegistryCoverage:
    """T6.19: every subcommand declared by the argparse parser must
    have a matching handler in ``cli._COMMANDS``. Pre-fix the two
    lists could drift silently — a new parser entry would print a
    generic help text at runtime because the dispatch map didn't
    know about it.
    """

    def test_every_parser_subcommand_has_a_handler(self):
        from coordinationhub.cli import _COMMANDS, _get_handler
        from coordinationhub.cli_parser import create_parser

        parser = create_parser()
        # argparse stashes the subparsers on the _subparsers attribute
        # of the main parser. Walk them out.
        subparsers_action = None
        for action in parser._actions:
            if hasattr(action, "choices") and hasattr(action, "dest") and action.dest == "command":
                subparsers_action = action
                break
        assert subparsers_action is not None, "No subparsers action found in parser"

        declared_commands = set(subparsers_action.choices.keys())
        mapped_commands = set(_COMMANDS.keys())

        missing_handler = declared_commands - mapped_commands
        assert not missing_handler, (
            f"Parser declares subcommands with no handler in _COMMANDS: {missing_handler}"
        )

        stale_handler = mapped_commands - declared_commands
        assert not stale_handler, (
            f"_COMMANDS maps subcommands that no longer exist in the parser: {stale_handler}"
        )

        # Also verify every mapped handler resolves to a callable.
        for name, handler_name in _COMMANDS.items():
            handler = _get_handler(handler_name)
            assert callable(handler), f"Handler {handler_name!r} for {name!r} is not callable"


class TestCliExitCodes:
    """T3.16 / T3.17: distinct exit codes for not-found vs error vs
    denied locks. Pre-fix a blanket ``except Exception`` returned 1 for
    anything that crashed and 0 for everything else — including
    ``{"not_found": True}`` return values that weren't exceptions.
    """

    def test_cancel_nonexistent_spawn_exits_3(self, tmp_path, capsys):
        from coordinationhub.cli_spawner import cmd_cancel_spawn

        args = _args(storage_dir=str(tmp_path), spawn_id="ghost.spawn.999")
        rc = cmd_cancel_spawn(args)
        assert rc == 3
        captured = capsys.readouterr()
        # Not-found message goes to stderr so stdout pipes stay clean.
        assert "not found" in captured.err.lower()

    def test_acquire_lock_denied_exits_4(self, tmp_path, capsys):
        from coordinationhub.cli_agents import cmd_register
        from coordinationhub.cli_locks import cmd_acquire_lock

        # Set up two agents; first holds the lock.
        cmd_register(_args(storage_dir=str(tmp_path), agent_id="holder",
                            parent_id=None, raw_ide_id=None))
        cmd_register(_args(storage_dir=str(tmp_path), agent_id="challenger",
                            parent_id=None, raw_ide_id=None))

        common = dict(
            storage_dir=str(tmp_path), document_path="contested.py",
            lock_type="exclusive", ttl=60.0, force=False,
            region_start=None, region_end=None,
            retry=False, max_retries=0, backoff_ms=0, timeout_ms=0,
        )
        rc1 = cmd_acquire_lock(_args(agent_id="holder", **common))
        assert rc1 == 0  # holder gets the lock

        rc2 = cmd_acquire_lock(_args(agent_id="challenger", **common))
        assert rc2 == 4  # challenger is denied → exit 4
        err = capsys.readouterr().err
        assert "FAILED" in err  # denial message on stderr, not stdout


class TestStatusCommand:
    def test_status_json(self, tmp_path):
        args = _args(storage_dir=str(tmp_path), json_output=True)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_status(args)
        data = json.loads(buf.getvalue())
        assert "active_agents" in data
        assert "active_locks" in data


class TestAssessOutputPathValidation:
    """T2.8: cmd_assess --output must reject symlinks and paths outside
    the project root so ``--output /etc/passwd`` can't land a file on
    disk.
    """

    def _make_engine(self, tmp_path):
        storage = tmp_path / "_storage"
        storage.mkdir()
        return CoordinationEngine(
            storage_dir=str(storage), project_root=tmp_path,
        )

    def test_rejects_output_path_outside_project_root(self, tmp_path, capsys):
        from coordinationhub.cli_vis import _validate_assess_output

        eng = self._make_engine(tmp_path)
        eng.start()
        try:
            outside = tmp_path.parent / "escape.md"
            path, error = _validate_assess_output(str(outside), eng)
            assert path is None
            assert "inside the project root" in error
        finally:
            eng.close()

    def test_rejects_symlinked_output_path(self, tmp_path):
        from coordinationhub.cli_vis import _validate_assess_output

        eng = self._make_engine(tmp_path)
        eng.start()
        try:
            real = tmp_path / "real.md"
            real.write_text("")
            link = tmp_path / "link.md"
            link.symlink_to(real)
            path, error = _validate_assess_output(str(link), eng)
            assert path is None
            assert "symlink" in error.lower()
        finally:
            eng.close()

    def test_accepts_normal_path_inside_project(self, tmp_path):
        from coordinationhub.cli_vis import _validate_assess_output

        eng = self._make_engine(tmp_path)
        eng.start()
        try:
            target = tmp_path / "report.md"
            path, error = _validate_assess_output(str(target), eng)
            assert error is None
            assert path == target.resolve()
        finally:
            eng.close()
