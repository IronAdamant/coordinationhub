"""Tests for handoff primitives + HandoffMixin (T1.15 + T1.19 regressions)."""

from __future__ import annotations

import pytest

from coordinationhub import handoffs as _handoffs
from coordinationhub.broadcast_subsystem import Broadcast as _Broadcast


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


class TestBroadcastHandoffTargets:
    """T6.22 regression: ``broadcast(handoff_targets=...)`` must dispatch
    through ``Broadcast._handoff`` and create a handoff row.

    Pre-T6.22 the private ``BroadcastMixin._handoff(...)`` helper on
    the engine was silently shadowed by ``self._handoff`` — the
    :class:`Handoff` subsystem attribute set in
    ``CoordinationEngine.__init__`` (introduced at step 6, commit
    ``ded641d``). No test exercised the ``handoff_targets`` branch so
    the shadowing went unnoticed for ~6 commits. The Broadcast
    extraction (commit ``fb9e200``) relocated the helper inside the new
    :class:`Broadcast` class where ``self._handoff(...)`` now resolves
    to the bound method on the class — fixing the shadow as a side
    effect. These tests lock that fix in.
    """

    def test_broadcast_with_handoff_targets_creates_handoff_record(
        self, engine, two_agents,
    ):
        parent = two_agents["parent"]
        sibling1 = two_agents["child"]
        sibling2 = two_agents["other"]
        engine.heartbeat(parent)
        engine.heartbeat(sibling1)
        engine.heartbeat(sibling2)

        result = engine.broadcast(
            agent_id=parent,
            handoff_targets=[sibling1, sibling2],
            message="please take over",
        )

        # The handoff_targets branch returns the handoff envelope, not
        # the broadcast envelope — no broadcast_id in the response.
        assert "handoff_id" in result, (
            f"broadcast(handoff_targets=...) must dispatch through "
            f"Broadcast._handoff and return a handoff_id; got {result}. "
            f"If this assertion fails, the BroadcastMixin._handoff "
            f"helper has been re-shadowed by self._handoff (the Handoff "
            f"subsystem attribute). See commit fb9e200 / T6.22."
        )
        assert result["to_agents"] == [sibling1, sibling2]
        assert result["handoff_type"] == "scope_transfer"

        # Handoff row visible via engine.get_handoffs(...) with both
        # siblings as recipients.
        handoffs = engine.get_handoffs(from_agent_id=parent)
        rows = handoffs["handoffs"]
        assert len(rows) == 1, f"Expected 1 handoff row, got {rows}"
        row = rows[0]
        assert row["from_agent_id"] == parent
        assert set(row["to_agents"]) == {sibling1, sibling2}

    def test_broadcast_with_single_handoff_target(self, engine, two_agents):
        """Bonus: single-element handoff_targets list must not raise and
        must still produce a handoff record."""
        parent = two_agents["parent"]
        sibling1 = two_agents["child"]
        engine.heartbeat(parent)
        engine.heartbeat(sibling1)

        result = engine.broadcast(
            agent_id=parent,
            handoff_targets=[sibling1],
            message="solo handoff",
        )
        assert "handoff_id" in result
        assert result["to_agents"] == [sibling1]

        handoffs = engine.get_handoffs(from_agent_id=parent)
        assert handoffs["count"] == 1
        assert handoffs["handoffs"][0]["to_agents"] == [sibling1]

    def test_broadcast_handoff_dispatches_handoff_messages(
        self, engine, two_agents,
    ):
        """The handoff_targets branch sends a 'handoff' message to each
        target. Pre-T6.22 the shadowed helper would have skipped this
        path entirely (or routed through the wrong attribute), so target
        agents would never see the handoff message in their inbox.
        """
        parent = two_agents["parent"]
        sibling1 = two_agents["child"]
        sibling2 = two_agents["other"]
        engine.heartbeat(parent)
        engine.heartbeat(sibling1)
        engine.heartbeat(sibling2)

        engine.broadcast(
            agent_id=parent,
            handoff_targets=[sibling1, sibling2],
        )

        for sibling in (sibling1, sibling2):
            msgs = engine.get_messages(sibling, unread_only=True)
            handoff_msgs = [
                m for m in msgs["messages"] if m["message_type"] == "handoff"
            ]
            assert len(handoff_msgs) == 1, (
                f"Sibling {sibling} should have received exactly 1 "
                f"handoff message, got {handoff_msgs}"
            )

    def test_broadcast_helper_is_not_shadowed_on_engine(self, engine):
        """Direct guard against the original shadowing bug: the
        :class:`Broadcast` subsystem instance on the engine
        (``engine._broadcast``) must own its own ``_handoff`` bound
        method, separate from ``engine._handoff`` (the :class:`Handoff`
        subsystem). If a future refactor merges them or rebinds one
        onto the other, this assertion catches it.
        """
        # The Broadcast instance's ``_handoff`` is a bound method on the
        # Broadcast class.
        bc_handoff = type(engine._broadcast)._handoff
        assert callable(bc_handoff)
        # And the engine's ``_handoff`` attribute is the Handoff
        # subsystem instance, not a method — confirming the shadow no
        # longer affects the broadcast dispatch path.
        assert not callable(engine._handoff) or hasattr(
            engine._handoff, "acknowledge_handoff"
        ), (
            "engine._handoff must be the Handoff subsystem (post-ded641d). "
            "If it has become a callable that does NOT expose "
            "acknowledge_handoff, the shadowing bug has returned."
        )
