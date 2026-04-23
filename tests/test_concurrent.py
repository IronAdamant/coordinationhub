"""Concurrent stress tests for CoordinationHub.

Validates that the SQLite WAL-mode backend handles multi-threaded access
correctly for locks, registrations, notifications, and heartbeats.

All tests use the ``run_concurrent`` helper from conftest (T5.1), which
uses ``threading.Barrier`` to guarantee every worker hits the critical
section in the same scheduler tick. Earlier versions started threads in
a for-loop; by the time thread N-1 started, thread 0 may already have
completed — the tests passed but did not actually exercise concurrency.
"""

from __future__ import annotations

import pytest

from .conftest import run_concurrent


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

        def acquire(idx):
            return engine.acquire_lock(f"/file_{idx}.txt", agents[idx])

        results, errors = run_concurrent(
            n, acquire, args_per_worker=[(i,) for i in range(n)]
        )

        assert not errors, f"Errors during concurrent lock: {errors}"
        assert all(r["acquired"] is True for r in results)

    def test_concurrent_lock_same_file(self, engine):
        """N agents racing to lock ONE file — exactly one wins.

        This is the canonical concurrency invariant: even with all workers
        hitting the same path in one scheduler tick, the acquire primitive
        must serialise them correctly via SQLite's BEGIN IMMEDIATE.
        """
        n = 10
        agents = []
        for _ in range(n):
            aid = engine.generate_agent_id()
            engine.register_agent(aid)
            agents.append(aid)

        def acquire(idx):
            return engine.acquire_lock("/contested.txt", agents[idx])

        results, errors = run_concurrent(
            n, acquire, args_per_worker=[(i,) for i in range(n)]
        )

        assert not errors, f"Errors during contested lock: {errors}"
        winners = [r for r in results if r["acquired"]]
        losers = [r for r in results if not r["acquired"]]
        assert len(winners) == 1, (
            f"Expected exactly 1 winner, got {len(winners)} winners and "
            f"{len(losers)} losers (total {len(results)})"
        )
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

        def cycle(idx):
            path = f"/cycle_{idx}.txt"
            for _ in range(cycles):
                engine.acquire_lock(path, agents[idx])
                engine.release_lock(path, agents[idx])
            return None

        _, errors = run_concurrent(
            n, cycle, args_per_worker=[(i,) for i in range(n)], timeout=30.0
        )

        assert not errors, f"Errors during lock cycling: {errors}"


class TestConcurrentRegistration:
    """Multiple threads registering agents simultaneously."""

    def test_concurrent_root_registrations(self, engine):
        """Register N root agents from N threads — all should succeed with unique IDs."""
        n = 20

        def register(idx):
            aid = engine.generate_agent_id()
            return engine.register_agent(aid)

        results, errors = run_concurrent(
            n, register, args_per_worker=[(i,) for i in range(n)]
        )

        assert not errors, f"Errors during concurrent registration: {errors}"
        ids = {r["agent_id"] for r in results if r}
        assert len(ids) == n, (
            f"Expected {n} unique agent IDs, got {len(ids)} — this indicates "
            f"a race in generate_agent_id/register_agent (see T1.2). "
            f"Collisions: {n - len(ids)}"
        )

    def test_concurrent_child_registrations(self, engine, registered_agent):
        """Register N children under the same parent concurrently."""
        n = 10

        def register_child(idx):
            cid = engine.generate_agent_id(registered_agent)
            return engine.register_agent(cid, parent_id=registered_agent)

        results, errors = run_concurrent(
            n, register_child, args_per_worker=[(i,) for i in range(n)]
        )

        assert not errors, f"Errors during child registration: {errors}"
        ids = {r["agent_id"] for r in results if r}
        assert len(ids) == n, f"Expected {n} unique child IDs, got {len(ids)}"


class TestConcurrentNotifications:
    """Multiple threads posting and reading notifications."""

    def test_concurrent_notify(self, engine, registered_agent):
        """N threads each post a change notification — all recorded."""
        n = 20

        def notify(idx):
            engine.notify_change(f"/file_{idx}.txt", "modified", registered_agent)
            return None

        _, errors = run_concurrent(
            n, notify, args_per_worker=[(i,) for i in range(n)]
        )

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

        def work(role):
            if role == "write":
                for i in range(30):
                    engine.notify_change(f"/w_{i}.txt", "modified", writer)
            else:
                for _ in range(30):
                    engine.get_notifications(exclude_agent=reader, limit=50)
            return None

        _, errors = run_concurrent(
            2, work, args_per_worker=[("write",), ("read",)]
        )

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

        def heartbeat(idx):
            return engine.heartbeat(agents[idx])

        results, errors = run_concurrent(
            n, heartbeat, args_per_worker=[(i,) for i in range(n)]
        )

        assert not errors
        assert all(r["updated"] is True for r in results)
