"""Agent lifecycle operations: register, heartbeat, deregister.

Zero internal dependencies on other coordinationhub modules.
The ``agents`` table is created by ``db.init_schema`` — no per-module init
function is needed here.
"""

from __future__ import annotations

import os
import time
from typing import Any

from .db import ConnectFn


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
    """Mark agent as stopped and orphan its children.

    When children are re-parented to the grandparent (or root if no grandparent),
    the stale ``lineage`` rows that reference the dead agent as parent are
    deleted — the spawning record is preserved in ``agents.parent_id`` and the
    lineage table tracks only the active spawning parent for responsibility
    inheritance (see ``scan._get_spawned_agent_responsibilities``).
    """
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
            # Delete stale lineage rows: the dead agent was the spawning parent
            # but is no longer in the active parent chain.
            conn.execute(
                "DELETE FROM lineage WHERE parent_id = ? AND child_id = ?",
                (agent_id, child_id),
            )
            orphaned += 1

        return {
            "deregistered": True,
            "locks_released": 0,  # Caller should call release_agent_locks separately
            "children_orphaned": orphaned,
        }
