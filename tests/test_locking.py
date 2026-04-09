"""Tests for document locking: acquire, release, refresh, status, reap."""

from __future__ import annotations

import pytest
import time


class TestLockAcquisition:
    def test_acquire_new_lock(self, engine, registered_agent):
        result = engine.acquire_lock("/test.txt", registered_agent)
        assert result["acquired"] is True
        assert result["locked_by"] == registered_agent
        assert result["document_path"]

    def test_acquire_same_agent_refreshes(self, engine, registered_agent):
        engine.acquire_lock("/test.txt", registered_agent)
        result = engine.acquire_lock("/test.txt", registered_agent)
        assert result["acquired"] is True

    def test_acquire_contested_lock(self, engine, two_agents):
        engine.acquire_lock("/test.txt", two_agents["child"])
        result = engine.acquire_lock("/test.txt", two_agents["other"])
        assert result["acquired"] is False
        assert result["locked_by"] == two_agents["child"]

    def test_acquire_force_steal(self, engine, two_agents):
        engine.acquire_lock("/test.txt", two_agents["child"])
        result = engine.acquire_lock("/test.txt", two_agents["other"], force=True)
        assert result["acquired"] is True
        assert result["locked_by"] == two_agents["other"]

    def test_acquire_expired_lock(self, engine, registered_agent):
        """An expired lock can be taken over without force."""
        result = engine.acquire_lock("/test.txt", registered_agent, ttl=0.01)
        time.sleep(0.02)
        other = engine.generate_agent_id()
        engine.register_agent(other)
        result2 = engine.acquire_lock("/test.txt", other)
        assert result2["acquired"] is True


class TestLockRelease:
    def test_release_owned_lock(self, engine, registered_agent):
        engine.acquire_lock("/test.txt", registered_agent)
        result = engine.release_lock("/test.txt", registered_agent)
        assert result["released"] is True

    def test_release_not_owner(self, engine, two_agents):
        engine.acquire_lock("/test.txt", two_agents["child"])
        result = engine.release_lock("/test.txt", two_agents["other"])
        assert result["released"] is False
        assert result["reason"] == "not_owner"

    def test_release_not_locked(self, engine, registered_agent):
        result = engine.release_lock("/nonexistent.txt", registered_agent)
        assert result["released"] is False
        assert result["reason"] == "not_locked"


class TestLockRefresh:
    def test_refresh_owned_lock(self, engine, registered_agent):
        engine.acquire_lock("/test.txt", registered_agent)
        result = engine.refresh_lock("/test.txt", registered_agent, ttl=60.0)
        assert result["refreshed"] is True
        assert result["expires_at"] > 0

    def test_refresh_not_owner(self, engine, two_agents):
        engine.acquire_lock("/test.txt", two_agents["child"])
        result = engine.refresh_lock("/test.txt", two_agents["other"])
        assert result["refreshed"] is False
        assert result["reason"] == "not_owner"

    def test_refresh_not_locked(self, engine, registered_agent):
        result = engine.refresh_lock("/nonexistent.txt", registered_agent)
        assert result["refreshed"] is False
        assert result["reason"] == "not_locked"


class TestLockStatus:
    def test_lock_status_locked(self, engine, registered_agent):
        engine.acquire_lock("/test.txt", registered_agent)
        status = engine.get_lock_status("/test.txt")
        assert status["locked"] is True
        assert status["locked_by"] == registered_agent

    def test_lock_status_expired(self, engine, registered_agent):
        engine.acquire_lock("/test.txt", registered_agent, ttl=0.01)
        time.sleep(0.02)
        status = engine.get_lock_status("/test.txt")
        assert status["locked"] is False

    def test_lock_status_not_locked(self, engine, registered_agent):
        status = engine.get_lock_status("/nonexistent.txt")
        assert status["locked"] is False


class TestListLocks:
    def test_list_locks_empty(self, engine, registered_agent):
        result = engine.list_locks()
        assert result["count"] == 0
        assert result["locks"] == []

    def test_list_locks_shows_active(self, engine, registered_agent):
        engine.acquire_lock("/a.txt", registered_agent)
        engine.acquire_lock("/b.txt", registered_agent)
        result = engine.list_locks()
        assert result["count"] == 2
        paths = {lock["document_path"] for lock in result["locks"]}
        assert any("a.txt" in p for p in paths)
        assert any("b.txt" in p for p in paths)

    def test_list_locks_excludes_expired(self, engine, registered_agent):
        engine.acquire_lock("/expired.txt", registered_agent, ttl=0.01)
        engine.acquire_lock("/active.txt", registered_agent, ttl=300.0)
        time.sleep(0.02)
        result = engine.list_locks()
        assert result["count"] == 1
        assert "active.txt" in result["locks"][0]["document_path"]

    def test_list_locks_filter_by_agent(self, engine, two_agents):
        engine.acquire_lock("/a.txt", two_agents["child"])
        engine.acquire_lock("/b.txt", two_agents["other"])
        result = engine.list_locks(agent_id=two_agents["child"])
        assert result["count"] == 1
        assert result["locks"][0]["locked_by"] == two_agents["child"]

    def test_list_locks_includes_details(self, engine, registered_agent):
        engine.acquire_lock("/test.txt", registered_agent, ttl=120.0)
        result = engine.list_locks()
        lock = result["locks"][0]
        assert "document_path" in lock
        assert "locked_by" in lock
        assert "locked_at" in lock
        assert "expires_at" in lock
        assert "lock_type" in lock
        assert lock["locked_by"] == registered_agent


class TestLockReaping:
    def test_reap_expired_locks(self, engine, registered_agent):
        engine.acquire_lock("/test.txt", registered_agent, ttl=0.01)
        time.sleep(0.02)
        result = engine.reap_expired_locks()
        assert result["reaped"] >= 1

    def test_release_agent_locks(self, engine, two_agents):
        engine.acquire_lock("/a.txt", two_agents["child"])
        engine.acquire_lock("/b.txt", two_agents["child"])
        result = engine.release_agent_locks(two_agents["child"])
        assert result["released"] == 2
