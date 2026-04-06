"""Shared lock primitives used by both local locks and coordination locks.

Zero internal dependencies — receives connect() from caller.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from .db import ConnectFn


def refresh_lock(
    conn: sqlite3.Connection,
    table: str,
    document_path: str,
    agent_id: str,
    ttl: float | None = None,
    not_found_reason: str = "not_locked",
) -> dict[str, bool | str | float]:
    """Extend TTL on a lock. Used by both local and shared lock tables."""
    now = time.time()
    row = conn.execute(
        f"SELECT locked_by, locked_at, lock_ttl FROM {table} WHERE document_path = ?",
        (document_path,),
    ).fetchone()

    if row is None:
        return {"refreshed": False, "reason": not_found_reason}
    if row["locked_by"] != agent_id:
        return {"refreshed": False, "reason": "not_owner"}

    new_ttl = ttl if ttl is not None else row["lock_ttl"]
    new_expires = now + new_ttl
    conn.execute(
        f"UPDATE {table} SET locked_at = ?, lock_ttl = ? WHERE document_path = ?",
        (now, new_ttl, document_path),
    )
    return {"refreshed": True, "expires_at": new_expires}


def reap_expired_locks(
    conn: sqlite3.Connection,
    table: str,
) -> dict[str, int]:
    """Clear all expired locks. Uses a single atomic DELETE statement."""
    now = time.time()
    cursor = conn.execute(
        f"DELETE FROM {table} WHERE locked_at + lock_ttl < ?",
        (now,),
    )
    return {"reaped": cursor.rowcount}


def record_conflict(
    conn: sqlite3.Connection,
    table: str,
    document_path: str,
    agent_a: str,
    agent_b: str,
    conflict_type: str,
    resolution: str = "rejected",
    details: dict[str, Any] | None = None,
) -> int | None:
    """Log a conflict event. Returns the inserted row ID."""
    import json
    now = time.time()
    details_json = json.dumps(details) if details else None
    cursor = conn.execute(
        f"""INSERT INTO {table}
        (document_path, agent_a, agent_b, conflict_type, resolution, details_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (document_path, agent_a, agent_b, conflict_type, resolution, details_json, now),
    )
    return cursor.lastrowid


def query_conflicts(
    conn: sqlite3.Connection,
    table: str,
    document_path: str | None = None,
    agent_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Query conflict records."""
    query = f"SELECT * FROM {table} WHERE 1=1"
    args: list[Any] = []
    if document_path is not None:
        query += " AND document_path = ?"
        args.append(document_path)
    if agent_id is not None:
        query += " AND (agent_a = ? OR agent_b = ?)"
        args.extend([agent_id, agent_id])
    query += f" ORDER BY created_at DESC LIMIT {limit}"
    rows = conn.execute(query, args).fetchall()
    return [dict(row) for row in rows]


def release_agent_locks(
    conn: sqlite3.Connection,
    table: str,
    agent_id: str,
    delete: bool = True,
) -> dict[str, int]:
    """Release all locks held by a given agent. Returns rowcount via 'released' key."""
    if delete:
        cursor = conn.execute(
            f"DELETE FROM {table} WHERE locked_by = ?",
            (agent_id,),
        )
    else:
        cursor = conn.execute(
            f"UPDATE {table} SET locked_by = NULL WHERE locked_by = ?",
            (agent_id,),
        )
    return {"released": cursor.rowcount}
