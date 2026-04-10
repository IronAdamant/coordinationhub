"""Shared lock primitives used by both local locks and coordination locks.

Supports file-level and region-level locking with shared/exclusive semantics.
Zero internal dependencies — receives connect() from caller.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from .db import ConnectFn


def _regions_overlap(
    a_start: int | None, a_end: int | None,
    b_start: int | None, b_end: int | None,
) -> bool:
    """Check if two lock regions overlap.

    None means whole-file — overlaps with everything.
    """
    if a_start is None or b_start is None:
        return True
    return a_start < b_end and b_start < a_end


def find_conflicting_locks(
    conn: sqlite3.Connection,
    table: str,
    document_path: str,
    agent_id: str,
    lock_type: str,
    region_start: int | None,
    region_end: int | None,
) -> list[dict[str, Any]]:
    """Return existing locks that conflict with a proposed lock.

    Two locks conflict if:
    - They are on the same document_path
    - Their regions overlap (or either is whole-file)
    - At least one is exclusive
    - They are held by different agents
    """
    now = time.time()
    rows = conn.execute(
        f"SELECT * FROM {table} WHERE document_path = ? AND locked_at + lock_ttl > ?",
        (document_path, now),
    ).fetchall()

    conflicts = []
    for row in rows:
        if row["locked_by"] == agent_id:
            continue
        # Two shared locks never conflict
        if row["lock_type"] == "shared" and lock_type == "shared":
            continue
        if _regions_overlap(
            row["region_start"], row["region_end"],
            region_start, region_end,
        ):
            conflicts.append(dict(row))
    return conflicts


def find_own_lock(
    conn: sqlite3.Connection,
    table: str,
    document_path: str,
    agent_id: str,
    region_start: int | None,
    region_end: int | None,
) -> dict | None:
    """Find an existing lock held by the same agent on the same region."""
    now = time.time()
    rows = conn.execute(
        f"SELECT * FROM {table} WHERE document_path = ? AND locked_by = ? AND locked_at + lock_ttl > ?",
        (document_path, agent_id, now),
    ).fetchall()
    for row in rows:
        if row["region_start"] == region_start and row["region_end"] == region_end:
            return dict(row)
    return None


def refresh_lock(
    conn: sqlite3.Connection,
    table: str,
    document_path: str,
    agent_id: str,
    ttl: float | None = None,
    not_found_reason: str = "not_locked",
    region_start: int | None = None,
    region_end: int | None = None,
) -> dict[str, bool | str | float]:
    """Extend TTL on a lock. Used by both local and shared lock tables."""
    now = time.time()
    if region_start is not None:
        row = conn.execute(
            f"SELECT * FROM {table} WHERE document_path = ? AND region_start = ? AND region_end = ?",
            (document_path, region_start, region_end),
        ).fetchone()
    else:
        row = conn.execute(
            f"SELECT * FROM {table} WHERE document_path = ? AND region_start IS NULL",
            (document_path,),
        ).fetchone()

    if row is None:
        return {"refreshed": False, "reason": not_found_reason}
    if row["locked_by"] != agent_id:
        return {"refreshed": False, "reason": "not_owner"}

    new_ttl = ttl if ttl is not None else row["lock_ttl"]
    new_expires = now + new_ttl
    conn.execute(
        f"UPDATE {table} SET locked_at = ?, lock_ttl = ? WHERE id = ?",
        (now, new_ttl, row["id"]),
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
