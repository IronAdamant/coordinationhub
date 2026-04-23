"""Broadcast acknowledgment primitives for CoordinationHub.

Supports delivery confirmation for broadcasts without requiring
formal handoffs. Any sibling agent can acknowledge a broadcast,
and the sender can poll for acknowledgment status.

Zero internal dependencies — receives connect() from caller.
"""

from __future__ import annotations

import time
from typing import Any

from .db import ConnectFn


def record_broadcast(
    connect: ConnectFn,
    from_agent_id: str,
    document_path: str | None,
    message: str | None,
    ttl: float,
    expected_count: int,
    targets: list[str] | None = None,
) -> dict[str, Any]:
    """Record a broadcast that requires acknowledgments.

    T1.11: when ``targets`` is provided, the exact set of expected
    recipients is snapshotted into ``broadcast_targets``. This makes
    ``pending_acks`` computable in :func:`get_broadcast_status` as
    ``targets - acks``. Late-joining siblings (registered after the
    broadcast) are not added — they never received the ack_request
    message anyway, so excluding them from ``pending_acks`` is correct.

    ``expected_count`` still parameterises the scalar for back-compat,
    but when ``targets`` is supplied the stored count is derived from
    ``len(targets)`` to guarantee consistency.
    """
    now = time.time()
    if targets is not None:
        expected_count = len(targets)
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO broadcasts
            (from_agent_id, document_path, message, created_at, ttl, expires_at, expected_count)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (from_agent_id, document_path, message, now, ttl, now + ttl, expected_count),
        )
        broadcast_id = cursor.lastrowid
        if targets:
            conn.executemany(
                "INSERT OR IGNORE INTO broadcast_targets (broadcast_id, agent_id) VALUES (?, ?)",
                [(broadcast_id, aid) for aid in targets],
            )
    return {
        "broadcast_id": broadcast_id,
        "from_agent_id": from_agent_id,
        "expires_at": now + ttl,
        "expected_count": expected_count,
    }


def acknowledge_broadcast(
    connect: ConnectFn,
    broadcast_id: int,
    agent_id: str,
) -> dict[str, Any]:
    """Acknowledge receipt of a broadcast."""
    now = time.time()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM broadcasts WHERE id = ? AND expires_at > ?",
            (broadcast_id, now),
        ).fetchone()
        if not row:
            return {"acknowledged": False, "reason": "expired_or_not_found"}

        conn.execute(
            """
            INSERT OR IGNORE INTO broadcast_acks
            (broadcast_id, agent_id, acknowledged_at)
            VALUES (?, ?, ?)
            """,
            (broadcast_id, agent_id, now),
        )
    return {"acknowledged": True, "broadcast_id": broadcast_id, "agent_id": agent_id}


def get_broadcast_status(
    connect: ConnectFn,
    broadcast_id: int,
) -> dict[str, Any]:
    """Get the current acknowledgment status for a broadcast.

    T1.11: ``pending_acks`` is now computed from the snapshot in
    ``broadcast_targets`` minus the rows in ``broadcast_acks``. For
    broadcasts recorded before the snapshot table existed (legacy), the
    list is empty and callers fall back to the scalar ``expected_count``.
    """
    with connect() as conn:
        row = conn.execute("SELECT * FROM broadcasts WHERE id = ?", (broadcast_id,)).fetchone()
        if not row:
            return {"found": False}

        acks = conn.execute(
            "SELECT agent_id FROM broadcast_acks WHERE broadcast_id = ?",
            (broadcast_id,),
        ).fetchall()
        targets = conn.execute(
            "SELECT agent_id FROM broadcast_targets WHERE broadcast_id = ?",
            (broadcast_id,),
        ).fetchall()

        expected_count = row["expected_count"] or 0
        acked = [a["agent_id"] for a in acks]
        acked_set = set(acked)
        pending_acks = [t["agent_id"] for t in targets if t["agent_id"] not in acked_set]
        return {
            "found": True,
            "broadcast_id": broadcast_id,
            "from_agent_id": row["from_agent_id"],
            "document_path": row["document_path"],
            "message": row["message"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "expected_count": expected_count,
            "acknowledged_by": acked,
            "pending_acks": pending_acks,
        }


def get_broadcasts(
    connect: ConnectFn,
    from_agent_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Get broadcasts with optional from_agent_id filtering."""
    with connect() as conn:
        query = "SELECT * FROM broadcasts WHERE 1=1"
        args: list[Any] = []
        if from_agent_id is not None:
            query += " AND from_agent_id=?"
            args.append(from_agent_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        rows = conn.execute(query, args).fetchall()

        broadcasts = []
        for r in rows:
            broadcasts.append(dict(r))
    return broadcasts
