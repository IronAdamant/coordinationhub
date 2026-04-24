"""Regression tests for the HousekeepingScheduler and its prune primitives.

Covers audit items T1.17 (tail), T4.7, and T7.32. The scheduler is
off-by-default so these tests construct it explicitly or call the
prune methods directly on the engine.
"""

from __future__ import annotations

import threading
import time

import pytest

from coordinationhub.core import CoordinationEngine
from coordinationhub.housekeeping import (
    HousekeepingScheduler,
    build_default_scheduler,
    is_enabled_by_env,
)


# ----------------------------------------------------------------------
# Prune primitive tests — verify each individual prune does what we want
# before wiring them into the scheduler.
# ----------------------------------------------------------------------


class TestAssessmentResultsPrune:
    def test_prune_removes_old_rows_keeps_fresh(self, engine):
        # Seed the table directly; the real assessment path is covered by
        # test_assessment.py.
        now = time.time()
        old_ts = now - (40 * 24 * 3600.0)  # 40 days ago
        fresh_ts = now - 60.0
        with engine._connect() as conn:
            conn.execute(
                "INSERT INTO assessment_results "
                "(suite_name, metric, score, details_json, run_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("suite", "m", 1.0, "{}", old_ts),
            )
            conn.execute(
                "INSERT INTO assessment_results "
                "(suite_name, metric, score, details_json, run_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("suite", "m", 1.0, "{}", fresh_ts),
            )
        result = engine.prune_assessment_results(
            max_age_seconds=30 * 24 * 3600.0,
        )
        assert result["pruned"] == 1
        with engine._connect() as conn:
            remaining = conn.execute(
                "SELECT run_at FROM assessment_results"
            ).fetchall()
        assert len(remaining) == 1
        assert remaining[0]["run_at"] == pytest.approx(fresh_ts)

    def test_prune_empty_table_is_noop(self, engine):
        result = engine.prune_assessment_results(max_age_seconds=1.0)
        assert result["pruned"] == 0


class TestStoppedAgentPrune:
    def test_prune_removes_long_stopped_agent(self, engine):
        # Register then deregister so the row is status='stopped'.
        aid = engine.generate_agent_id()
        engine.register_agent(aid)
        engine.deregister_agent(aid)

        # Backdate the heartbeat past the retention window.
        ancient = time.time() - (8 * 24 * 3600.0)
        with engine._connect() as conn:
            conn.execute(
                "UPDATE agents SET last_heartbeat = ? WHERE agent_id = ?",
                (ancient, aid),
            )

        result = engine.prune_stopped_agents(
            retention_seconds=7 * 24 * 3600.0,
        )
        assert result["pruned"] == 1

        with engine._connect() as conn:
            row = conn.execute(
                "SELECT agent_id FROM agents WHERE agent_id = ?", (aid,),
            ).fetchone()
        assert row is None

    def test_prune_preserves_agent_with_active_children(self, engine):
        parent = engine.generate_agent_id()
        engine.register_agent(parent)
        child = engine.generate_agent_id(parent)
        engine.register_agent(child, parent_id=parent)

        # Stop the parent but not the child. Backdate the parent's
        # heartbeat so it's eligible by age.
        with engine._connect() as conn:
            conn.execute(
                "UPDATE agents SET status = 'stopped', last_heartbeat = ? "
                "WHERE agent_id = ?",
                (time.time() - (8 * 24 * 3600.0), parent),
            )

        result = engine.prune_stopped_agents(
            retention_seconds=7 * 24 * 3600.0,
        )
        assert result["pruned"] == 0
        assert result["skipped_live_children"] == 1

        with engine._connect() as conn:
            row = conn.execute(
                "SELECT status FROM agents WHERE agent_id = ?", (parent,),
            ).fetchone()
        assert row is not None  # still present

    def test_prune_skips_recently_stopped_agent(self, engine):
        aid = engine.generate_agent_id()
        engine.register_agent(aid)
        engine.deregister_agent(aid)

        result = engine.prune_stopped_agents(
            retention_seconds=7 * 24 * 3600.0,
        )
        assert result["pruned"] == 0

        with engine._connect() as conn:
            row = conn.execute(
                "SELECT agent_id FROM agents WHERE agent_id = ?", (aid,),
            ).fetchone()
        assert row is not None

    def test_prune_clears_lineage_and_responsibilities(self, engine):
        parent = engine.generate_agent_id()
        engine.register_agent(parent)
        child = engine.generate_agent_id(parent)
        engine.register_agent(child, parent_id=parent)

        # Deregister the child, backdate, then prune.
        engine.deregister_agent(child)
        with engine._connect() as conn:
            conn.execute(
                "UPDATE agents SET last_heartbeat = ? WHERE agent_id = ?",
                (time.time() - (8 * 24 * 3600.0), child),
            )
            # Install a responsibility row for the child so we can assert
            # it gets swept.
            conn.execute(
                "INSERT OR IGNORE INTO agent_responsibilities "
                "(agent_id, graph_agent_id, updated_at) VALUES (?, ?, ?)",
                (child, "test-responsibility", time.time()),
            )

        engine.prune_stopped_agents(retention_seconds=7 * 24 * 3600.0)

        with engine._connect() as conn:
            lineage_rows = conn.execute(
                "SELECT 1 FROM lineage WHERE parent_id = ? OR child_id = ?",
                (child, child),
            ).fetchall()
            resp_rows = conn.execute(
                "SELECT 1 FROM agent_responsibilities WHERE agent_id = ?",
                (child,),
            ).fetchall()
        assert lineage_rows == []
        assert resp_rows == []


# ----------------------------------------------------------------------
# Scheduler behavior
# ----------------------------------------------------------------------


class TestHousekeepingSchedulerBasics:
    def test_run_once_fires_every_task(self):
        sched = HousekeepingScheduler()
        calls: list[str] = []
        sched.add_task("a", 60.0, lambda: calls.append("a"))
        sched.add_task("b", 60.0, lambda: calls.append("b"))

        result = sched.run_once()
        assert set(result.keys()) == {"a", "b"}
        assert all(r["ok"] for r in result.values())
        assert calls == ["a", "b"]

    def test_run_once_captures_exceptions(self):
        sched = HousekeepingScheduler()

        def bad():
            raise RuntimeError("sentinel failure")

        sched.add_task("bad", 60.0, bad)
        result = sched.run_once()
        assert result["bad"]["ok"] is False
        assert "sentinel failure" in result["bad"]["error"]

    def test_background_thread_fires_then_stops_cleanly(self):
        sched = HousekeepingScheduler()
        ran = threading.Event()

        def mark():
            ran.set()

        sched.add_task("mark", 60.0, mark)
        sched.start()
        # next_run_at starts at 0, so the task fires on the first tick.
        assert ran.wait(timeout=5.0), "task never ran on scheduler thread"
        sched.stop(timeout=5.0)
        # Idempotent double-stop.
        sched.stop(timeout=1.0)

    def test_failing_task_does_not_kill_the_thread(self):
        sched = HousekeepingScheduler()
        good_ran = threading.Event()

        def bad():
            raise RuntimeError("keep going")

        def good():
            good_ran.set()

        sched.add_task("bad", 60.0, bad)
        sched.add_task("good", 60.0, good)
        sched.start()
        try:
            assert good_ran.wait(timeout=5.0), (
                "good task did not run alongside the failing one"
            )
        finally:
            sched.stop(timeout=5.0)

    def test_add_task_after_start_is_rejected(self):
        sched = HousekeepingScheduler()
        sched.add_task("a", 60.0, lambda: None)
        sched.start()
        try:
            with pytest.raises(RuntimeError):
                sched.add_task("b", 60.0, lambda: None)
        finally:
            sched.stop(timeout=5.0)


# ----------------------------------------------------------------------
# Engine wiring
# ----------------------------------------------------------------------


class TestEngineHousekeepingIntegration:
    def test_default_scheduler_prunes_events_on_demand(self, tmp_path):
        storage_dir = tmp_path / "_coordhub_storage"
        storage_dir.mkdir(exist_ok=True)
        eng = CoordinationEngine(
            storage_dir=str(storage_dir),
            project_root=tmp_path,
            housekeeping=False,  # drive manually via run_once
        )
        eng.start()
        try:
            # Seed an ancient coordination_events row.
            with eng._connect() as conn:
                conn.execute(
                    "INSERT INTO coordination_events "
                    "(topic, payload_json, created_at) VALUES (?, ?, ?)",
                    ("stale.topic", "{}", time.time() - (8 * 24 * 3600.0)),
                )

            sched = build_default_scheduler(eng)
            results = sched.run_once()

            assert results["coordination_events"]["ok"] is True
            assert results["stale_agents"]["ok"] is True
            assert results["assessment_results"]["ok"] is True
            assert results["work_intents"]["ok"] is True

            with eng._connect() as conn:
                stale = conn.execute(
                    "SELECT COUNT(*) AS c FROM coordination_events "
                    "WHERE topic = 'stale.topic'"
                ).fetchone()
            assert stale["c"] == 0
        finally:
            eng.close()

    def test_engine_housekeeping_opt_in_spawns_thread(self, tmp_path):
        storage_dir = tmp_path / "_coordhub_storage"
        storage_dir.mkdir(exist_ok=True)
        eng = CoordinationEngine(
            storage_dir=str(storage_dir),
            project_root=tmp_path,
            housekeeping=True,
        )
        eng.start()
        try:
            assert eng._housekeeper is not None
            thread = eng._housekeeper._thread
            assert thread is not None and thread.is_alive()
        finally:
            eng.close()
            # After close, scheduler handle must be cleared.
            assert eng._housekeeper is None

    def test_engine_housekeeping_default_off(self, tmp_path, monkeypatch):
        # Ensure env var doesn't leak in from the outer shell.
        monkeypatch.delenv("COORDINATIONHUB_HOUSEKEEPING", raising=False)
        storage_dir = tmp_path / "_coordhub_storage"
        storage_dir.mkdir(exist_ok=True)
        eng = CoordinationEngine(
            storage_dir=str(storage_dir),
            project_root=tmp_path,
        )
        eng.start()
        try:
            assert eng._housekeeper is None
        finally:
            eng.close()

    def test_env_var_enables_housekeeping(self, tmp_path, monkeypatch):
        monkeypatch.setenv("COORDINATIONHUB_HOUSEKEEPING", "1")
        storage_dir = tmp_path / "_coordhub_storage"
        storage_dir.mkdir(exist_ok=True)
        eng = CoordinationEngine(
            storage_dir=str(storage_dir),
            project_root=tmp_path,
        )
        eng.start()
        try:
            assert eng._housekeeper is not None
        finally:
            eng.close()


class TestIsEnabledByEnv:
    @pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes", "on"])
    def test_truthy_values_enable(self, monkeypatch, val):
        monkeypatch.setenv("COORDINATIONHUB_HOUSEKEEPING", val)
        assert is_enabled_by_env() is True

    @pytest.mark.parametrize("val", ["", "0", "false", "no", "off", "garbage"])
    def test_falsy_values_disable(self, monkeypatch, val):
        monkeypatch.setenv("COORDINATIONHUB_HOUSEKEEPING", val)
        assert is_enabled_by_env() is False
