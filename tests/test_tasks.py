"""Tests for the task registry (tasks.py + core_tasks.py mixin)."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from coordinationhub.core import CoordinationEngine


@pytest.fixture
def engine(tmp_path):
    eng = CoordinationEngine(storage_dir=tmp_path, project_root=tmp_path)
    eng.start()
    yield eng
    eng.close()


@pytest.fixture
def registered_agent(engine):
    engine.register_agent("hub.agent.1")
    return "hub.agent.1"


class TestCreateTask:
    def test_create_task_basic(self, engine, registered_agent):
        result = engine.create_task(
            task_id="task.1", parent_agent_id=registered_agent, description="Do something"
        )
        assert result["created"] is True
        assert result["task_id"] == "task.1"

    def test_create_task_with_priority(self, engine, registered_agent):
        result = engine.create_task(
            task_id="task.1", parent_agent_id=registered_agent, description="Urgent", priority=10
        )
        assert result["priority"] == 10

    def test_create_task_with_dependencies(self, engine, registered_agent):
        engine.create_task(task_id="dep.1", parent_agent_id=registered_agent, description="dep")
        result = engine.create_task(
            task_id="task.1", parent_agent_id=registered_agent, description="main", depends_on=["dep.1"]
        )
        assert result["created"] is True


class TestAssignTask:
    def test_assign_task(self, engine, registered_agent):
        engine.create_task(task_id="task.1", parent_agent_id=registered_agent, description="x")
        result = engine.assign_task("task.1", "hub.worker.1")
        assert result["assigned"] is True
        task = engine.query_tasks(query_type="task", task_id="task.1")
        assert task["task"]["assigned_agent_id"] == "hub.worker.1"


class TestUpdateTaskStatus:
    def test_update_to_in_progress(self, engine, registered_agent):
        engine.create_task(task_id="task.1", parent_agent_id=registered_agent, description="x")
        result = engine.update_task_status("task.1", "in_progress")
        assert result["updated"] is True
        task = engine.query_tasks(query_type="task", task_id="task.1")
        assert task["task"]["status"] == "in_progress"

    def test_update_to_completed_with_summary(self, engine, registered_agent):
        engine.create_task(task_id="task.1", parent_agent_id=registered_agent, description="x")
        result = engine.update_task_status("task.1", "completed", summary="Done!")
        assert result["status"] == "completed"
        task = engine.query_tasks(query_type="task", task_id="task.1")
        assert task["task"]["summary"] == "Done!"

    def test_update_to_failed_records_dlq(self, engine, registered_agent):
        engine.create_task(task_id="task.1", parent_agent_id=registered_agent, description="x")
        result = engine.update_task_status("task.1", "failed", error="Something broke")
        assert result["status"] == "failed"
        history = engine.task_failures(action="history", task_id="task.1")
        assert history["count"] >= 1
        assert "Something broke" in history["history"][0]["error"]

    def test_blocked_by(self, engine, registered_agent):
        engine.create_task(task_id="task.1", parent_agent_id=registered_agent, description="x")
        result = engine.update_task_status("task.1", "blocked", blocked_by="task.0")
        assert result["status"] == "blocked"


class TestQueryTasks:
    def test_query_by_id(self, engine, registered_agent):
        engine.create_task(task_id="task.1", parent_agent_id=registered_agent, description="x")
        result = engine.query_tasks(query_type="task", task_id="task.1")
        assert result["task"]["id"] == "task.1"

    def test_query_child_tasks(self, engine, registered_agent):
        engine.create_task(task_id="task.1", parent_agent_id=registered_agent, description="x")
        result = engine.query_tasks(query_type="child", parent_agent_id=registered_agent)
        assert result["count"] == 1

    def test_query_all(self, engine, registered_agent):
        engine.create_task(task_id="task.1", parent_agent_id=registered_agent, description="a")
        engine.create_task(task_id="task.2", parent_agent_id=registered_agent, description="b")
        result = engine.query_tasks(query_type="all")
        assert result["count"] == 2

    def test_query_subtasks(self, engine, registered_agent):
        engine.create_task(task_id="parent.1", parent_agent_id=registered_agent, description="parent")
        engine.create_subtask(
            task_id="child.1", parent_task_id="parent.1", parent_agent_id=registered_agent,
            description="child"
        )
        result = engine.query_tasks(query_type="subtasks", parent_task_id="parent.1")
        assert result["count"] == 1
        assert result["subtasks"][0]["id"] == "child.1"

    def test_query_tree(self, engine, registered_agent):
        engine.create_task(task_id="root.1", parent_agent_id=registered_agent, description="root")
        engine.create_subtask(
            task_id="child.1", parent_task_id="root.1", parent_agent_id=registered_agent,
            description="child"
        )
        result = engine.query_tasks(query_type="tree", root_task_id="root.1")
        assert result["id"] == "root.1"
        assert len(result["subtasks"]) == 1

    def test_query_missing_task(self, engine):
        result = engine.query_tasks(query_type="task", task_id="nope")
        assert "error" in result


class TestSubtask:
    def test_create_subtask(self, engine, registered_agent):
        engine.create_task(task_id="parent.1", parent_agent_id=registered_agent, description="p")
        result = engine.create_subtask(
            task_id="sub.1", parent_task_id="parent.1", parent_agent_id=registered_agent,
            description="sub", priority=5
        )
        assert result["created"] is True
        assert result["parent_task_id"] == "parent.1"


class TestWaitForTask:
    def test_wait_for_already_completed_task(self, engine, registered_agent):
        engine.create_task(task_id="task.1", parent_agent_id=registered_agent, description="x")
        engine.update_task_status("task.1", "completed")
        result = engine.wait_for_task("task.1", timeout_s=2.0)
        assert result["timed_out"] is False
        assert result["status"] == "completed"

    def test_wait_times_out_for_pending(self, engine, registered_agent):
        engine.create_task(task_id="task.1", parent_agent_id=registered_agent, description="x")
        result = engine.wait_for_task("task.1", timeout_s=0.5, poll_interval_s=0.1)
        assert result["timed_out"] is True

    def test_wait_for_task_not_found(self, engine):
        result = engine.wait_for_task("missing", timeout_s=0.5, poll_interval_s=0.1)
        assert result["timed_out"] is True
        assert result["status"] == "timeout"


class TestGetAvailableTasks:
    def test_no_tasks_available_when_all_claimed(self, engine, registered_agent):
        engine.create_task(task_id="task.1", parent_agent_id=registered_agent, description="x")
        engine.assign_task("task.1", "hub.worker")
        engine.update_task_status("task.1", "in_progress")
        result = engine.get_available_tasks()
        assert result["count"] == 0

    def test_pending_task_is_available(self, engine, registered_agent):
        engine.create_task(task_id="task.1", parent_agent_id=registered_agent, description="x")
        result = engine.get_available_tasks()
        assert result["count"] == 1
        assert result["tasks"][0]["id"] == "task.1"

    def test_task_with_unsatisfied_dependency_not_available(self, engine, registered_agent):
        engine.create_task(task_id="dep.1", parent_agent_id=registered_agent, description="dep")
        engine.create_task(
            task_id="task.1", parent_agent_id=registered_agent, description="main", depends_on=["dep.1"]
        )
        result = engine.get_available_tasks()
        assert result["count"] == 1  # only dep.1
        assert result["tasks"][0]["id"] == "dep.1"

    def test_task_with_satisfied_dependency_is_available(self, engine, registered_agent):
        engine.create_task(task_id="dep.1", parent_agent_id=registered_agent, description="dep")
        engine.create_task(
            task_id="task.1", parent_agent_id=registered_agent, description="main", depends_on=["dep.1"]
        )
        engine.update_task_status("dep.1", "completed")
        result = engine.get_available_tasks()
        assert result["count"] == 1
        assert result["tasks"][0]["id"] == "task.1"

    def test_filter_by_agent_id(self, engine, registered_agent):
        engine.create_task(task_id="task.1", parent_agent_id=registered_agent, description="x")
        engine.assign_task("task.1", "hub.worker.1")
        result = engine.get_available_tasks(agent_id="hub.worker.1")
        assert result["count"] == 1
        result2 = engine.get_available_tasks(agent_id="hub.worker.2")
        assert result2["count"] == 0


class TestDeadLetterQueue:
    def test_failure_history_tracks_attempts(self, engine, registered_agent):
        engine.create_task(task_id="task.1", parent_agent_id=registered_agent, description="x")
        engine.update_task_status("task.1", "failed", error="boom")
        history = engine.task_failures(action="history", task_id="task.1")
        assert history["count"] == 1
        assert "boom" in history["history"][0]["error"]

    def test_task_reaches_dead_letter_after_max_retries(self, engine, registered_agent):
        engine.create_task(task_id="task.1", parent_agent_id=registered_agent, description="x")
        # Need 3 failures to reach dead_letter (default max_retries=3)
        for _ in range(3):
            engine.update_task_status("task.1", "failed", error="boom")
        dlq = engine.task_failures(action="list_dead_letter")
        assert dlq["count"] == 1
        result = engine.task_failures(action="retry", task_id="task.1")
        assert result.get("retried") is True
        task = engine.query_tasks(query_type="task", task_id="task.1")
        assert task["task"]["status"] == "pending"

    def test_retry_requires_task_id(self, engine):
        result = engine.task_failures(action="retry")
        assert "error" in result

    def test_history_requires_task_id(self, engine):
        result = engine.task_failures(action="history")
        assert "error" in result
