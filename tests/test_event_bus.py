"""Tests for the in-memory event bus."""

from __future__ import annotations

import queue

import pytest

from coordinationhub.event_bus import EventBus


class TestEventBus:
    def test_publish_delivers_to_subscriber(self):
        bus = EventBus()
        sub_id, sub = bus.subscribe(["lock.acquired"])
        bus.publish("lock.acquired", {"document_path": "/a.py"})
        event = sub.get(timeout=0.1)
        assert event["document_path"] == "/a.py"
        bus.unsubscribe(sub_id)

    def test_filter_fn_blocks_non_matching_events(self):
        bus = EventBus()
        sub_id, sub = bus.subscribe(
            ["lock.released"],
            filter_fn=lambda e: e.get("document_path") == "/b.py",
        )
        bus.publish("lock.released", {"document_path": "/a.py"})
        bus.publish("lock.released", {"document_path": "/b.py"})
        event = sub.get(timeout=0.1)
        assert event["document_path"] == "/b.py"
        bus.unsubscribe(sub_id)

    def test_wait_for_event_returns_match(self):
        import threading
        bus = EventBus()
        threading.Timer(0.01, lambda: bus.publish("agent.deregistered", {"agent_id": "hub.1"})).start()
        event = bus.wait_for_event(
            ["agent.deregistered"],
            filter_fn=lambda e: e.get("agent_id") == "hub.1",
            timeout=0.1,
        )
        assert event["agent_id"] == "hub.1"

    def test_wait_for_event_returns_none_on_timeout(self):
        bus = EventBus()
        event = bus.wait_for_event(["task.completed"], timeout=0.01)
        assert event is None

    def test_unsubscribe_prevents_delivery(self):
        bus = EventBus()
        sub_id, sub = bus.subscribe(["message.received"])
        bus.unsubscribe(sub_id)
        bus.publish("message.received", {"from": "a"})
        with pytest.raises(queue.Empty):
            sub.get(timeout=0.01)


class TestPublishEventDurability:
    """T1.10: _publish_event journals BEFORE firing the in-mem bus; journal
    failures are logged, not silently swallowed; in-mem publish still fires
    so same-process waiters don't deadlock.
    """

    def test_event_journaled_before_inmem_publish(self, engine):
        """The SQLite journal row must exist BEFORE the in-memory bus fires.

        Deterministic observation: intercept ``EventBus.publish`` and
        record whether the coordination_events row is visible at that
        moment. Pre-fix the bus fires first so the journal row isn't yet
        committed; post-fix the journal commit precedes the bus call.
        """
        journal_visible_at_inmem_publish = {"value": None}
        orig_publish = engine._event_bus.publish

        def wrapped_publish(topic, payload):
            if topic == "t1.10.ordering":
                with engine._connect() as conn:
                    row = conn.execute(
                        "SELECT COUNT(*) as c FROM coordination_events WHERE topic=?",
                        ("t1.10.ordering",),
                    ).fetchone()
                journal_visible_at_inmem_publish["value"] = row["c"] > 0
            return orig_publish(topic, payload)

        engine._event_bus.publish = wrapped_publish  # type: ignore[assignment]
        try:
            engine._publish_event("t1.10.ordering", {"payload": "x"})
        finally:
            engine._event_bus.publish = orig_publish  # type: ignore[assignment]

        assert journal_visible_at_inmem_publish["value"] is True, (
            "in-memory bus fired before journal row committed — "
            "cross-process waiters could miss this event on crash"
        )

    def test_journal_failure_is_logged_not_swallowed(self, engine, caplog):
        """Pre-fix: bare ``except: pass`` silently swallowed all DB errors.
        Post-fix: WARNING is logged and _last_event_journaled=False.
        """
        import logging

        class _Boom:
            def __enter__(self_inner):
                raise RuntimeError("db on fire")

            def __exit__(self_inner, *a):
                return False

        original_connect = engine._connect
        engine._connect = lambda: _Boom()  # type: ignore[method-assign]
        try:
            with caplog.at_level(logging.WARNING, logger="coordinationhub.core"):
                engine._publish_event("t1.10.failtest", {"x": 1})
        finally:
            engine._connect = original_connect  # type: ignore[method-assign]

        assert engine._last_event_journaled is False
        assert any(
            "journal write failed" in r.getMessage()
            and "t1.10.failtest" in r.getMessage()
            for r in caplog.records
        ), f"no journal-failure warning in: {[r.getMessage() for r in caplog.records]}"

    def test_journal_failure_still_fires_inmem_bus(self, engine):
        """Same-process waiters shouldn't hang when the journal write fails."""
        sub_id, sub = engine._event_bus.subscribe(["t1.10.inmem"])

        class _Boom:
            def __enter__(self_inner):
                raise RuntimeError("db on fire")

            def __exit__(self_inner, *a):
                return False

        original_connect = engine._connect
        engine._connect = lambda: _Boom()  # type: ignore[method-assign]
        try:
            engine._publish_event("t1.10.inmem", {"n": 42})
        finally:
            engine._connect = original_connect  # type: ignore[method-assign]

        event = sub.get(timeout=0.5)
        assert event["n"] == 42
        engine._event_bus.unsubscribe(sub_id)
