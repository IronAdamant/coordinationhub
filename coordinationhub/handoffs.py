"""Handoff recording and acknowledgement primitives for CoordinationHub.

Supports one-to-many handoffs with acknowledgment tracking.
When an agent broadcasts to multiple recipients via handoff_targets,
a formal handoff is recorded and each target must acknowledge.

Zero internal dependencies — receives connect() from caller.
"""

from __future__ import annotations

import json
import time
from typing import Any

from .db import ConnectFn


# T1.15: legal state transitions. Keys are current status, values are
# allowed target statuses.
_LEGAL_TRANSITIONS = {
    "pending": {"partially_acknowledged", "acknowledged", "cancelled"},
    "partially_acknowledged": {"partially_acknowledged", "acknowledged", "cancelled"},
    "acknowledged": {"completed", "cancelled"},
    "completed": set(),  # terminal
    "cancelled": set(),  # terminal
}


def record_handoff(
    connect: ConnectFn,
    from_agent_id: str,
    to_agents: list[str],
    document_path: str | None = None,
    handoff_type: str = "scope_transfer",
) -> dict[str, Any]:
    """Record a formal multi-recipient handoff."""
    now = time.time()
    with connect() as conn:
        cursor = conn.execute(
            """INSERT INTO handoffs
            (from_agent_id, to_agents, document_path, handoff_type, created_at)
            VALUES (?, ?, ?, ?, ?)""",
            (from_agent_id, json.dumps(to_agents), document_path, handoff_type, now),
        )
        handoff_id = cursor.lastrowid
    return {"recorded": True, "handoff_id": handoff_id}


def acknowledge_handoff(
    connect: ConnectFn,
    handoff_id: int,
    agent_id: str,
) -> dict[str, Any]:
    """Acknowledge receipt of a handoff.

    T1.15: only an agent listed in ``to_agents`` may ack. Aggregate status
    becomes ``'acknowledged'`` only when ALL recipients have acked; until
    then it is ``'partially_acknowledged'``. This fixes the previous
    behaviour where a single ack flipped the aggregate status to
    ``'acknowledged'`` even when other recipients hadn't responded.
    """
    now = time.time()
    with connect() as conn:
        row = conn.execute(
            "SELECT to_agents, status FROM handoffs WHERE id = ?",
            (handoff_id,),
        ).fetchone()
        if row is None:
            return {
                "acknowledged": False,
                "reason": "not_found",
                "handoff_id": handoff_id,
            }
        try:
            to_agents = json.loads(row["to_agents"]) if row["to_agents"] else []
        except json.JSONDecodeError:
            to_agents = []
        if agent_id not in to_agents:
            return {
                "acknowledged": False,
                "reason": "not_recipient",
                "handoff_id": handoff_id,
                "agent_id": agent_id,
            }
        current_status = row["status"] or "pending"
        if current_status in ("completed", "cancelled"):
            return {
                "acknowledged": False,
                "reason": f"illegal_transition_from_{current_status}",
                "handoff_id": handoff_id,
            }

        conn.execute(
            """INSERT OR IGNORE INTO handoff_acks
            (handoff_id, agent_id, acknowledged_at)
            VALUES (?, ?, ?)""",
            (handoff_id, agent_id, now),
        )

        # Count distinct acks after insert.
        ack_count = conn.execute(
            "SELECT COUNT(DISTINCT agent_id) AS n FROM handoff_acks WHERE handoff_id = ?",
            (handoff_id,),
        ).fetchone()["n"]

        expected = len(to_agents)
        if expected > 0 and ack_count >= expected:
            conn.execute(
                "UPDATE handoffs SET status='acknowledged', acknowledged_at=? WHERE id=?",
                (now, handoff_id),
            )
            aggregate = "acknowledged"
        else:
            conn.execute(
                "UPDATE handoffs SET status='partially_acknowledged' WHERE id=?",
                (handoff_id,),
            )
            aggregate = "partially_acknowledged"

    return {
        "acknowledged": True,
        "handoff_id": handoff_id,
        "agent_id": agent_id,
        "status": aggregate,
        "acks": ack_count,
        "expected": expected,
    }


def complete_handoff(
    connect: ConnectFn,
    handoff_id: int,
) -> dict[str, Any]:
    """Mark a handoff as completed.

    T1.15 + T1.19: verifies the handoff exists and its current status
    permits the transition. Returns ``{"completed": False, "reason": ...}``
    on not-found or illegal-transition instead of phantom-success.
    """
    now = time.time()
    with connect() as conn:
        row = conn.execute(
            "SELECT status FROM handoffs WHERE id = ?",
            (handoff_id,),
        ).fetchone()
        if row is None:
            return {
                "completed": False,
                "reason": "not_found",
                "handoff_id": handoff_id,
            }
        current = row["status"] or "pending"
        if "completed" not in _LEGAL_TRANSITIONS.get(current, set()):
            return {
                "completed": False,
                "reason": f"illegal_transition_from_{current}",
                "handoff_id": handoff_id,
            }
        conn.execute(
            "UPDATE handoffs SET status='completed', completed_at=? WHERE id=?",
            (now, handoff_id),
        )
    return {"completed": True, "handoff_id": handoff_id}


def cancel_handoff(
    connect: ConnectFn,
    handoff_id: int,
) -> dict[str, Any]:
    """Cancel a handoff.

    T1.15 + T1.19: verifies existence and state-machine legality.
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT status FROM handoffs WHERE id = ?",
            (handoff_id,),
        ).fetchone()
        if row is None:
            return {
                "cancelled": False,
                "reason": "not_found",
                "handoff_id": handoff_id,
            }
        current = row["status"] or "pending"
        if "cancelled" not in _LEGAL_TRANSITIONS.get(current, set()):
            return {
                "cancelled": False,
                "reason": f"illegal_transition_from_{current}",
                "handoff_id": handoff_id,
            }
        conn.execute(
            "UPDATE handoffs SET status='cancelled' WHERE id=?",
            (handoff_id,),
        )
    return {"cancelled": True, "handoff_id": handoff_id}


def get_handoffs(
    connect: ConnectFn,
    status: str | None = None,
    from_agent_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Get handoffs with optional status/from_agent_id filtering."""
    with connect() as conn:
        query = "SELECT * FROM handoffs WHERE 1=1"
        args: list[Any] = []
        if status is not None:
            query += " AND status=?"
            args.append(status)
        if from_agent_id is not None:
            query += " AND from_agent_id=?"
            args.append(from_agent_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        rows = conn.execute(query, args).fetchall()
        handoffs = []
        for r in rows:
            d = dict(r)
            try:
                d["to_agents"] = json.loads(d["to_agents"])
            except (json.JSONDecodeError, TypeError):
                d["to_agents"] = []
            handoffs.append(d)
    return handoffs
