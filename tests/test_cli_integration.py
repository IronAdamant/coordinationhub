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
            action="all",
            json_output=True,
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_query_tasks(args)
        data = json.loads(buf.getvalue())
        assert data["count"] >= 1
        assert any(t["id"] == "task.q.1" for t in data["tasks"])


class TestStatusCommand:
    def test_status_json(self, tmp_path):
        args = _args(storage_dir=str(tmp_path), json_output=True)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_status(args)
        data = json.loads(buf.getvalue())
        assert "active_agents" in data
        assert "active_locks" in data
