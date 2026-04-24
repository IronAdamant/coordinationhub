"""Handoff subsystem — one-to-many handoff acknowledgment and lifecycle.

T6.22 sixth step: extracted out of ``core_handoffs.HandoffMixin`` into
a standalone class. Coupling audit confirmed HandoffMixin had zero
cross-mixin method calls and only relied on three pieces of engine
infrastructure — ``_connect``, ``_publish_event``, ``_hybrid_wait`` —
which are now injected as constructor dependencies. Same three-dep
shape as :class:`Spawner` (commit ``1ee46c6``) and :class:`Messaging`
(commit ``d9f84d3``). See commits ``3d1bd48`` (WorkIntent),
``b4a3e6b`` (Lease), and ``d6c8796`` (Dependency) for the other
extractions in this series. This continues breaking the god-object
inheritance chain on ``CoordinationEngine`` without changing
observable behaviour.

Preserves T1.15's caller-vs-row authz check on handoff acknowledgment
— the primitive ``acknowledge_handoff`` in :mod:`handoffs` validates
that the supplied ``agent_id`` appears in the row's ``to_agents``
list, rejecting non-recipients with ``reason='not_recipient'``.
Preserves T1.19's no-phantom-event guarantee: events only fire when
the primitive reports success.

Delegates to: handoffs (handoffs.py) for handoff DB primitives.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from . import handoffs as _handoffs


class Handoff:
    """Formal handoff recording with multi-recipient acknowledgment tracking.

    Constructed by :class:`CoordinationEngine` and exposed as
    ``engine._handoff``. The engine keeps facade methods for each
    public operation so the existing tool API is preserved.
    """

    def __init__(
        self,
        connect_fn: Callable[[], Any],
        publish_event_fn: Callable[[str, dict[str, Any]], None],
        hybrid_wait_fn: Callable[..., dict[str, Any] | None],
    ) -> None:
        self._connect = connect_fn
        self._publish_event = publish_event_fn
        self._hybrid_wait = hybrid_wait_fn

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def acknowledge_handoff(self, handoff_id: int, agent_id: str) -> dict[str, Any]:
        """Acknowledge receipt of a handoff.

        T1.15: the primitive rejects acks from agents not present in the
        handoff row's ``to_agents`` list. The event only fires on a real
        ack (T1.19).
        """
        result = _handoffs.acknowledge_handoff(self._connect, handoff_id, agent_id)
        if result.get("acknowledged"):
            self._publish_event(
                "handoff.ack",
                {"handoff_id": handoff_id, "agent_id": agent_id},
            )
        return result

    def complete_handoff(self, handoff_id: int) -> dict[str, Any]:
        """Mark a handoff as completed."""
        result = _handoffs.complete_handoff(self._connect, handoff_id)
        if result.get("completed"):
            self._publish_event(
                "handoff.completed",
                {"handoff_id": handoff_id},
            )
        return result

    def cancel_handoff(self, handoff_id: int) -> dict[str, Any]:
        """Cancel a handoff."""
        result = _handoffs.cancel_handoff(self._connect, handoff_id)
        if result.get("cancelled"):
            self._publish_event(
                "handoff.cancelled",
                {"handoff_id": handoff_id},
            )
        return result

    def get_handoffs(
        self,
        status: str | None = None,
        from_agent_id: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Get handoffs with optional filtering."""
        handoffs = _handoffs.get_handoffs(self._connect, status, from_agent_id, limit)
        return {"handoffs": handoffs, "count": len(handoffs)}

    def wait_for_handoff(
        self,
        handoff_id: int,
        timeout_s: float = 30.0,
        agent_id: str | None = None,
        mode: str = "completion",
    ) -> dict[str, Any]:
        """Unified handoff operation: status | ack | complete | cancel | completion-wait.

        mode='status' with timeout_s=0 returns the handoff record (replaces getter).
        mode='ack' acknowledges the handoff.
        mode='complete' marks it completed.
        mode='cancel' cancels it.
        mode='completion' waits for completion (default).
        """
        if mode == "status":
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM handoffs WHERE id = ?", (handoff_id,)
                ).fetchone()
                if not row:
                    return {"error": f"Handoff {handoff_id} not found"}
                d = dict(row)
                import json
                d["to_agents"] = json.loads(d["to_agents"]) if d.get("to_agents") else []
                return d
        if mode == "ack":
            if not agent_id:
                return {"error": "agent_id is required for ack"}
            result = _handoffs.acknowledge_handoff(self._connect, handoff_id, agent_id)
            if result.get("acknowledged"):
                self._publish_event(
                    "handoff.ack",
                    {"handoff_id": handoff_id, "agent_id": agent_id},
                )
            return result
        if mode == "complete":
            result = _handoffs.complete_handoff(self._connect, handoff_id)
            if result.get("completed"):
                self._publish_event(
                    "handoff.completed",
                    {"handoff_id": handoff_id},
                )
            return result
        if mode == "cancel":
            result = _handoffs.cancel_handoff(self._connect, handoff_id)
            if result.get("cancelled"):
                self._publish_event(
                    "handoff.cancelled",
                    {"handoff_id": handoff_id},
                )
            return result
        if mode == "completion":
            start = time.time()
            # Fast-path: already completed
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT status FROM handoffs WHERE id = ?",
                    (handoff_id,),
                ).fetchone()
                if row and row["status"] == "completed":
                    return {"timed_out": False, "handoff_id": handoff_id}
            event = self._hybrid_wait(
                ["handoff.completed"],
                filter_fn=lambda e: e.get("handoff_id") == handoff_id,
                timeout=timeout_s,
            )
            if event:
                return {"timed_out": False, "handoff_id": handoff_id}
            return {"timed_out": True, "handoff_id": handoff_id}
        return {"error": f"Unknown mode: {mode!r}"}
