"""Work intent board primitives for CoordinationHub.

A lightweight cooperative signal: before attempting a lock, an agent declares
intent to work on a file. Other agents checking the board receive a
proximity_warning (not a denial) when their intended lock conflicts.

Zero internal dependencies — receives connect() from caller.
"""

from __future__ import annotations

import time
from typing import Any

from .db import ConnectFn


def upsert_intent(
    connect: ConnectFn,
    agent_id: str,
    document_path: str,
    intent: str,
    ttl: float = 60.0,
) -> dict[str, Any]:
    """Declare intent to work on a file."""
    now = time.time()
    with connect() as conn:
        conn.execute(
            """INSERT INTO work_intent (agent_id, document_path, intent, declared_at, ttl)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                document_path = excluded.document_path,
                intent = excluded.intent,
                declared_at = excluded.declared_at,
                ttl = excluded.ttl""",
            (agent_id, document_path, intent, now, ttl),
        )
    return {"recorded": True, "agent_id": agent_id}


def get_live_intents(
    connect: ConnectFn,
    agent_id: str | None = None,
) -> list[dict[str, Any]]:
    """Get all live (non-expired) work intents, optionally filtered by agent."""
    now = time.time()
    with connect() as conn:
        if agent_id is not None:
            rows = conn.execute(
                """SELECT * FROM work_intent
                WHERE agent_id=? AND declared_at + ttl > ?""",
                (agent_id, now),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM work_intent
                WHERE declared_at + ttl > ? ORDER BY declared_at DESC""",
                (now,),
            ).fetchall()
    return [dict(r) for r in rows]


def clear_intent(connect: ConnectFn, agent_id: str) -> dict[str, Any]:
    """Clear an agent's declared intent."""
    with connect() as conn:
        conn.execute("DELETE FROM work_intent WHERE agent_id=?", (agent_id,))
    return {"cleared": True, "agent_id": agent_id}


def check_intent_conflict(
    connect: ConnectFn,
    document_path: str,
    exclude_agent_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return live intents for a document path, excluding the given agent."""
    now = time.time()
    with connect() as conn:
        if exclude_agent_id is not None:
            rows = conn.execute(
                """SELECT * FROM work_intent
                WHERE document_path=? AND agent_id!=? AND declared_at + ttl > ?""",
                (document_path, exclude_agent_id, now),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM work_intent
                WHERE document_path=? AND declared_at + ttl > ?""",
                (document_path, now),
            ).fetchall()
    return [dict(r) for r in rows]
