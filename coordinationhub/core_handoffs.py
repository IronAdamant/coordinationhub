"""HandoffMixin — one-to-many handoff acknowledgment and lifecycle.

Expects the host class to provide:
    self._connect() — callable returning a sqlite3 connection

Delegates to: handoffs (handoffs.py)
"""

from __future__ import annotations

from typing import Any

from . import handoffs as _handoffs


class HandoffMixin:
    """Formal handoff recording with multi-recipient acknowledgment tracking."""

    def acknowledge_handoff(self, handoff_id: int, agent_id: str) -> dict[str, Any]:
        """Acknowledge receipt of a handoff."""
        return _handoffs.acknowledge_handoff(self._connect, handoff_id, agent_id)

    def complete_handoff(self, handoff_id: int) -> dict[str, Any]:
        """Mark a handoff as completed."""
        return _handoffs.complete_handoff(self._connect, handoff_id)

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