"""Agent lifecycle: register, heartbeat, deregister, lineage management.

Zero internal dependencies — receives connect() from caller.
"""

from __future__ import annotations

import os
import sqlite3
import time
from typing import Any

from .db import ConnectFn


# ------------------------------------------------------------------ #
# Schema helpers
# ------------------------------------------------------------------ #

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


# ------------------------------------------------------------------ #
# Core operations
# ------------------------------------------------------------------ #

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


def list_agents(
    connect: ConnectFn,
    active_only: bool = True,
    stale_timeout: float = 600.0,
) -> list[dict[str, Any]]:
    """List registered agents with staleness detection."""
    now = time.time()
    with connect() as conn:
        if active_only:
            rows = conn.execute(
                "SELECT * FROM agents WHERE status = 'active' "
                "ORDER BY last_heartbeat DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM agents ORDER BY last_heartbeat DESC"
            ).fetchall()

        agents = []
        for row in rows:
            d = dict(row)
            d["stale"] = (now - d["last_heartbeat"]) > stale_timeout
            agents.append(d)
        return agents


def reap_stale_agents(
    connect: ConnectFn,
    timeout: float = 600.0,
) -> dict[str, Any]:
    """Mark stale agents as stopped and orphan their children."""
    now = time.time()
    cutoff = now - timeout
    with connect() as conn:
        stale = conn.execute(
            "SELECT agent_id, parent_id FROM agents "
            "WHERE status = 'active' AND last_heartbeat < ?",
            (cutoff,),
        ).fetchall()
        stale_ids = [r["agent_id"] for r in stale]

        orphaned_children = 0
        reaped = 0
        for row in stale:
            agent_id = row["agent_id"]
            parent_id = row["parent_id"]
            children = conn.execute(
                "SELECT agent_id FROM agents WHERE parent_id = ? AND status = 'active'",
                (agent_id,),
            ).fetchall()
            for child_row in children:
                child_id = child_row["agent_id"]
                conn.execute(
                    "UPDATE agents SET parent_id = ? WHERE agent_id = ?",
                    (parent_id, child_id),
                )
                orphaned_children += 1

            conn.execute(
                "UPDATE agents SET status = 'stopped' WHERE agent_id = ?",
                (agent_id,),
            )
            reaped += 1

        return {"reaped": reaped, "orphaned_children": orphaned_children}


def get_lineage(
    connect: ConnectFn,
    agent_id: str,
) -> dict[str, Any]:
    """Get ancestors and descendants of an agent."""
    ancestors: list[dict[str, Any]] = []
    descendants: list[dict[str, Any]] = []

    with connect() as conn:
        # Walk up ancestors
        current_id: str | None = agent_id
        while current_id is not None:
            row = conn.execute(
                "SELECT parent_id FROM agents WHERE agent_id = ?",
                (current_id,),
            ).fetchone()
            if row is None:
                break
            parent_id = row["parent_id"]
            if parent_id is None:
                break
            ancestors.append({"agent_id": parent_id, "parent_id": row["parent_id"]})
            current_id = parent_id

        # Walk down descendants via lineage table
        # lineage table maps child_id -> parent_id (not the reverse)
        # We need to build descendant list manually
        def _find_children(parent: str) -> list[tuple[str, str]]:
            return conn.execute(
                "SELECT agent_id, parent_id FROM agents WHERE parent_id = ? AND status = 'active'",
                (parent,),
            ).fetchall()

        stack = [agent_id]
        while stack:
            current = stack.pop()
            children = _find_children(current)
            for child_row in children:
                child_id = child_row["agent_id"]
                descendants.append({"agent_id": child_id, "parent_id": current})
                stack.append(child_id)

    return {"ancestors": ancestors, "descendants": descendants}


def get_siblings(
    connect: ConnectFn,
    agent_id: str,
) -> list[dict[str, Any]]:
    """Get agents that share the same parent as the given agent."""
    with connect() as conn:
        row = conn.execute(
            "SELECT parent_id FROM agents WHERE agent_id = ?",
            (agent_id,),
        ).fetchone()
        if row is None or row["parent_id"] is None:
            return []
        parent_id = row["parent_id"]
        siblings = conn.execute(
            "SELECT agent_id, status, last_heartbeat FROM agents "
            "WHERE parent_id = ? AND status = 'active' AND agent_id != ?",
            (parent_id, agent_id),
        ).fetchall()
        return [dict(r) for r in siblings]
