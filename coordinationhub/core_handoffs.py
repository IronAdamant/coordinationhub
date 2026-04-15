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
    ) -> dict[str, Any]:
        """Wait until a handoff is acknowledged and completed or timeout expires.

        Uses the event bus for low-latency notification.
        Returns {"timed_out": False, "handoff_id": ...} on success,
        or {"timed_out": True, ...} on timeout.
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

        event = self._hybrid_wait(
            ["handoff.completed"],
            filter_fn=lambda e: e.get("handoff_id") == handoff_id,
            timeout=timeout_s,
        )
        if event:
            return {"timed_out": False, "handoff_id": handoff_id}
        return {"timed_out": True, "handoff_id": handoff_id}
