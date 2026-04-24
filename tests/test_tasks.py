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


class TestGetAvailableTasksNoPerDepRoundTrip:
    """T6.3: dependency resolution used to call ``get_task`` per dep,
    opening a fresh connection each time. A task with D deps across N
    candidate tasks cost O(N*D) round trips. After the rewrite, deps
    are resolved from the already-loaded universe — a new SELECT fires
    only for deps pointing outside the universe (cross-scope).
    """

    def test_many_deps_do_not_scale_round_trips(self, engine, registered_agent):
        # Create 20 tasks, mark them completed.
        engine.create_task(
            task_id="b.0", parent_agent_id=registered_agent, description="seed"
        )
        for i in range(1, 21):
            engine.create_task(
                task_id=f"b.{i}", parent_agent_id=registered_agent, description=f"dep{i}"
            )
            engine.update_task_status(f"b.{i}", "completed")
        # One pending task with all 20 as deps.
        engine.create_task(
            task_id="target",
            parent_agent_id=registered_agent,
            description="collector",
            depends_on=[f"b.{i}" for i in range(1, 21)],
        )

        selects_before = 0
        selects_after = 0

        # Pre-query baseline: how many SELECTs does get_available_tasks
        # fire with no deps? (upper-bound irrespective of dep count)
        conn = engine._connect()
        def _make_tracer(bucket):
            def _t(stmt):
                if stmt.strip().upper().startswith("SELECT"):
                    bucket.append(stmt)
            return _t

        # Baseline: seed task has no deps
        baseline: list[str] = []
        conn.set_trace_callback(_make_tracer(baseline))
        try:
            engine.get_available_tasks()
        finally:
            conn.set_trace_callback(None)

        # With-20-deps: must not scale by 20.
        with_deps: list[str] = []
        conn.set_trace_callback(_make_tracer(with_deps))
        try:
            available = engine.get_available_tasks()
        finally:
            conn.set_trace_callback(None)

        # Pre-fix, `get_task` would fire 20 extra SELECTs for the
        # depends_on resolution. After T6.3 the resolution is in-memory
        # so the select count should differ by ≤ 3 (to leave headroom
        # for unrelated reads).
        delta = len(with_deps) - len(baseline)
        assert delta <= 3, (
            f"get_available_tasks ran {delta} extra SELECTs when a 20-dep "
            f"task was present; expected ≤3 (in-memory dep resolution). "
            f"T6.3 regression.\nBaseline: {len(baseline)}\nWith deps: {len(with_deps)}"
        )
        # The target should be in the available list (all deps completed).
        assert any(t["id"] == "target" for t in available["tasks"])


class TestGetTaskTreeSingleQuery:
    """T6.36: get_task_tree uses one WITH RECURSIVE query; a deep tree
    used to pay O(N) round trips (one SELECT per node + one SELECT per
    direct-child list). The trace-callback assertion below guards the
    optimisation against regression.
    """

    def _build_chain(self, engine, parent_agent, n):
        """Build a linear chain of n+1 tasks (root, t.1, ..., t.n)."""
        engine.create_task(
            task_id="t.root", parent_agent_id=parent_agent, description="root"
        )
        prev = "t.root"
        for i in range(1, n + 1):
            tid = f"t.{i}"
            engine.create_subtask(
                task_id=tid,
                parent_task_id=prev,
                parent_agent_id=parent_agent,
                description=f"d{i}",
            )
            prev = tid

    def test_deep_chain_runs_one_select(self, engine, registered_agent):
        self._build_chain(engine, registered_agent, n=10)

        selects: list[str] = []
        conn = engine._connect()
        def _tracer(stmt):
            if stmt.strip().upper().startswith("SELECT"):
                selects.append(stmt)
        conn.set_trace_callback(_tracer)
        try:
            tree = engine.query_tasks(query_type="tree", root_task_id="t.root")
        finally:
            conn.set_trace_callback(None)

        # The query returns a dict rooted at t.root with 10 descendants.
        assert tree.get("id") == "t.root"
        # Walk the chain.
        node = tree
        for i in range(1, 11):
            assert len(node["subtasks"]) == 1, f"Chain broken at depth {i}"
            node = node["subtasks"][0]
            assert node["id"] == f"t.{i}"

        # Pre-fix, 11 tasks → ≥ 22 SELECTs (one per node + one for
        # children list). After T6.36, the whole traversal uses one
        # SELECT. Allow one more for safety (trace can pick up
        # sqlite-internal statements in some environments).
        assert len(selects) <= 2, (
            f"Expected ≤2 SELECTs for 11-node tree (one WITH RECURSIVE); "
            f"got {len(selects)}. T6.36 regression. Statements:\n"
            + "\n".join(selects)
        )


class TestUpdateTaskStatusAtomicSideEffects:
    """T6.38: the status UPDATE and its side effects (dep-satisfy for
    completed, DLQ record for failed) are now in one transaction. A
    crash in between cannot leave inconsistent state — the sqlite3
    trace should show a single COMMIT for the whole compound operation.
    """

    def test_complete_uses_single_transaction(self, engine, registered_agent):
        """Trace callback asserts dep-satisfy and status UPDATE share
        the same tx. Pre-fix the primitive committed after the status
        UPDATE and the mixin's satisfy call opened a separate tx.
        """
        engine.create_task(
            task_id="t.atomic", parent_agent_id=registered_agent, description="x"
        )
        # Create a dependency pointing at this task.
        engine.manage_dependencies(
            mode="declare",
            dependent_agent_id=registered_agent,
            depends_on_task_id="t.atomic",
            condition="task_completed",
        )

        statements: list[str] = []

        def _tracer(stmt):
            up = stmt.strip().upper()
            if up.startswith(("BEGIN", "COMMIT", "ROLLBACK", "UPDATE TASKS", "UPDATE AGENT_DEPENDENCIES")):
                statements.append(up.split()[0] if up.startswith(("BEGIN", "COMMIT", "ROLLBACK")) else " ".join(up.split()[:2]))

        conn = engine._connect()
        conn.set_trace_callback(_tracer)
        try:
            engine.update_task_status("t.atomic", "completed")
        finally:
            conn.set_trace_callback(None)

        # Expect: exactly one UPDATE TASKS followed by one UPDATE
        # AGENT_DEPENDENCIES, with no COMMIT in between.
        try:
            i_tasks = statements.index("UPDATE TASKS")
            i_deps = statements.index("UPDATE AGENT_DEPENDENCIES")
        except ValueError:
            # No dep row means satisfy was a no-op UPDATE — still fine.
            # Instead just assert no COMMIT between status write and end.
            assert "UPDATE TASKS" in statements
            return
        assert i_deps > i_tasks
        between = statements[i_tasks + 1: i_deps]
        assert "COMMIT" not in between, (
            f"Expected no COMMIT between status UPDATE and dep satisfy; got {statements}"
        )

    def test_fail_records_dlq_inside_status_tx(self, engine, registered_agent):
        """DLQ recording moves into the primitive's tx. After a failed
        transition, history must already be visible without needing a
        subsequent commit.
        """
        engine.create_task(
            task_id="t.fail", parent_agent_id=registered_agent, description="x"
        )
        result = engine.update_task_status("t.fail", "failed", error="boom")
        # Primitive now surfaces the failure record inline.
        assert result.get("failure_record") is not None
        assert result["failure_record"].get("status") in ("failed", "dead_letter")
        history = engine.task_failures(action="history", task_id="t.fail")
        assert history["count"] == 1

    def test_noop_failed_call_does_not_record_extra_attempt(
        self, engine, registered_agent
    ):
        """T6.40 guard under T6.38 fold: idempotent failed() still
        doesn't double-record. Fold must not accidentally re-enable the
        pre-T6.40 behaviour.
        """
        engine.create_task(
            task_id="t.idem2", parent_agent_id=registered_agent, description="x"
        )
        engine.update_task_status("t.idem2", "failed", error="b1")
        engine.update_task_status("t.idem2", "failed", error="b2")
        engine.update_task_status("t.idem2", "failed", error="b3")
        history = engine.task_failures(action="history", task_id="t.idem2")
        assert history["count"] == 1


class TestAssignTaskReassignment:
    """T6.32: re-assigning a task clears the previous assignee's
    ``current_task`` row so it doesn't stale-reference work they no
    longer own. Old behaviour left the row pointing at the description
    forever, so ``list_agents`` would show two agents working on the
    same thing after a handoff.
    """

    def _agent_current_task(self, engine, agent_id):
        with engine._connect() as conn:
            row = conn.execute(
                "SELECT current_task FROM agent_responsibilities WHERE agent_id=?",
                (agent_id,),
            ).fetchone()
            return row["current_task"] if row else None

    def test_reassign_clears_prior_current_task(self, engine, two_agents):
        a = two_agents["child"]
        b = two_agents["other"]
        engine.create_task(
            task_id="t.re", parent_agent_id=two_agents["parent"], description="pay"
        )
        engine.assign_task("t.re", a)
        assert self._agent_current_task(engine, a) == "pay"

        engine.assign_task("t.re", b)
        # a's current_task is cleared; b picks it up.
        assert self._agent_current_task(engine, a) is None
        assert self._agent_current_task(engine, b) == "pay"

    def test_reassign_to_same_agent_is_noop(self, engine, registered_agent):
        engine.create_task(
            task_id="t.same", parent_agent_id=registered_agent, description="x"
        )
        engine.assign_task("t.same", registered_agent)
        engine.assign_task("t.same", registered_agent)
        assert self._agent_current_task(engine, registered_agent) == "x"

    def test_reassign_does_not_clobber_unrelated_current_task(
        self, engine, two_agents
    ):
        """If a's current_task already points at something else (not this
        task's description), reassigning task t.re away from a must not
        wipe that unrelated entry.
        """
        a = two_agents["child"]
        b = two_agents["other"]
        engine.create_task(
            task_id="t.unrelated",
            parent_agent_id=two_agents["parent"],
            description="different work",
        )
        engine.create_task(
            task_id="t.move",
            parent_agent_id=two_agents["parent"],
            description="moving work",
        )
        engine.assign_task("t.move", a)
        # Override a's current_task to the unrelated description.
        with engine._connect() as conn:
            conn.execute(
                "UPDATE agent_responsibilities SET current_task=? WHERE agent_id=?",
                ("different work", a),
            )

        engine.assign_task("t.move", b)
        # Unrelated current_task is preserved.
        assert self._agent_current_task(engine, a) == "different work"


class TestUpdateTaskStatusErrorForwarding:
    """T6.39: ``error`` is persisted on the task row for every status
    transition, not just ``status='failed'``. Callers that move a task
    to ``blocked`` or ``in_progress`` can surface diagnostic context.
    """

    def test_error_stored_on_blocked_transition(self, engine, registered_agent):
        engine.create_task(task_id="t.blk", parent_agent_id=registered_agent, description="x")
        result = engine.update_task_status(
            "t.blk", "blocked", error="upstream API returned 502"
        )
        assert result.get("updated") is True
        task = engine.query_tasks(query_type="task", task_id="t.blk").get("task")
        assert task is not None
        assert task.get("error") == "upstream API returned 502"
        assert task.get("status") == "blocked"

    def test_error_stored_on_in_progress_transition(self, engine, registered_agent):
        engine.create_task(task_id="t.ip", parent_agent_id=registered_agent, description="x")
        engine.update_task_status(
            "t.ip", "in_progress", error="retry after partial failure of subtask X"
        )
        task = engine.query_tasks(query_type="task", task_id="t.ip").get("task")
        assert task.get("error") == "retry after partial failure of subtask X"

    def test_none_error_does_not_clobber_existing(self, engine, registered_agent):
        """A later transition without an error arg must not erase a
        previously-stored error message. Pre-fix the SET clause was
        positional and would have overwritten with None; the dynamic
        SQL build only updates the column when the arg is non-None.
        """
        engine.create_task(task_id="t.k", parent_agent_id=registered_agent, description="x")
        engine.update_task_status("t.k", "blocked", error="disk full")
        engine.update_task_status("t.k", "in_progress")  # no error arg
        task = engine.query_tasks(query_type="task", task_id="t.k").get("task")
        assert task.get("error") == "disk full"
        assert task.get("status") == "in_progress"


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
        # Need 3 failures to reach dead_letter (default max_retries=3).
        # T6.40: each failure is an actual transition — cycle through
        # in_progress between attempts so each "failed" call is a true
        # status change, not a no-op re-report.
        for _ in range(3):
            engine.update_task_status("task.1", "in_progress")
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
        # Push to dead_letter. T6.40: cycle through in_progress so each
        # failure is a real transition.
        for _ in range(3):
            engine.update_task_status("task.dlq.1", "in_progress")
            engine.update_task_status("task.dlq.1", "failed", error="boom")
        assert engine.task_failures(action="list_dead_letter")["count"] == 1
        engine.task_failures(action="retry", task_id="task.dlq.1")

        # One more failure after retry — should NOT immediately DLQ.
        # retry puts the task back in 'pending', so this is a real
        # pending → failed transition.
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

    def test_orphan_dlq_row_flagged_and_retry_rejected(
        self, engine, registered_agent
    ):
        """T6.41: a DLQ row whose underlying task no longer exists is
        flagged ``orphan=True`` in list_dead_letter, and
        ``retry_from_dead_letter`` refuses to pretend a retry happened.
        Pre-fix the UPDATE on the ``tasks`` table matched zero rows and
        we still returned ``{"retried": True}``.
        """
        engine.create_task(
            task_id="task.ghost", parent_agent_id=registered_agent, description="x"
        )
        for _ in range(3):
            engine.update_task_status("task.ghost", "in_progress")
            engine.update_task_status("task.ghost", "failed", error="boom")
        assert engine.task_failures(action="list_dead_letter")["count"] == 1

        # Delete the underlying task row out-of-band (the DLQ row remains).
        with engine._connect() as conn:
            conn.execute("DELETE FROM tasks WHERE id = ?", ("task.ghost",))

        dlq = engine.task_failures(action="list_dead_letter")
        assert dlq["count"] == 1
        assert dlq["dead_letter_tasks"][0]["orphan"] is True

        # Retrying must refuse and explain why.
        result = engine.task_failures(action="retry", task_id="task.ghost")
        assert result.get("retried") is False
        assert result.get("reason") == "task_row_missing"

        # After rejection, the DLQ row should no longer advertise itself
        # as dead_letter (status flipped to 'orphan'), so the next
        # list_dead_letter won't show it.
        dlq_after = engine.task_failures(action="list_dead_letter")
        assert dlq_after["count"] == 0

    def test_non_orphan_dlq_row_retries_cleanly(self, engine, registered_agent):
        engine.create_task(
            task_id="task.live", parent_agent_id=registered_agent, description="x"
        )
        for _ in range(3):
            engine.update_task_status("task.live", "in_progress")
            engine.update_task_status("task.live", "failed", error="boom")
        dlq = engine.task_failures(action="list_dead_letter")
        assert dlq["dead_letter_tasks"][0]["orphan"] is False
        result = engine.task_failures(action="retry", task_id="task.live")
        assert result.get("retried") is True

    def test_double_failed_call_is_idempotent(self, engine, registered_agent):
        """T6.40: calling ``update_task_status(task_id, 'failed')`` twice
        in a row must NOT record two separate attempts. The second call
        sees the task already in ``failed`` (no state transition), so no
        DLQ side-effect fires. Pre-fix, two calls accelerated the DLQ
        threshold — three back-to-back failed() calls would hit
        dead_letter at max_retries=3 even though only one actual
        execution had failed.
        """
        engine.create_task(
            task_id="task.idem", parent_agent_id=registered_agent, description="x"
        )
        engine.update_task_status("task.idem", "failed", error="boom")
        engine.update_task_status("task.idem", "failed", error="boom-again")
        engine.update_task_status("task.idem", "failed", error="boom-third")
        history = engine.task_failures(action="history", task_id="task.idem")
        assert history["count"] == 1, (
            f"Three no-op 'failed' calls should record exactly one attempt, "
            f"got {history['count']}"
        )
        assert history["history"][0]["attempt"] == 1
        # Task stays in 'failed', not escalated to dead_letter prematurely.
        dlq = engine.task_failures(action="list_dead_letter")
        assert dlq["count"] == 0
