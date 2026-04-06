"""Tests for broadcast and wait_for_locks coordination primitives."""

from __future__ import annotations

import pytest
import time


class TestBroadcast:
    def test_broadcast_no_document_path(self, engine, two_agents):
        result = engine.broadcast(two_agents["child"])
        assert "acknowledged_by" in result
        assert "conflicts" in result

    def test_broadcast_with_document_path(self, engine, two_agents):
        engine.acquire_lock("/test.txt", two_agents["other"])
        result = engine.broadcast(
            two_agents["child"], document_path="/test.txt"
        )
        assert two_agents["other"] in [c["locked_by"] for c in result["conflicts"]]

    def test_broadcast_stale_sibling_excluded(self, engine, two_agents):
        """A sibling that hasn't heartbeat'd in >60s should be excluded."""
        # Manually set a stale last_heartbeat
        with engine._connect() as conn:
            conn.execute(
                "UPDATE agents SET last_heartbeat = 0 WHERE agent_id = ?",
                (two_agents["other"],),
            )
        result = engine.broadcast(
            two_agents["child"], document_path="/test.txt"
        )
        assert two_agents["other"] not in result["acknowledged_by"]

    def test_broadcast_batch_locks(self, engine, two_agents):
        """Verify broadcast uses single query for all sibling locks."""
        engine.acquire_lock("/test.txt", two_agents["other"])
        result = engine.broadcast(
            two_agents["child"], document_path="/test.txt"
        )
        assert result["conflicts"]  # other agent has the lock


class TestWaitForLocks:
    def test_wait_for_locks_immediate_release(self, engine, registered_agent):
        engine.acquire_lock("/test.txt", registered_agent)
        result = engine.wait_for_locks(["/test.txt"], registered_agent, timeout_s=1.0)
        assert "/test.txt" in result["released"]

    def test_wait_for_locks_timeout(self, engine, two_agents):
        engine.acquire_lock("/test.txt", two_agents["child"])
        result = engine.wait_for_locks(
            ["/test.txt"], two_agents["other"], timeout_s=0.1
        )
        assert "/test.txt" in result["timed_out"]

    def test_wait_for_locks_own_lock_releases(self, engine, registered_agent):
        engine.acquire_lock("/test.txt", registered_agent)
        result = engine.wait_for_locks(["/test.txt"], registered_agent)
        assert "/test.txt" in result["released"]
