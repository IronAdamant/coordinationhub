"""Conflict recording and querying for CoordinationHub.

Zero internal dependencies — receives connect() from caller.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .db import ConnectFn
from .lock_ops import record_conflict as _record_conflict, query_conflicts as _query_conflicts


def record_conflict(
    connect: ConnectFn,
    document_path: str,
    agent_a: str,
    agent_b: str,
    conflict_type: str,
    resolution: str = "rejected",
    details: dict[str, Any] | None = None,
) -> int | None:
    """Record a lock conflict to the shared conflict log."""
    with connect() as conn:
        return _record_conflict(
            conn,
            "lock_conflicts",
            document_path,
            agent_a,
            agent_b,
            conflict_type,
            resolution=resolution,
            details=details,
        )


def query_conflicts(
    connect: ConnectFn,
    document_path: str | None = None,
    agent_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Query the conflict log."""
    with connect() as conn:
        return _query_conflicts(
            conn,
            "lock_conflicts",
            document_path,
            agent_id,
            limit,
        )
