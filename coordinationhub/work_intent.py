"""Work intent board primitives for CoordinationHub.

A lightweight cooperative signal: before attempting a lock, an agent declares
intent to work on a file. Other agents checking the board receive a
proximity_warning (not a denial) when their intended lock conflicts.

T1.16: the primary key is now ``(agent_id, document_path)`` so one agent
can declare intent on multiple files simultaneously. Conflict detection
also honours read/write semantics — two readers of the same file do not
conflict; read vs write and write vs write both do.

Zero internal dependencies — receives connect() from caller.
"""

from __future__ import annotations

import time
from typing import Any

from .db import ConnectFn


# T1.16: canonical intent categories. "read" is the only non-conflicting
# category against other readers. Everything else is treated as writing
# to preserve cooperative pessimism for unknown intent strings.
_READ_INTENTS = {"read", "review", "watch", "observe"}


def _is_read_intent(intent: str | None) -> bool:
    """True if the declared intent represents a read-only observation."""
    if not intent:
        return False
    return intent.strip().lower() in _READ_INTENTS


def upsert_intent(
    connect: ConnectFn,
    agent_id: str,
    document_path: str,
    intent: str,
    ttl: float = 60.0,
) -> dict[str, Any]:
    """Declare intent to work on a file.

    T1.16: the ON CONFLICT key is now the compound PK
    ``(agent_id, document_path)``. A second declare for the same agent
    on a different file inserts a new row rather than clobbering the
    first.
    """
    now = time.time()
    with connect() as conn:
        conn.execute(
            """INSERT INTO work_intent (agent_id, document_path, intent, declared_at, ttl)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(agent_id, document_path) DO UPDATE SET
                intent = excluded.intent,
                declared_at = excluded.declared_at,
                ttl = excluded.ttl""",
            (agent_id, document_path, intent, now, ttl),
        )
    return {"recorded": True, "agent_id": agent_id, "document_path": document_path}


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


def clear_intent(
    connect: ConnectFn,
    agent_id: str,
    document_path: str | None = None,
) -> dict[str, Any]:
    """Clear an agent's declared intent.

    T1.16: when ``document_path`` is supplied only that specific intent is
    cleared. When omitted, every intent row for the agent is cleared
    (preserving the pre-v23 semantic).
    """
    with connect() as conn:
        if document_path is None:
            cursor = conn.execute(
                "DELETE FROM work_intent WHERE agent_id=?", (agent_id,),
            )
        else:
            cursor = conn.execute(
                "DELETE FROM work_intent WHERE agent_id=? AND document_path=?",
                (agent_id, document_path),
            )
    return {
        "cleared": True,
        "agent_id": agent_id,
        "document_path": document_path,
        "rows_cleared": cursor.rowcount,
    }


def check_intent_conflict(
    connect: ConnectFn,
    document_path: str,
    exclude_agent_id: str | None = None,
    requesting_intent: str | None = None,
) -> list[dict[str, Any]]:
    """Return live conflicting intents for a document path.

    T1.16: ``requesting_intent`` classifies the caller's own access mode.
    When both the caller and a live row are read-class intents,
    the row is not a conflict. When either side is a write-class intent
    (or unknown), the row is returned so the lock-layer can warn.

    Legacy callers that do not supply ``requesting_intent`` get the prior
    behaviour — every live intent on the file is returned.
    """
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
        result = [dict(r) for r in rows]

    if requesting_intent is None or not _is_read_intent(requesting_intent):
        # Caller is writing (or mode is unknown) — every live intent
        # conflicts, even other readers.
        return result
    # Caller is a reader: filter out other readers; keep writers.
    return [r for r in result if not _is_read_intent(r.get("intent"))]


def prune_expired_intents(connect: ConnectFn) -> dict[str, Any]:
    """Delete rows whose ``declared_at + ttl <= now``.

    T1.16: work_intent has no query-time delete of expired rows so the
    table grows unbounded when callers forget to clear. Engine callers
    should invoke this periodically (at start-up and on a timer).
    """
    now = time.time()
    with connect() as conn:
        cursor = conn.execute(
            "DELETE FROM work_intent WHERE declared_at + ttl <= ?",
            (now,),
        )
    return {"pruned": cursor.rowcount}
