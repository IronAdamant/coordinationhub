"""Tests for the in-memory lock cache."""

from __future__ import annotations

import time

import pytest

from coordinationhub.lock_cache import LockCache


class TestLockCache:
    def test_add_and_get_status(self):
        cache = LockCache()
        cache.add_lock({
            "document_path": "/a.py",
            "locked_by": "agent1",
            "locked_at": time.time(),
            "lock_ttl": 300.0,
            "lock_type": "exclusive",
            "region_start": None,
            "region_end": None,
            "worktree_root": "/proj",
        })
        status = cache.get_status("/a.py", time.time())
        assert status["locked"] is True
        assert status["locked_by"] == "agent1"

    def test_get_status_expired(self):
        cache = LockCache()
        cache.add_lock({
            "document_path": "/a.py",
            "locked_by": "agent1",
            "locked_at": time.time() - 10.0,
            "lock_ttl": 5.0,
            "lock_type": "exclusive",
        })
        status = cache.get_status("/a.py", time.time())
        assert status["locked"] is False

    def test_remove_lock(self):
        cache = LockCache()
        now = time.time()
        cache.add_lock({
            "document_path": "/a.py",
            "locked_by": "agent1",
            "locked_at": now,
            "lock_ttl": 300.0,
            "lock_type": "exclusive",
        })
        assert cache.remove_lock("/a.py", "agent1") is True
        assert cache.get_status("/a.py", time.time())["locked"] is False

    def test_refresh_lock(self):
        cache = LockCache()
        now = time.time()
        cache.add_lock({
            "document_path": "/a.py",
            "locked_by": "agent1",
            "locked_at": now,
            "lock_ttl": 10.0,
            "lock_type": "exclusive",
        })
        assert cache.refresh_lock("/a.py", "agent1", now, 300.0, "exclusive") is True
        status = cache.get_status("/a.py", time.time())
        assert status["locked"] is True
        assert status["expires_at"] > now + 250

    def test_list_active_filtered_by_agent(self):
        cache = LockCache()
        now = time.time()
        cache.add_lock({
            "document_path": "/a.py",
            "locked_by": "agent1",
            "locked_at": now,
            "lock_ttl": 300.0,
            "lock_type": "exclusive",
        })
        cache.add_lock({
            "document_path": "/b.py",
            "locked_by": "agent2",
            "locked_at": now,
            "lock_ttl": 300.0,
            "lock_type": "exclusive",
        })
        locks = cache.list_active(now, agent_id="agent1")
        assert len(locks) == 1
        assert locks[0]["document_path"] == "/a.py"

    def test_conflicting_locks_shared(self):
        cache = LockCache()
        now = time.time()
        cache.add_lock({
            "document_path": "/a.py",
            "locked_by": "agent1",
            "locked_at": now,
            "lock_ttl": 300.0,
            "lock_type": "shared",
        })
        # Another shared lock should not conflict
        conflicts = cache.list_conflicting_locks("/a.py", "agent2", "shared")
        assert len(conflicts) == 0
        # An exclusive lock should conflict
        conflicts = cache.list_conflicting_locks("/a.py", "agent2", "exclusive")
        assert len(conflicts) == 1

    def test_conflicting_locks_region_overlap(self):
        cache = LockCache()
        now = time.time()
        cache.add_lock({
            "document_path": "/a.py",
            "locked_by": "agent1",
            "locked_at": now,
            "lock_ttl": 300.0,
            "lock_type": "exclusive",
            "region_start": 10,
            "region_end": 20,
        })
        # Non-overlapping region should not conflict
        conflicts = cache.list_conflicting_locks("/a.py", "agent2", "exclusive", 21, 30)
        assert len(conflicts) == 0
        # Overlapping region should conflict
        conflicts = cache.list_conflicting_locks("/a.py", "agent2", "exclusive", 15, 25)
        assert len(conflicts) == 1

    def test_remove_by_agent(self):
        cache = LockCache()
        now = time.time()
        cache.add_lock({
            "document_path": "/a.py",
            "locked_by": "agent1",
            "locked_at": now,
            "lock_ttl": 300.0,
            "lock_type": "exclusive",
        })
        cache.add_lock({
            "document_path": "/b.py",
            "locked_by": "agent1",
            "locked_at": now,
            "lock_ttl": 300.0,
            "lock_type": "exclusive",
        })
        removed = cache.remove_by_agent("agent1")
        assert removed == 2
        assert cache.get_status("/a.py", time.time())["locked"] is False
        assert cache.get_status("/b.py", time.time())["locked"] is False

    def test_warm_replaces_existing(self):
        cache = LockCache()
        now = time.time()
        cache.add_lock({
            "document_path": "/a.py",
            "locked_by": "agent1",
            "locked_at": now,
            "lock_ttl": 300.0,
            "lock_type": "exclusive",
        })
        cache.warm([{
            "document_path": "/b.py",
            "locked_by": "agent2",
            "locked_at": now,
            "lock_ttl": 300.0,
            "lock_type": "shared",
            "region_start": None,
            "region_end": None,
            "worktree_root": "/proj",
        }])
        assert cache.get_status("/a.py", time.time())["locked"] is False
        assert cache.get_status("/b.py", time.time())["locked"] is True
