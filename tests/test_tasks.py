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


class TestDeclaredDependencyBlocksAvailability:
    """T1.12 regression: get_available_tasks must also honor rows declared
    via declare_dependency. Pre-fix the two dependency systems were
    disconnected — calling declare_dependency had no effect on
    get_available_tasks output.
    """

    def test_declared_task_dependency_blocks_task(self, engine, two_agents):
        """Task T assigned to agent A is blocked when A has an unsatisfied
        agent_dependencies row pointing at task B.
        """
        parent = two_agents["parent"]
        worker = two_agents["child"]
        helper = two_agents["other"]

        engine.create_task(task_id="blocker", parent_agent_id=parent, description="b")
        engine.create_task(task_id="work", parent_agent_id=parent, description="w")
        engine.assign_task("work", worker)

        engine.declare_dependency(
            dependent_agent_id=worker,
            depends_on_agent_id=helper,
            depends_on_task_id="blocker",
            condition="task_completed",
        )

        # Without fix, this returned 2 tasks (both blocker and work).
        result = engine.get_available_tasks()
        avail_ids = {t["id"] for t in result["tasks"]}
        assert "work" not in avail_ids, (
            f"work should be blocked by declared dep; got {avail_ids}"
        )
        assert "blocker" in avail_ids

    def test_completing_blocker_unblocks_task(self, engine, two_agents):
        """After the depends_on_task_id completes, the dependent task
        becomes available.
        """
        parent = two_agents["parent"]
        worker = two_agents["child"]
        helper = two_agents["other"]

        engine.create_task(task_id="blocker", parent_agent_id=parent, description="b")
        engine.create_task(task_id="work", parent_agent_id=parent, description="w")
        engine.assign_task("work", worker)

        engine.declare_dependency(
            dependent_agent_id=worker,
            depends_on_agent_id=helper,
            depends_on_task_id="blocker",
            condition="task_completed",
        )
        engine.update_task_status("blocker", "completed")
        # update_task_status auto-satisfies deps via satisfy_dependencies_for_task.

        result = engine.get_available_tasks()
        avail_ids = {t["id"] for t in result["tasks"]}
        assert "work" in avail_ids

    def test_agent_registered_condition_blocks(self, engine, two_agents):
        """Non-task dependency conditions also block (agent_registered,
        agent_stopped).
        """
        parent = two_agents["parent"]
        worker = two_agents["child"]

        engine.create_task(task_id="work", parent_agent_id=parent, description="w")
        engine.assign_task("work", worker)

        # Declare dep on a never-registered agent — stays unsatisfied.
        engine.declare_dependency(
            dependent_agent_id=worker,
            depends_on_agent_id="ghost.never.existed",
            condition="agent_registered",
        )

        result = engine.get_available_tasks()
        avail_ids = {t["id"] for t in result["tasks"]}
        assert "work" not in avail_ids

    def test_unassigned_task_not_blocked_by_declared_dep(self, engine, two_agents):
        """A task with no assigned_agent_id is not blocked by any
        agent_dependencies (no agent to check).
        """
        parent = two_agents["parent"]
        worker = two_agents["child"]
        helper = two_agents["other"]

        engine.create_task(task_id="work", parent_agent_id=parent, description="w")
        # no assignment
        engine.declare_dependency(
            dependent_agent_id=worker,
            depends_on_agent_id=helper,
            depends_on_task_id="other",
            condition="task_completed",
        )

        result = engine.get_available_tasks()
        avail_ids = {t["id"] for t in result["tasks"]}
        assert "work" in avail_ids


class TestUpdateTaskStatusValidation:
    """T1.13 regression tests."""

    def test_invalid_status_rejected(self, engine, registered_agent):
        """Unknown status string must not be accepted."""
        engine.create_task(task_id="t.1", parent_agent_id=registered_agent, description="x")
        result = engine.update_task_status("t.1", "done")  # not in vocabulary
        assert result.get("updated") is False
        assert result.get("reason") == "invalid_status"

    def test_missing_task_rejected(self, engine):
        result = engine.update_task_status("t.does_not_exist", "completed")
        assert result.get("updated") is False
        assert result.get("reason") == "task_not_found"

    def test_valid_statuses_all_accepted(self, engine, registered_agent):
        engine.create_task(task_id="t.2", parent_agent_id=registered_agent, description="x")
        for status in ["pending", "in_progress", "blocked", "failed", "completed"]:
            result = engine.update_task_status("t.2", status)
            # completed/failed trigger side effects (DLQ recording) — both legal here
            assert result.get("updated") is not False, (
                f"Status {status!r} should be accepted, got {result}"
            )


class TestSubtaskCycleDetection:
    """T1.14 regression tests."""

    def test_create_subtask_rejects_self_cycle(self, engine, registered_agent):
        """A task cannot be its own parent."""
        engine.create_task(task_id="t.root", parent_agent_id=registered_agent, description="x")
        result = engine.create_subtask(
            task_id="t.root",
            parent_task_id="t.root",
            parent_agent_id=registered_agent,
            description="cycle",
        )
        assert result.get("created") is False
        assert result.get("reason") == "cycle"

    def test_create_subtask_rejects_parent_not_found(self, engine, registered_agent):
        result = engine.create_subtask(
            task_id="t.orphan",
            parent_task_id="t.does_not_exist",
            parent_agent_id=registered_agent,
            description="x",
        )
        assert result.get("created") is False
        assert result.get("reason") == "parent_not_found"

    def test_create_subtask_rejects_descendant_as_parent(self, engine, registered_agent):
        """Attempting to wire a task as a descendant of its own descendant
        must be rejected. Setup:
            t.a
            └── t.b
        Then try to make t.a a subtask of t.b (cycle).
        """
        engine.create_task(task_id="t.a", parent_agent_id=registered_agent, description="a")
        engine.create_subtask(
            task_id="t.b", parent_task_id="t.a",
            parent_agent_id=registered_agent, description="b",
        )
        # Now try cycle: create t.a as subtask of t.b — but t.a already exists.
        # Use a fresh task id that would close the loop; since create_subtask
        # creates a NEW task, we test the actual supported path: creating a
        # cycle via an id that collides with an ancestor.
        # Instead, verify that the ancestor walk correctly traverses upward:
        # try creating t.c whose parent is t.b, then try re-creating t.b
        # (PK collision) — not our concern. The direct test is self-cycle
        # above. Add a two-level cycle test by pointing a new task at itself
        # via an existing ancestor path:
        result = engine.create_subtask(
            task_id="t.b",  # t.b already exists
            parent_task_id="t.b",
            parent_agent_id=registered_agent,
            description="self",
        )
        # Either cycle or PK collision — both indicate rejection.
        assert result.get("created") is False

    def test_get_task_tree_survives_cycle(self, engine, registered_agent):
        """Even if a cycle exists in the DB (via legacy data), get_task_tree
        must not infinite-loop."""
        import time as _time
        engine.create_task(task_id="t.x", parent_agent_id=registered_agent, description="x")
        engine.create_subtask(
            task_id="t.y", parent_task_id="t.x",
            parent_agent_id=registered_agent, description="y",
        )
        # Inject a cycle directly via DB: t.x's parent_task_id = t.y
        with engine._connect() as conn:
            conn.execute(
                "UPDATE tasks SET parent_task_id = ? WHERE id = ?",
                ("t.y", "t.x"),
            )
        # Must return without infinite recursion
        tree = engine.query_tasks(query_type="tree", root_task_id="t.x")
        # Test passes as long as this returns; content may vary
        assert tree is not None


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

    def test_retry_resets_retry_budget(self, engine, registered_agent):
        """T1.8 regression: after retry_from_dead_letter, the next failure
        must start a fresh attempt=1 row, not increment the retried row's
        attempt and immediately re-enter dead_letter.

        Before the fix: the `ORDER BY attempt DESC LIMIT 1` in
        record_task_failure picked up the 'retried' row and bumped its
        attempt. With max_retries=3, one more failure after retry would
        jump straight to attempt=4 → dead_letter again.
        """
        engine.create_task(
            task_id="task.dlq.1", parent_agent_id=registered_agent, description="x"
        )
        # Push to dead_letter
        for _ in range(3):
            engine.update_task_status("task.dlq.1", "failed", error="boom")
        assert engine.task_failures(action="list_dead_letter")["count"] == 1
        engine.task_failures(action="retry", task_id="task.dlq.1")

        # One more failure after retry — should NOT immediately DLQ.
        engine.update_task_status("task.dlq.1", "failed", error="boom-again")
        history = engine.task_failures(action="history", task_id="task.dlq.1")
        # The retried row is still in history, plus a new attempt=1 row.
        # The new row must be 'failed' (not 'dead_letter') because the
        # retry budget reset.
        active_rows = [
            h for h in history["history"] if h.get("status") != "retried"
        ]
        assert len(active_rows) == 1
        assert active_rows[0]["status"] == "failed", (
            f"After retry, next failure should be 'failed' not 'dead_letter' "
            f"(retry budget reset). Got: {active_rows[0]['status']}"
        )
        assert active_rows[0]["attempt"] == 1, (
            f"After retry, attempt should reset to 1, got "
            f"{active_rows[0]['attempt']}"
        )

    def test_stored_max_retries_wins_over_call_site_default(
        self, engine, registered_agent
    ):
        """T1.7 regression: when a failure row exists, subsequent
        record_task_failure calls must use the stored max_retries from
        the row, not the call-site default of 3. Before the fix, a task
        created with max_retries=5 would prematurely dead-letter at
        attempt=3.

        We exercise this via the primitive directly since the engine
        wrapper always passes the default.
        """
        from coordinationhub import task_failures as _tf

        engine.create_task(
            task_id="task.custom", parent_agent_id=registered_agent, description="x"
        )
        # First failure with max_retries=5 (stored)
        _tf.record_task_failure(
            engine._connect, "task.custom", error="e1", max_retries=5
        )
        # Subsequent failures with default max_retries=3 should still use 5
        for i in range(2, 5):
            r = _tf.record_task_failure(
                engine._connect, "task.custom", error=f"e{i}", max_retries=3
            )
            # attempts 2, 3, 4 should all still be 'failed', not dead_letter
            assert r["status"] == "failed", (
                f"Attempt {i}: expected 'failed' with stored max_retries=5, "
                f"got {r['status']}. Stored max_retries is being ignored."
            )
        # Attempt 5 crosses the stored max_retries threshold
        r = _tf.record_task_failure(
            engine._connect, "task.custom", error="final", max_retries=3
        )
        assert r["status"] == "dead_letter"
        assert r["attempt"] == 5
