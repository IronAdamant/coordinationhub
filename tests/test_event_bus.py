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
