"""Tests for handoff primitives + HandoffMixin (T1.15 + T1.19 regressions)."""

from __future__ import annotations

import pytest

from coordinationhub import handoffs as _handoffs


class TestAcknowledgeHandoff:
    """T1.15: multi-recipient ack semantics."""

    def test_single_ack_on_multi_recipient_is_partial(self, engine):
        a = engine.generate_agent_id()
        engine.register_agent(a)
        b = engine.generate_agent_id()
        engine.register_agent(b)
        c = engine.generate_agent_id()
        engine.register_agent(c)

        h = _handoffs.record_handoff(engine._connect, a, [b, c])
        hid = h["handoff_id"]

        r1 = engine.acknowledge_handoff(hid, b)
        assert r1["acknowledged"] is True
        assert r1["status"] == "partially_acknowledged", (
            f"Single ack on 2-recipient handoff should be 'partially_acknowledged', "
            f"got {r1['status']}. T1.15 regression."
        )
        # All acks flips to 'acknowledged'
        r2 = engine.acknowledge_handoff(hid, c)
        assert r2["acknowledged"] is True
        assert r2["status"] == "acknowledged"

    def test_ack_by_non_recipient_rejected(self, engine):
        a = engine.generate_agent_id()
        engine.register_agent(a)
        b = engine.generate_agent_id()
        engine.register_agent(b)
        outsider = engine.generate_agent_id()
        engine.register_agent(outsider)

        h = _handoffs.record_handoff(engine._connect, a, [b])
        r = engine.acknowledge_handoff(h["handoff_id"], outsider)
        assert r["acknowledged"] is False
        assert r["reason"] == "not_recipient"

    def test_ack_on_completed_rejected(self, engine):
        a = engine.generate_agent_id()
        engine.register_agent(a)
        b = engine.generate_agent_id()
        engine.register_agent(b)
        h = _handoffs.record_handoff(engine._connect, a, [b])
        engine.acknowledge_handoff(h["handoff_id"], b)
        engine.complete_handoff(h["handoff_id"])

        r = engine.acknowledge_handoff(h["handoff_id"], b)
        assert r["acknowledged"] is False
        assert "illegal_transition" in r["reason"]

    def test_ack_nonexistent_rejected(self, engine):
        r = engine.acknowledge_handoff(99999, "hub.nonexistent.0")
        assert r["acknowledged"] is False
        assert r["reason"] == "not_found"


class TestCompleteHandoff:
    """T1.15 + T1.19: complete must guard state machine + avoid phantom success."""

    def test_complete_nonexistent_returns_error(self, engine):
        r = engine.complete_handoff(99999)
        assert r["completed"] is False
        assert r["reason"] == "not_found"

    def test_complete_cancelled_rejected(self, engine):
        a = engine.generate_agent_id()
        engine.register_agent(a)
        b = engine.generate_agent_id()
        engine.register_agent(b)
        h = _handoffs.record_handoff(engine._connect, a, [b])
        engine.cancel_handoff(h["handoff_id"])

        r = engine.complete_handoff(h["handoff_id"])
        assert r["completed"] is False
        assert "illegal_transition" in r["reason"]

    def test_complete_requires_acknowledged(self, engine):
        """A handoff must be acknowledged before it can be completed."""
        a = engine.generate_agent_id()
        engine.register_agent(a)
        b = engine.generate_agent_id()
        engine.register_agent(b)
        h = _handoffs.record_handoff(engine._connect, a, [b])
        # Try complete on 'pending' (never acked)
        r = engine.complete_handoff(h["handoff_id"])
        assert r["completed"] is False, (
            "T1.15: cannot complete a handoff that was never acknowledged"
        )


class TestCancelHandoff:
    def test_cancel_nonexistent_returns_error(self, engine):
        r = engine.cancel_handoff(99999)
        assert r["cancelled"] is False
        assert r["reason"] == "not_found"

    def test_cancel_completed_rejected(self, engine):
        a = engine.generate_agent_id()
        engine.register_agent(a)
        b = engine.generate_agent_id()
        engine.register_agent(b)
        h = _handoffs.record_handoff(engine._connect, a, [b])
        engine.acknowledge_handoff(h["handoff_id"], b)
        engine.complete_handoff(h["handoff_id"])

        r = engine.cancel_handoff(h["handoff_id"])
        assert r["cancelled"] is False
        assert "illegal_transition" in r["reason"]


class TestPhantomEvents:
    """T1.19: no handoff event must fire for no-op operations."""

    def test_no_event_for_nonexistent_handoff(self, engine):
        """complete_handoff / cancel_handoff on a non-existent id must NOT
        publish a handoff.completed / handoff.cancelled event. Before
        T1.15+T1.19, the primitive returned {"completed": True} regardless
        and core_handoffs would fire the event.
        """
        events: list[dict] = []

        def capture(topic, payload):
            events.append({"topic": topic, "payload": payload})

        # Subscribe to the event bus directly
        sub_id = engine._event_bus.subscribe(["handoff.completed", "handoff.cancelled"])
        try:
            engine.complete_handoff(99999)
            engine.cancel_handoff(99998)
            # Drain events (non-blocking)
            import queue
            drained = []
            while True:
                try:
                    sub = engine._event_bus._subs.get(sub_id)
                    if sub is None:
                        break
                    ev = sub.q.get_nowait()
                    drained.append(ev)
                except Exception:
                    break
            assert drained == [], (
                f"No handoff events should fire for nonexistent handoffs, "
                f"got {drained}"
            )
        finally:
            engine._event_bus.unsubscribe(sub_id)
