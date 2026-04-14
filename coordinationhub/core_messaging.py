"""MessagingMixin — inter-agent messages and await.

Expects the host class to provide:
    self._connect() — callable returning a sqlite3 connection

Delegates to: messages (messages.py)
"""

from __future__ import annotations

import time as _time
from typing import Any

from . import messages as _msg
from . import broadcasts as _bc


class MessagingMixin:
    """Inter-agent message passing and agent await."""

    # ------------------------------------------------------------------ #
    # Messaging
    # ------------------------------------------------------------------ #

    def send_message(
        self,
        from_agent_id: str,
        to_agent_id: str,
        message_type: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a message to another agent."""
        return _msg.send_message(self._connect, from_agent_id, to_agent_id, message_type, payload)

    def get_messages(
        self, agent_id: str, unread_only: bool = False, limit: int = 50,
    ) -> dict[str, Any]:
        """Get messages for an agent."""
        messages = _msg.get_messages(self._connect, agent_id, unread_only, limit)
        # Auto-acknowledge broadcast requests so non-interactive agents
        # don't leave pending_acks dangling indefinitely.
        for msg in messages:
            if msg.get("message_type") == "broadcast_ack_request":
                payload = msg.get("payload") or {}
                broadcast_id = payload.get("broadcast_id")
                if broadcast_id is not None:
                    _bc.acknowledge_broadcast(self._connect, broadcast_id, agent_id)
        return {"messages": messages, "count": len(messages)}

    def mark_messages_read(
        self, agent_id: str, message_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """Mark messages as read."""
        return _msg.mark_messages_read(self._connect, agent_id, message_ids)

    def await_agent(self, agent_id: str, timeout_s: float = 60.0) -> dict[str, Any]:
        """Wait for an agent to deregister (complete its work).

        Polls agent status until the agent is stopped or timeout expires.
        """
        start = _time.time()
        poll_interval = 2.0
        while _time.time() - start < timeout_s:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT status FROM agents WHERE agent_id = ?", (agent_id,)
                ).fetchone()
                if row is None or row["status"] == "stopped":
                    return {
                        "awaited": True,
                        "agent_id": agent_id,
                        "status": row["status"] if row else "not_found",
                        "waited_s": _time.time() - start,
                    }
            remaining = timeout_s - (_time.time() - start)
            if remaining <= 0:
                break
            _time.sleep(min(poll_interval, remaining))
        return {
            "awaited": False,
            "agent_id": agent_id,
            "status": "timeout",
            "timeout_s": timeout_s,
        }