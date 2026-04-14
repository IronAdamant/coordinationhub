"""HandoffMixin — one-to-many handoff acknowledgment and lifecycle.

Expects the host class to provide:
    self._connect() — callable returning a sqlite3 connection
    self._event_bus  — EventBus instance for pub-sub notifications

Delegates to: handoffs (handoffs.py)
"""

from __future__ import annotations

import time
from typing import Any

from . import handoffs as _handoffs


class HandoffMixin:
    """Formal handoff recording with multi-recipient acknowledgment tracking."""

    def acknowledge_handoff(self, handoff_id: int, agent_id: str) -> dict[str, Any]:
        """Acknowledge receipt of a handoff."""
        result = _handoffs.acknowledge_handoff(self._connect, handoff_id, agent_id)
        if result.get("acknowledged"):
            self._event_bus.publish(
                "handoff.ack",
                {"handoff_id": handoff_id, "agent_id": agent_id},
            )
        return result

    def complete_handoff(self, handoff_id: int) -> dict[str, Any]:
        """Mark a handoff as completed."""
        result = _handoffs.complete_handoff(self._connect, handoff_id)
        if result.get("completed"):
            self._event_bus.publish(
                "handoff.completed",
                {"handoff_id": handoff_id},
            )
        return result

    def cancel_handoff(self, handoff_id: int) -> dict[str, Any]:
        """Cancel a handoff."""
        return _handoffs.cancel_handoff(self._connect, handoff_id)

    def get_handoffs(
        self,
        status: str | None = None,
        from_agent_id: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Get handoffs with optional filtering."""
        handoffs = _handoffs.get_handoffs(self._connect, status, from_agent_id, limit)
        return {"handoffs": handoffs, "count": len(handoffs)}

    def await_handoff_acks(
        self,
        handoff_id: int,
        expected_agents: list[str],
        timeout_s: float = 30.0,
    ) -> dict[str, Any]:
        """Wait until all expected agents have acknowledged a handoff or timeout expires.

        Uses the event bus for low-latency notification of handoff acknowledgments.
        Returns the final acknowledgment status.
        """
        import queue as _queue

        start = time.time()
        acked: set[str] = set()

        # Seed already-acknowledged agents
        handoff_rows = _handoffs.get_handoffs(self._connect, limit=1000)
        for h in handoff_rows:
            if h.get("id") == handoff_id:
                # handoffs.get_handoffs does not expose ack list; poll initial state via DB
                break

        # Quick DB seed of existing acks
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT agent_id FROM handoff_acks WHERE handoff_id = ?",
                (handoff_id,),
            ).fetchall()
            acked = {r["agent_id"] for r in rows}

        expected_set = set(expected_agents)
        if expected_set and acked >= expected_set:
            return {"timed_out": False, "acknowledged_by": list(acked)}

        sub_id, sub = self._event_bus.subscribe(
            ["handoff.ack"],
            filter_fn=lambda e: e.get("handoff_id") == handoff_id,
        )
        try:
            while expected_set and len(acked) < len(expected_set):
                elapsed = time.time() - start
                if elapsed >= timeout_s:
                    break
                try:
                    event = sub.get(timeout=timeout_s - elapsed)
                except _queue.Empty:
                    break
                acked.add(event.get("agent_id"))
        finally:
            self._event_bus.unsubscribe(sub_id)

        return {
            "timed_out": expected_set and len(acked) < len(expected_set),
            "acknowledged_by": list(acked),
        }

    def await_handoff_completion(
        self,
        handoff_id: int,
        timeout_s: float = 30.0,
    ) -> dict[str, Any]:
        """Wait until a handoff is marked completed or timeout expires.

        Uses the event bus for low-latency notification.
        """
        start = time.time()

        # Fast-path: already completed
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM handoffs WHERE id = ?",
                (handoff_id,),
            ).fetchone()
        if row and row["status"] == "completed":
            return {"timed_out": False, "handoff_id": handoff_id}

        event = self._event_bus.wait_for_event(
            ["handoff.completed"],
            filter_fn=lambda e: e.get("handoff_id") == handoff_id,
            timeout=timeout_s,
        )
        if event:
            return {"timed_out": False, "handoff_id": handoff_id}
        return {"timed_out": True, "handoff_id": handoff_id}
