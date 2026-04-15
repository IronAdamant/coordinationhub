"""Tests for document locking: acquire, release, refresh, status, list, reap.

Covers file-level locks, region locks, shared/exclusive semantics.
"""

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
        engine.acquire_lock("/test.txt", registered_agent, ttl=0.01)
        time.sleep(0.02)
        other = engine.generate_agent_id()
        engine.register_agent(other)
        result = engine.acquire_lock("/test.txt", other)
        assert result["acquired"] is True


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


class TestSharedLocks:
    """Tests for shared lock semantics — multiple shared locks allowed."""

    def test_shared_locks_no_conflict(self, engine, two_agents):
        """Two shared locks on the same file should both succeed."""
        r1 = engine.acquire_lock("/shared.txt", two_agents["child"], lock_type="shared")
        r2 = engine.acquire_lock("/shared.txt", two_agents["other"], lock_type="shared")
        assert r1["acquired"] is True
        assert r2["acquired"] is True

    def test_exclusive_blocks_shared(self, engine, two_agents):
        """An exclusive lock blocks a subsequent shared lock."""
        engine.acquire_lock("/test.txt", two_agents["child"], lock_type="exclusive")
        result = engine.acquire_lock("/test.txt", two_agents["other"], lock_type="shared")
        assert result["acquired"] is False

    def test_shared_blocks_exclusive(self, engine, two_agents):
        """A shared lock blocks a subsequent exclusive lock."""
        engine.acquire_lock("/test.txt", two_agents["child"], lock_type="shared")
        result = engine.acquire_lock("/test.txt", two_agents["other"], lock_type="exclusive")
        assert result["acquired"] is False

    def test_three_shared_locks(self, engine):
        """Three agents can all hold shared locks on the same file."""
        agents = []
        for i in range(3):
            aid = engine.generate_agent_id()
            engine.register_agent(aid)
            agents.append(aid)
        for aid in agents:
            result = engine.acquire_lock("/multi.txt", aid, lock_type="shared")
            assert result["acquired"] is True
        status = engine.get_lock_status("/multi.txt")
        assert status["locked"] is True
        assert "holders" in status
        assert len(status["holders"]) == 3

    def test_release_one_shared_keeps_others(self, engine, two_agents):
        """Releasing one shared lock doesn't affect the other."""
        engine.acquire_lock("/shared.txt", two_agents["child"], lock_type="shared")
        engine.acquire_lock("/shared.txt", two_agents["other"], lock_type="shared")
        engine.release_lock("/shared.txt", two_agents["child"])
        status = engine.get_lock_status("/shared.txt")
        assert status["locked"] is True
        assert status["locked_by"] == two_agents["other"]


class TestRegionLocks:
    """Tests for region-level locking — concurrent edits to non-overlapping regions."""

    def test_non_overlapping_regions_no_conflict(self, engine, two_agents):
        """Two exclusive locks on non-overlapping regions should both succeed."""
        r1 = engine.acquire_lock("/app.js", two_agents["child"], region_start=1, region_end=50)
        r2 = engine.acquire_lock("/app.js", two_agents["other"], region_start=51, region_end=100)
        assert r1["acquired"] is True
        assert r2["acquired"] is True

    def test_overlapping_regions_conflict(self, engine, two_agents):
        """Two exclusive locks on overlapping regions should conflict."""
        engine.acquire_lock("/app.js", two_agents["child"], region_start=1, region_end=60)
        result = engine.acquire_lock("/app.js", two_agents["other"], region_start=50, region_end=100)
        assert result["acquired"] is False

    def test_whole_file_conflicts_with_region(self, engine, two_agents):
        """A whole-file lock conflicts with any region lock."""
        engine.acquire_lock("/app.js", two_agents["child"])  # whole file
        result = engine.acquire_lock("/app.js", two_agents["other"], region_start=1, region_end=10)
        assert result["acquired"] is False

    def test_region_conflicts_with_whole_file(self, engine, two_agents):
        """A region lock conflicts with a whole-file lock request."""
        engine.acquire_lock("/app.js", two_agents["child"], region_start=1, region_end=10)
        result = engine.acquire_lock("/app.js", two_agents["other"])  # whole file
        assert result["acquired"] is False

    def test_shared_overlapping_regions_no_conflict(self, engine, two_agents):
        """Shared locks on overlapping regions should not conflict."""
        r1 = engine.acquire_lock("/app.js", two_agents["child"], lock_type="shared",
                                 region_start=1, region_end=60)
        r2 = engine.acquire_lock("/app.js", two_agents["other"], lock_type="shared",
                                 region_start=50, region_end=100)
        assert r1["acquired"] is True
        assert r2["acquired"] is True

    def test_release_region_lock(self, engine, two_agents):
        """Release a specific region lock without affecting other regions."""
        engine.acquire_lock("/app.js", two_agents["child"], region_start=1, region_end=50)
        engine.acquire_lock("/app.js", two_agents["other"], region_start=51, region_end=100)
        engine.release_lock("/app.js", two_agents["child"], region_start=1, region_end=50)
        # Other agent's lock should still be held
        locks = engine.list_locks(agent_id=two_agents["other"])
        assert locks["count"] == 1

    def test_refresh_region_lock(self, engine, registered_agent):
        """Refresh a region lock by specifying the region."""
        engine.acquire_lock("/app.js", registered_agent, region_start=1, region_end=50, ttl=60.0)
        result = engine.refresh_lock("/app.js", registered_agent, ttl=120.0,
                                     region_start=1, region_end=50)
        assert result["refreshed"] is True

    def test_list_locks_includes_region(self, engine, registered_agent):
        """list_locks returns region info."""
        engine.acquire_lock("/app.js", registered_agent, region_start=10, region_end=20)
        result = engine.list_locks()
        assert result["count"] == 1
        lock = result["locks"][0]
        assert lock["region_start"] == 10
        assert lock["region_end"] == 20

    def test_same_agent_multiple_regions(self, engine, registered_agent):
        """Same agent can hold locks on multiple non-overlapping regions."""
        r1 = engine.acquire_lock("/app.js", registered_agent, region_start=1, region_end=50)
        r2 = engine.acquire_lock("/app.js", registered_agent, region_start=51, region_end=100)
        assert r1["acquired"] is True
        assert r2["acquired"] is True
        locks = engine.list_locks(agent_id=registered_agent)
        assert locks["count"] == 2

    def test_force_steal_region_lock(self, engine, two_agents):
        """Force-steal a conflicting region lock."""
        engine.acquire_lock("/app.js", two_agents["child"], region_start=1, region_end=50)
        result = engine.acquire_lock("/app.js", two_agents["other"], region_start=1, region_end=50, force=True)
        assert result["acquired"] is True
        assert result["locked_by"] == two_agents["other"]

    def test_acquire_returns_region_info(self, engine, registered_agent):
        """acquire_lock response includes region_start/region_end."""
        result = engine.acquire_lock("/app.js", registered_agent, region_start=5, region_end=15)
        assert result["region_start"] == 5
        assert result["region_end"] == 15


class TestLockReaping:
    def test_reap_expired_locks(self, engine, registered_agent):
        engine.acquire_lock("/test.txt", registered_agent, ttl=0.01)
        time.sleep(0.02)
        result = engine.admin_locks(action="reap_expired")
        assert result["reaped"] >= 1

    def test_reap_spares_active_agent_locks(self, engine, registered_agent):
        """Expired locks held by agents with a recent heartbeat are spared."""
        engine.acquire_lock("/active.txt", registered_agent, ttl=0.01)
        time.sleep(0.02)
        # Lock is expired, but agent has a fresh heartbeat
        engine.heartbeat(registered_agent)
        result = engine.admin_locks(action="reap_expired", grace_seconds=120.0)
        assert result["reaped"] == 0
        # Lock still exists
        status = engine.get_lock_status("/active.txt")
        assert status["locked"] is True

    def test_reap_removes_crashed_agent_locks(self, engine, registered_agent):
        """Expired locks from agents with stale heartbeats are reaped normally."""
        engine.acquire_lock("/stale.txt", registered_agent, ttl=0.01)
        time.sleep(0.02)
        # Simulate stale agent: set heartbeat far in the past
        with engine._connect() as conn:
            conn.execute(
                "UPDATE agents SET last_heartbeat = 0 WHERE agent_id = ?",
                (registered_agent,),
            )
        result = engine.admin_locks(action="reap_expired", grace_seconds=120.0)
        assert result["reaped"] >= 1

    def test_release_agent_locks(self, engine, two_agents):
        engine.acquire_lock("/a.txt", two_agents["child"])
        engine.acquire_lock("/b.txt", two_agents["child"])
        result = engine.admin_locks(action="release_by_agent", agent_id=two_agents["child"])
        assert result["released"] == 2

    def test_release_agent_locks_includes_regions(self, engine, registered_agent):
        """release_agent_locks releases both whole-file and region locks."""
        engine.acquire_lock("/a.txt", registered_agent)
        engine.acquire_lock("/b.txt", registered_agent, region_start=1, region_end=50)
        result = engine.admin_locks(action="release_by_agent", agent_id=registered_agent)
        assert result["released"] == 2


class TestBroadcastAcknowledgment:
    def _heartbeat_all(self, engine, two_agents):
        engine.heartbeat(two_agents["parent"])
        engine.heartbeat(two_agents["child"])
        engine.heartbeat(two_agents["other"])

    def test_broadcast_without_require_ack(self, engine, two_agents):
        self._heartbeat_all(engine, two_agents)
        # broadcast from child so other is a sibling
        result = engine.broadcast(two_agents["child"])
        assert "broadcast_id" not in result
        assert "acknowledged_by" in result

    def test_broadcast_with_require_ack_creates_broadcast(self, engine, two_agents):
        self._heartbeat_all(engine, two_agents)
        child = two_agents["child"]
        other = two_agents["other"]

        result = engine.broadcast(child, require_ack=True, message="hello")
        assert "broadcast_id" in result
        assert result["pending_acks"] == [other]
        assert result["acknowledged_by"] == []

    def test_acknowledge_broadcast(self, engine, two_agents):
        self._heartbeat_all(engine, two_agents)
        child = two_agents["child"]
        other = two_agents["other"]

        broadcast = engine.broadcast(child, require_ack=True)
        bid = broadcast["broadcast_id"]

        result = engine.acknowledge_broadcast(bid, other)
        assert result["acknowledged"] is True

        status = engine.wait_for_broadcast_acks(bid, timeout_s=1.0)
        assert status["timed_out"] is False
        assert other in status["acknowledged_by"]

    def test_acknowledge_expired_broadcast_fails(self, engine, two_agents):
        self._heartbeat_all(engine, two_agents)
        child = two_agents["child"]
        other = two_agents["other"]

        broadcast = engine.broadcast(child, require_ack=True, ttl=0.01)
        bid = broadcast["broadcast_id"]
        time.sleep(0.02)

        result = engine.acknowledge_broadcast(bid, other)
        assert result["acknowledged"] is False
        assert result["reason"] == "expired_or_not_found"

    def test_wait_for_broadcast_acks_not_found(self, engine):
        status = engine.wait_for_broadcast_acks(99999, timeout_s=0.1)
        assert status["timed_out"] is True
        assert status["reason"] == "not_found"

    def test_broadcast_sends_ack_request_messages(self, engine, two_agents):
        self._heartbeat_all(engine, two_agents)
        child = two_agents["child"]
        other = two_agents["other"]

        engine.broadcast(child, require_ack=True, message="test")

        msgs = engine.get_messages(other, unread_only=True)
        assert msgs["count"] == 1
        assert msgs["messages"][0]["message_type"] == "broadcast_ack_request"
        assert msgs["messages"][0]["payload"]["message"] == "test"
