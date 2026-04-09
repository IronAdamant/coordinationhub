"""Concurrent stress tests for CoordinationHub.

Validates that the SQLite WAL-mode backend handles multi-threaded access
correctly for locks, registrations, notifications, and heartbeats.
"""

from __future__ import annotations

import threading
import time

import pytest


class TestConcurrentLocking:
    """Multiple threads competing for locks on the same DB."""

    def test_concurrent_lock_different_files(self, engine):
        """N agents locking N distinct files — all should succeed."""
        n = 10
        agents = []
        for _ in range(n):
            aid = engine.generate_agent_id()
            engine.register_agent(aid)
            agents.append(aid)

        results = [None] * n
        errors = []

        def acquire(idx):
            try:
                results[idx] = engine.acquire_lock(f"/file_{idx}.txt", agents[idx])
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=acquire, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Errors during concurrent lock: {errors}"
        assert all(r["acquired"] is True for r in results)

    def test_concurrent_lock_same_file(self, engine):
        """N agents racing to lock ONE file — exactly one wins."""
        n = 10
        agents = []
        for _ in range(n):
            aid = engine.generate_agent_id()
            engine.register_agent(aid)
            agents.append(aid)

        results = [None] * n
        errors = []

        def acquire(idx):
            try:
                results[idx] = engine.acquire_lock("/contested.txt", agents[idx])
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=acquire, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Errors during contested lock: {errors}"
        winners = [r for r in results if r["acquired"]]
        losers = [r for r in results if not r["acquired"]]
        assert len(winners) == 1
        assert len(losers) == n - 1

    def test_concurrent_lock_release_cycle(self, engine):
        """Agents acquire, release, re-acquire in tight loops."""
        n = 5
        cycles = 20
        agents = []
        for _ in range(n):
            aid = engine.generate_agent_id()
            engine.register_agent(aid)
            agents.append(aid)

        errors = []

        def cycle(idx):
            try:
                path = f"/cycle_{idx}.txt"
                for _ in range(cycles):
                    engine.acquire_lock(path, agents[idx])
                    engine.release_lock(path, agents[idx])
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=cycle, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"Errors during lock cycling: {errors}"


class TestConcurrentRegistration:
    """Multiple threads registering agents simultaneously."""

    def test_concurrent_root_registrations(self, engine):
        """Register N root agents from N threads — all should succeed."""
        n = 20
        results = [None] * n
        errors = []

        def register(idx):
            try:
                aid = engine.generate_agent_id()
                results[idx] = engine.register_agent(aid)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=register, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Errors during concurrent registration: {errors}"
        ids = {r["agent_id"] for r in results if r}
        assert len(ids) == n, f"Expected {n} unique agent IDs, got {len(ids)}"

    def test_concurrent_child_registrations(self, engine, registered_agent):
        """Register N children under the same parent concurrently."""
        n = 10
        results = [None] * n
        errors = []

        def register_child(idx):
            try:
                cid = engine.generate_agent_id(registered_agent)
                results[idx] = engine.register_agent(cid, parent_id=registered_agent)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=register_child, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Errors during child registration: {errors}"
        ids = {r["agent_id"] for r in results if r}
        assert len(ids) == n


class TestConcurrentNotifications:
    """Multiple threads posting and reading notifications."""

    def test_concurrent_notify(self, engine, registered_agent):
        """N threads each post a change notification — all recorded."""
        n = 20
        errors = []

        def notify(idx):
            try:
                engine.notify_change(f"/file_{idx}.txt", "modified", registered_agent)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=notify, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors
        result = engine.get_notifications(limit=100)
        assert len(result["notifications"]) == n

    def test_concurrent_read_write_notifications(self, engine):
        """Writers and readers running simultaneously — no crashes."""
        parent = engine.generate_agent_id()
        engine.register_agent(parent)
        writer = engine.generate_agent_id(parent)
        engine.register_agent(writer, parent_id=parent)
        reader = engine.generate_agent_id(parent)
        engine.register_agent(reader, parent_id=parent)

        errors = []
        read_counts = []

        def write_loop():
            try:
                for i in range(30):
                    engine.notify_change(f"/w_{i}.txt", "modified", writer)
            except Exception as exc:
                errors.append(exc)

        def read_loop():
            try:
                for _ in range(30):
                    result = engine.get_notifications(exclude_agent=reader, limit=50)
                    read_counts.append(len(result["notifications"]))
            except Exception as exc:
                errors.append(exc)

        t_write = threading.Thread(target=write_loop)
        t_read = threading.Thread(target=read_loop)
        t_write.start()
        t_read.start()
        t_write.join(timeout=10)
        t_read.join(timeout=10)

        assert not errors


class TestConcurrentHeartbeats:
    """Multiple agents heartbeating at once."""

    def test_concurrent_heartbeats(self, engine):
        """N agents sending heartbeats concurrently — all succeed."""
        n = 15
        agents = []
        for _ in range(n):
            aid = engine.generate_agent_id()
            engine.register_agent(aid)
            agents.append(aid)

        results = [None] * n
        errors = []

        def heartbeat(idx):
            try:
                results[idx] = engine.heartbeat(agents[idx])
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=heartbeat, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors
        assert all(r["updated"] is True for r in results)
