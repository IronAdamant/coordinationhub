"""Tests for the HA spawner — sub-agent spawn tracking."""

from __future__ import annotations

import pytest

from tests.conftest import run_concurrent


class TestSpawnSubagent:
    def test_spawn_subagent_creates_pending_record(self, engine):
        parent = engine.generate_agent_id()
        engine.register_agent(parent)

        result = engine.spawn_subagent(parent, "Explore", description="test task")
        assert result["stashed"] is True
        assert result["parent_agent_id"] == parent

        spawns = engine.get_pending_spawns(parent)
        assert len(spawns) == 1
        assert spawns[0]["status"] == "pending"
        assert spawns[0]["description"] == "test task"

    def test_spawn_subagent_with_source(self, engine):
        parent = engine.generate_agent_id()
        engine.register_agent(parent)

        result = engine.spawn_subagent(parent, "Plan", source="kimi_cli")
        assert result["stashed"] is True

        spawns = engine.get_pending_spawns(parent)
        assert spawns[0]["source"] == "kimi_cli"


class TestReportSubagentSpawned:
    def test_report_subagent_spawned_consumes_pending(self, engine):
        parent = engine.generate_agent_id()
        engine.register_agent(parent)

        engine.spawn_subagent(parent, "Explore", description="test task")
        child = engine.generate_agent_id(parent)
        engine.register_agent(child, parent)

        result = engine.report_subagent_spawned(parent, "Explore", child, source="kimi_cli")
        assert result["reported"] is True
        assert result["child_agent_id"] == child
        assert result["description"] == "test task"
        assert result["spawn_id"] is not None

        spawns = engine.get_pending_spawns(parent, include_consumed=True)
        assert spawns[0]["status"] == "registered"
        assert spawns[0]["source"] == "kimi_cli"

    def test_report_subagent_spawned_without_pending(self, engine):
        parent = engine.generate_agent_id()
        engine.register_agent(parent)
        child = engine.generate_agent_id(parent)
        engine.register_agent(child, parent)

        result = engine.report_subagent_spawned(parent, "Explore", child)
        assert result["reported"] is True
        assert result["spawn_id"] is None
        assert result["description"] is None

    def test_report_subagent_spawned_matches_oldest_pending(self, engine):
        parent = engine.generate_agent_id()
        engine.register_agent(parent)

        engine.spawn_subagent(parent, "Explore", description="first")
        engine.spawn_subagent(parent, "Explore", description="second")

        child = engine.generate_agent_id(parent)
        engine.register_agent(child, parent)

        result = engine.report_subagent_spawned(parent, "Explore", child)
        assert result["description"] == "first"

        # Second spawn should still be pending
        spawns = engine.get_pending_spawns(parent)
        assert len(spawns) == 1
        assert spawns[0]["description"] == "second"


class TestSpawnIdAtomicity:
    """T1.9 regression: spawn_id generation must be atomic with insert.

    Pre-fix, ``generate_spawn_id`` used ``COUNT(*)`` in one connection
    then a separate ``INSERT`` in another. Two concurrent callers observed
    the same COUNT and produced identical spawn IDs; the second INSERT
    blew up on the ``task_id`` PK.
    """

    def test_concurrent_spawns_produce_distinct_ids(self, engine):
        parent = engine.generate_agent_id()
        engine.register_agent(parent)

        def spawn():
            return engine.spawn_subagent(parent, "Explore", description="t")

        results, errors = run_concurrent(n=8, target=spawn)
        assert errors == [], f"concurrent spawn raised: {errors}"
        spawn_ids = [r["spawn_id"] for r in results]
        assert len(set(spawn_ids)) == 8, f"collisions: {spawn_ids}"

        spawns = engine.get_pending_spawns(parent)
        assert len(spawns) == 8

    def test_sequential_spawn_ids_are_monotonic(self, engine):
        """After N spawns the numeric seq reaches N-1, not wraps back to 0."""
        parent = engine.generate_agent_id()
        engine.register_agent(parent)

        ids = [
            engine.spawn_subagent(parent, "Plan", description=f"t{i}")["spawn_id"]
            for i in range(5)
        ]
        seqs = [int(sid.rsplit(".", 1)[-1]) for sid in ids]
        assert seqs == [0, 1, 2, 3, 4]

    def test_spawn_seq_survives_consumption(self, engine):
        """Consumed spawns don't free their seq — next spawn keeps incrementing.

        This matters so that a re-used IDE tool_use_id (which would hash
        back to the same spawn_id under COUNT semantics) still produces
        distinct rows.
        """
        parent = engine.generate_agent_id()
        engine.register_agent(parent)
        child = engine.generate_agent_id(parent)
        engine.register_agent(child, parent)

        first = engine.spawn_subagent(parent, "Explore")["spawn_id"]
        engine.report_subagent_spawned(parent, "Explore", child)
        second = engine.spawn_subagent(parent, "Explore")["spawn_id"]

        assert first != second
        assert int(second.rsplit(".", 1)[-1]) > int(first.rsplit(".", 1)[-1])


class TestPendingTaskDoubleFire:
    """T1.9 regression: ON CONFLICT DO UPDATE must not resurrect consumed rows.

    Pre-fix, a second ``stash_pending_task`` on the same tool_use_id
    (IDE replay) blindly reset ``consumed_at = NULL, status = 'pending'``,
    which re-queued a task that had already been consumed by a
    SubagentStart event.
    """

    def test_double_fire_on_consumed_row_is_noop(self, engine):
        from coordinationhub.pending_tasks import (
            stash_pending_task,
            consume_pending_task,
        )

        session = "session-abc"
        tool_use_id = "tool-xyz"

        stash_pending_task(
            engine._connect, tool_use_id, session, "Explore",
            description="original",
        )
        consumed = consume_pending_task(engine._connect, session, "Explore")
        assert consumed is not None
        assert consumed["description"] == "original"

        # IDE replays the hook with altered description. Pre-fix, this
        # would reset the row and a second consume would pop it again.
        result = stash_pending_task(
            engine._connect, tool_use_id, session, "Explore",
            description="replay-should-be-ignored",
        )
        assert result["stashed"] is False  # narrowed ON CONFLICT skipped the row

        # No pending task available — the consumed row stays consumed.
        second = consume_pending_task(engine._connect, session, "Explore")
        assert second is None

        # The row's status must still be 'consumed'.
        with engine._connect() as conn:
            row = conn.execute(
                "SELECT status, description FROM pending_tasks WHERE task_id = ?",
                (tool_use_id,),
            ).fetchone()
            assert row["status"] == "consumed"
            # The consumed row's description is unchanged by the replay.
            assert row["description"] == "original"

    def test_double_fire_on_pending_row_updates_description(self, engine):
        """Non-consumed rows can still be updated by a retry (e.g. the IDE
        genuinely edited the prompt before SubagentStart fired).
        """
        from coordinationhub.pending_tasks import (
            stash_pending_task,
            consume_pending_task,
        )

        session = "sess"
        tool_use_id = "tid"

        stash_pending_task(engine._connect, tool_use_id, session, "Plan", description="v1")
        stash_pending_task(engine._connect, tool_use_id, session, "Plan", description="v2")

        consumed = consume_pending_task(engine._connect, session, "Plan")
        assert consumed["description"] == "v2"
