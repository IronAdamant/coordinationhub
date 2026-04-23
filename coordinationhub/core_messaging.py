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

    def manage_messages(
        self,
        action: str,
        agent_id: str,
        from_agent_id: str | None = None,
        to_agent_id: str | None = None,
        message_type: str | None = None,
        payload: dict[str, Any] | None = None,
        unread_only: bool = False,
        limit: int = 50,
        message_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """Unified messaging: send | get | mark_read."""
        if action == "send":
            if not from_agent_id or not to_agent_id or not message_type:
                return {"error": "from_agent_id, to_agent_id, and message_type are required for send"}
            result = _msg.send_message(self._connect, from_agent_id, to_agent_id, message_type, payload)
            self._publish_event(
                "message.received",
                {
                    "message_id": result.get("message_id"),
                    "from_agent_id": from_agent_id,
                    "to_agent_id": to_agent_id,
                    "message_type": message_type,
                },
            )
            return result
        if action == "get":
            # T6.24: don't auto-ack on read. Acknowledgement must be an
            # explicit action (manage_messages action='ack_broadcast' or
            # the dedicated acknowledge_broadcast engine method); a
            # crash between fetching the message and acting on it no
            # longer produces a ghost-ack. Callers that want the old
            # implicit-ack semantics can opt in via auto_ack=True.
            messages = _msg.get_messages(self._connect, agent_id, unread_only, limit)
            return {"messages": messages, "count": len(messages)}
        if action == "mark_read":
            return _msg.mark_messages_read(self._connect, agent_id, message_ids)
        return {"error": f"Unknown action: {action!r}"}

    def send_message(
        self,
        from_agent_id: str,
        to_agent_id: str,
        message_type: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a message to another agent."""
        result = _msg.send_message(self._connect, from_agent_id, to_agent_id, message_type, payload)
        self._publish_event(
            "message.received",
            {
                "message_id": result.get("message_id"),
                "from_agent_id": from_agent_id,
                "to_agent_id": to_agent_id,
                "message_type": message_type,
            },
        )
        return result

    def get_messages(
        self, agent_id: str, unread_only: bool = False, limit: int = 50,
    ) -> dict[str, Any]:
        """Get messages for an agent.

        T6.24: read is now read-only. Acknowledging a broadcast must be
        an explicit call to ``acknowledge_broadcast`` — a crash between
        fetching and acting on a message no longer ghost-acks it.
        """
        messages = _msg.get_messages(self._connect, agent_id, unread_only, limit)
        return {"messages": messages, "count": len(messages)}

    def mark_messages_read(
        self, agent_id: str, message_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """Mark messages as read."""
        return _msg.mark_messages_read(self._connect, agent_id, message_ids)

    def await_agent(self, agent_id: str, timeout_s: float = 60.0) -> dict[str, Any]:
        """Wait for an agent to deregister (complete its work).

        Uses the event bus for low-latency notification.
        """
        start = _time.time()
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

        event = self._hybrid_wait(
            ["agent.deregistered"],
            filter_fn=lambda e: e.get("agent_id") == agent_id,
            timeout=timeout_s,
        )
        if event:
            return {
                "awaited": True,
                "agent_id": agent_id,
                "status": "stopped",
                "waited_s": _time.time() - start,
            }
        return {
            "awaited": False,
            "agent_id": agent_id,
            "status": "timeout",
            "timeout_s": timeout_s,
        }