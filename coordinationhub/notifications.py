"""Change notification storage and retrieval for CoordinationHub.

Zero internal dependencies — receives connect() from caller.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from .db import ConnectFn


def notify_change(
    connect: ConnectFn,
    document_path: str,
    change_type: str,
    agent_id: str,
    worktree_root: str | None = None,
) -> dict[str, Any]:
    """Record a change event for other agents to poll."""
    now = time.time()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO change_notifications
            (document_path, change_type, agent_id, worktree_root, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (document_path, change_type, agent_id, worktree_root, now),
        )
    return {"recorded": True}


def get_notifications(
    connect: ConnectFn,
    since: float | None = None,
    exclude_agent: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Poll for changes since a timestamp."""
    with connect() as conn:
        query = "SELECT * FROM change_notifications WHERE 1=1"
        args: list[Any] = []
        if since is not None:
            query += " AND created_at > ?"
            args.append(since)
        if exclude_agent is not None:
            query += " AND agent_id != ?"
            args.append(exclude_agent)
        query += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        rows = conn.execute(query, args).fetchall()
        return {
            "notifications": [dict(row) for row in rows]
        }


def prune_notifications(
    connect: ConnectFn,
    max_age_seconds: float | None = None,
    max_entries: int | None = None,
) -> dict[str, Any]:
    """Clean up old notifications by age or entry count."""
    with connect() as conn:
        pruned = 0

        if max_age_seconds is not None:
            cutoff = time.time() - max_age_seconds
            cursor = conn.execute(
                "DELETE FROM change_notifications WHERE created_at < ?",
                (cutoff,),
            )
            pruned += cursor.rowcount

        if max_entries is not None:
            count_row = conn.execute(
                "SELECT COUNT(*) as cnt FROM change_notifications"
            ).fetchone()
            count = count_row["cnt"] if count_row else 0
            if count > max_entries:
                excess = count - max_entries
                cursor = conn.execute(
                    """
                    DELETE FROM change_notifications WHERE id IN (
                        SELECT id FROM change_notifications ORDER BY created_at ASC LIMIT ?
                    )
                    """,
                    (excess,),
                )
                pruned += cursor.rowcount

        return {"pruned": pruned}
