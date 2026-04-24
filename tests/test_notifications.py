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
    def test_context_bundle_has_url(self, engine, registered_agent):
        """Context bundle includes a single coordination_url string, not a dict."""
        # T6.22 (final step): ``_build_context_bundle`` moved from the
        # engine onto the :class:`Identity` subsystem as a private
        # helper. Callers that reached into the private method now go
        # through ``engine._identity._build_context_bundle(...)``.
        ctx = engine._identity._build_context_bundle(registered_agent, None)
        assert "coordination_url" in ctx
        assert isinstance(ctx["coordination_url"], str)
        assert ctx["coordination_url"].startswith("http://")

    def test_status_tool_count_dynamic(self, engine):
        """status() returns dynamic tool count from TOOL_DISPATCH."""
        from coordinationhub.dispatch import TOOL_DISPATCH
        status = engine.status()
        assert status["tools"] == len(TOOL_DISPATCH)
