"""Messaging subsystem — inter-agent message passing and agent await.

T6.22 fifth step: extracted out of ``core_messaging.MessagingMixin``
into a standalone class. Coupling audit confirmed MessagingMixin had
zero cross-mixin method calls and only relied on three pieces of engine
infrastructure — ``_connect``, ``_publish_event``, ``_hybrid_wait`` —
which are now injected as constructor dependencies. Same three-dep
shape as :class:`Spawner` (see commit ``1ee46c6``). See commits
``3d1bd48`` (WorkIntent), ``b4a3e6b`` (Lease), and ``d6c8796``
(Dependency) for the other extractions in this series. This continues
breaking the god-object inheritance chain on ``CoordinationEngine``
without changing observable behaviour.

Preserves the T2.4 ``caller_agent_id`` security check on both
``send_message`` and ``manage_messages`` — a compromised caller can no
longer forge messages "from" another agent or read another agent's
inbox when the optional caller id is supplied. Preserves the T7.23
dual-path design: ``send_message`` and ``manage_messages(action='send')``
remain functionally equivalent by design.

Delegates to: messages (messages.py) for message DB primitives.
"""

from __future__ import annotations

import time as _time
from typing import Any, Callable

from . import messages as _msg


class Messaging:
    """Inter-agent message passing and agent await.

    Constructed by :class:`CoordinationEngine` and exposed as
    ``engine._messaging``. The engine keeps facade methods for each
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
        since_id: int | None = None,
        caller_agent_id: str | None = None,
    ) -> dict[str, Any]:
        """Unified messaging: send | get | mark_read.

        T2.4: ``caller_agent_id`` (optional) — when supplied, must equal
        ``from_agent_id`` for send and ``agent_id`` for get/mark_read.
        Prevents cross-agent impersonation where a compromised caller
        sends messages "from" another agent or reads another agent's
        inbox. The check is opt-in (omitted caller_agent_id preserves
        the pre-T2.4 permissive behaviour) so existing internal callers
        that already trust the agent_id are unchanged.
        """
        if action == "send":
            if not from_agent_id or not to_agent_id or not message_type:
                return {"error": "from_agent_id, to_agent_id, and message_type are required for send"}
            if caller_agent_id is not None and caller_agent_id != from_agent_id:
                return {
                    "sent": False,
                    "error": "caller_agent_id does not match from_agent_id",
                    "reason": "caller_mismatch",
                }
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
            # T2.4: when caller_agent_id is supplied it must equal
            # agent_id — an agent can't read another's inbox. Without
            # this, a compromised agent could siphon messages routed to
            # a sibling.
            if caller_agent_id is not None and caller_agent_id != agent_id:
                return {
                    "messages": [],
                    "count": 0,
                    "error": "caller_agent_id does not match agent_id",
                    "reason": "caller_mismatch",
                }
            # T6.24: don't auto-ack on read. Acknowledgement must be an
            # explicit action (manage_messages action='ack_broadcast' or
            # the dedicated acknowledge_broadcast engine method); a
            # crash between fetching the message and acting on it no
            # longer produces a ghost-ack. Callers that want the old
            # implicit-ack semantics can opt in via auto_ack=True.
            # T6.25: ``since_id`` supports incremental polling.
            messages = _msg.get_messages(
                self._connect, agent_id, unread_only, limit, since_id=since_id,
            )
            return {"messages": messages, "count": len(messages)}
        if action == "mark_read":
            if caller_agent_id is not None and caller_agent_id != agent_id:
                return {
                    "marked_read": 0,
                    "error": "caller_agent_id does not match agent_id",
                    "reason": "caller_mismatch",
                }
            return _msg.mark_messages_read(self._connect, agent_id, message_ids)
        return {"error": f"Unknown action: {action!r}"}

    def send_message(
        self,
        from_agent_id: str,
        to_agent_id: str,
        message_type: str,
        payload: dict[str, Any] | None = None,
        caller_agent_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a message to another agent.

        T2.4: ``caller_agent_id`` (optional) — when supplied, must equal
        ``from_agent_id``. Prevents a compromised caller from forging a
        message "from" another agent. Omitted = pre-T2.4 trust model.
        """
        if caller_agent_id is not None and caller_agent_id != from_agent_id:
            return {
                "sent": False,
                "error": "caller_agent_id does not match from_agent_id",
                "reason": "caller_mismatch",
            }
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
        since_id: int | None = None,
    ) -> dict[str, Any]:
        """Get messages for an agent.

        T6.24: read is now read-only. Acknowledging a broadcast must be
        an explicit call to ``acknowledge_broadcast`` — a crash between
        fetching and acting on a message no longer ghost-acks it.

        T6.25: ``since_id`` enables cursor-based incremental polling;
        pass the previous batch's highest id to get only newer messages.
        """
        messages = _msg.get_messages(
            self._connect, agent_id, unread_only, limit, since_id=since_id,
        )
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
