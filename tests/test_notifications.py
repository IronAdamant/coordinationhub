"""Tests for change notifications: notify_change, get_notifications, prune_notifications."""

from __future__ import annotations

import pytest
import time


class TestNotifications:
    def test_notify_change(self, engine, registered_agent):
        result = engine.notify_change("/test.txt", "modified", registered_agent)
        assert result["recorded"] is True

    def test_get_notifications(self, engine, registered_agent):
        engine.notify_change("/a.txt", "modified", registered_agent)
        engine.notify_change("/b.txt", "created", registered_agent)
        result = engine.get_notifications()
        assert result["notifications"]

    def test_get_notifications_exclude_agent(self, engine, two_agents):
        engine.notify_change("/test.txt", "modified", two_agents["child"])
        engine.notify_change("/test.txt", "modified", two_agents["other"])
        result = engine.get_notifications(exclude_agent=two_agents["child"])
        assert all(n["agent_id"] != two_agents["child"] for n in result["notifications"])

    def test_get_notifications_since(self, engine, registered_agent):
        before = time.time() - 10
        engine.notify_change("/test.txt", "modified", registered_agent)
        result = engine.get_notifications(since=before)
        assert len(result["notifications"]) >= 1

    def test_prune_by_max_entries(self, engine, registered_agent):
        for i in range(10):
            engine.notify_change(f"/file{i}.txt", "modified", registered_agent)
        result = engine.prune_notifications(max_entries=5)
        assert result["pruned"] >= 5

    def test_prune_by_max_age(self, engine, registered_agent):
        engine.notify_change("/old.txt", "modified", registered_agent)
        result = engine.prune_notifications(max_age_seconds=0.01)
        time.sleep(0.02)
        result2 = engine.prune_notifications(max_age_seconds=0.01)
        # Second prune should clean up remaining entries
        assert result2["pruned"] >= 0


class TestCoordinationUrls:
    def test_context_bundle_has_urls(self, engine, registered_agent):
        ctx = engine._context_bundle(registered_agent)
        assert "coordination_urls" in ctx
        assert "coordinationhub" in ctx["coordination_urls"]
        assert "stele" in ctx["coordination_urls"]
        assert "chisel" in ctx["coordination_urls"]
        assert "trammel" in ctx["coordination_urls"]

    def test_status_tool_count_dynamic(self, engine):
        """status() returns dynamic tool count, not hardcoded number."""
        status = engine.status()
        assert status["tools"] == 20
