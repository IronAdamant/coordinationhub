"""Agent lifecycle operations: register, heartbeat, deregister.

Zero internal dependencies on other coordinationhub modules.
"""

from __future__ import annotations

import os
import time
from typing import Any

from .db import ConnectFn


def init_agents_table(connect: ConnectFn) -> None:
    """Create the agents table if it does not exist."""
    with connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agents (
                agent_id      TEXT PRIMARY KEY,
                parent_id     TEXT,
                worktree_root TEXT NOT NULL,
                pid           INTEGER,
                started_at    REAL NOT NULL,
                last_heartbeat REAL NOT NULL,
                status        TEXT DEFAULT 'active'
            )
        """)


def register_agent(
    connect: ConnectFn,
    agent_id: str,
    worktree_root: str,
    parent_id: str | None = None,
    pid: int | None = None,
) -> dict[str, Any]:
    """Register a new agent or update heartbeat if already registered."""
    now = time.time()
    if pid is None:
        pid = os.getpid()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO agents
            (agent_id, parent_id, worktree_root, pid, started_at, last_heartbeat, status)
            VALUES (?, ?, ?, ?, ?, ?, 'active')
            ON CONFLICT(agent_id) DO UPDATE SET
                parent_id       = excluded.parent_id,
                worktree_root   = excluded.worktree_root,
                pid             = excluded.pid,
                last_heartbeat  = excluded.last_heartbeat,
                status         = 'active'
            """,
            (agent_id, parent_id, worktree_root, pid, now, now),
        )
    return {"registered": True, "agent_id": agent_id}


def heartbeat(connect: ConnectFn, agent_id: str) -> dict[str, Any]:
    """Update the last_heartbeat timestamp for a registered agent."""
    now = time.time()
    with connect() as conn:
        cursor = conn.execute(
            "UPDATE agents SET last_heartbeat = ?, status = 'active' "
            "WHERE agent_id = ? AND status = 'active'",
            (now, agent_id),
        )
        return {"updated": cursor.rowcount > 0}


def deregister_agent(
    connect: ConnectFn,
    agent_id: str,
) -> dict[str, Any]:
    """Mark agent as stopped and orphan its children."""
    with connect() as conn:
        row = conn.execute(
            "SELECT parent_id FROM agents WHERE agent_id = ?",
            (agent_id,),
        ).fetchone()
        parent_id = row["parent_id"] if row else None

        conn.execute(
            "UPDATE agents SET status = 'stopped' WHERE agent_id = ?",
            (agent_id,),
        )

        # Children become orphaned: re-parent to grandparent
        children = conn.execute(
            "SELECT agent_id, parent_id FROM agents WHERE parent_id = ? AND status = 'active'",
            (agent_id,),
        ).fetchall()
        orphaned = 0
        for child_row in children:
            child_id = child_row["agent_id"]
            conn.execute(
                "UPDATE agents SET parent_id = ? WHERE agent_id = ?",
                (parent_id, child_id),
            )
            orphaned += 1

        return {
            "deregistered": True,
            "locks_released": 0,  # Caller should call release_agent_locks separately
            "children_orphaned": orphaned,
        }
