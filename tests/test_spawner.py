"""Tests for the HA spawner — sub-agent spawn tracking."""

from __future__ import annotations

import pytest


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
