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
    """Acknowledge receipt of a handoff."""
    now = time.time()
    with connect() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO handoff_acks
            (handoff_id, agent_id, acknowledged_at)
            VALUES (?, ?, ?)""",
            (handoff_id, agent_id, now),
        )
        conn.execute(
            "UPDATE handoffs SET status='acknowledged', acknowledged_at=? WHERE id=?",
            (now, handoff_id),
        )
    return {"acknowledged": True, "handoff_id": handoff_id, "agent_id": agent_id}


def complete_handoff(
    connect: ConnectFn,
    handoff_id: int,
) -> dict[str, Any]:
    """Mark a handoff as completed."""
    now = time.time()
    with connect() as conn:
        conn.execute(
            "UPDATE handoffs SET status='completed', completed_at=? WHERE id=?",
            (now, handoff_id),
        )
    return {"completed": True, "handoff_id": handoff_id}


def cancel_handoff(
    connect: ConnectFn,
    handoff_id: int,
) -> dict[str, Any]:
    """Cancel a handoff."""
    with connect() as conn:
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
            d["to_agents"] = json.loads(d["to_agents"])
            handoffs.append(d)
    return handoffs
