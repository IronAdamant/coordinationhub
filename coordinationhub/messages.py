"""Inter-agent messaging primitives for CoordinationHub.

Supports direct message passing between agents with payload, type, and timestamps.
Zero internal dependencies — receives connect() from caller.
"""

from __future__ import annotations

import json
import time
from typing import Any

from .db import ConnectFn


def send_message(
    connect: ConnectFn,
    from_agent_id: str,
    to_agent_id: str,
    message_type: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send a message to another agent. Returns the message ID."""
    now = time.time()
    payload_json = json.dumps(payload) if payload is not None else None
    with connect() as conn:
        cursor = conn.execute(
            """INSERT INTO messages
            (from_agent_id, to_agent_id, message_type, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)""",
            (from_agent_id, to_agent_id, message_type, payload_json, now),
        )
        return {"sent": True, "message_id": cursor.lastrowid}


def get_messages(
    connect: ConnectFn,
    agent_id: str,
    unread_only: bool = False,
    limit: int = 50,
    since_id: int | None = None,
) -> list[dict[str, Any]]:
    """Get messages for an agent, optionally filtering to unread only.

    T6.25: ``since_id`` is a monotonic cursor — pass the highest
    ``id`` from the previous batch and subsequent calls return only
    messages with a strictly greater id. Timestamp ordering is kept
    for display (newest first), but the filter lets pollers make
    progress without relying on indeterminate ``ORDER BY created_at``
    tie-breaks.
    """
    clauses = ["to_agent_id = ?"]
    params: list[Any] = [agent_id]
    if unread_only:
        clauses.append("read_at IS NULL")
    if since_id is not None:
        clauses.append("id > ?")
        params.append(since_id)
    where = " AND ".join(clauses)
    params.append(limit)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM messages WHERE {where} "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            tuple(params),
        ).fetchall()
        messages = []
        for row in rows:
            d = dict(row)
            if d["payload_json"]:
                d["payload"] = json.loads(d["payload_json"])
            else:
                d["payload"] = None
            del d["payload_json"]
            messages.append(d)
        return messages


def mark_messages_read(
    connect: ConnectFn,
    agent_id: str,
    message_ids: list[int] | None = None,
) -> dict[str, int]:
    """Mark messages as read. If message_ids is None, mark all unread for agent."""
    now = time.time()
    with connect() as conn:
        if message_ids is None:
            cursor = conn.execute(
                "UPDATE messages SET read_at = ? WHERE to_agent_id = ? AND read_at IS NULL",
                (now, agent_id),
            )
        else:
            placeholders = ",".join("?" * len(message_ids))
            cursor = conn.execute(
                f"UPDATE messages SET read_at = ? WHERE to_agent_id = ? AND id IN ({placeholders})",
                [now, agent_id] + message_ids,
            )
        return {"marked_read": cursor.rowcount}


def count_unread(
    connect: ConnectFn,
    agent_id: str,
) -> int:
    """Count unread messages for an agent."""
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM messages WHERE to_agent_id = ? AND read_at IS NULL",
            (agent_id,),
        ).fetchone()
        return row["cnt"] if row else 0
