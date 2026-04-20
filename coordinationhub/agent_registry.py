"""Agent lifecycle: register, heartbeat, deregister, lineage management.

Zero internal dependencies on other coordinationhub modules.
The ``agents`` table is created by ``db.init_schema`` — no per-module init
function is needed here.
"""

from __future__ import annotations

import os
import time
from typing import Any

from .db import ConnectFn


# ------------------------------------------------------------------ #
# Descendant registry (event-driven descendant tracking)
# ------------------------------------------------------------------ #

def _record_descendant_relationship(conn, agent_id: str, parent_id: str) -> None:
    """Walk the ancestor chain and record agent_id as a descendant of each ancestor.

    Called from ``register_agent`` so that every ancestor in the chain
    immediately knows about the new descendant — no lazy population.
    Uses ``INSERT OR IGNORE`` so re-registrations are idempotent.
    """
    now = time.time()
    stack = [(parent_id, 1)]  # (ancestor_id, depth)
    visited = set()
    while stack:
        ancestor_id, depth = stack.pop()
        if ancestor_id in visited:
            continue
        visited.add(ancestor_id)
        conn.execute(
            """
            INSERT OR IGNORE INTO descendant_registry
                (ancestor_id, descendant_id, depth, registered_at)
            VALUES (?, ?, ?, ?)
            """,
            (ancestor_id, agent_id, depth, now),
        )
        # Walk up to the next ancestor
        row = conn.execute(
            "SELECT parent_id FROM agents WHERE agent_id = ?",
            (ancestor_id,),
        ).fetchone()
        if row and row["parent_id"]:
            stack.append((row["parent_id"], depth + 1))


def get_descendants_status(
    connect: ConnectFn,
    ancestor_id: str,
) -> list[dict[str, Any]]:
    """Return active descendants of ancestor_id with their status and tasks.

    Includes all depth levels. Returns agents ordered by depth then agent_id.
    Stops agents are included so callers can detect when descendants died.
    """
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT dr.depth, dr.descendant_id, a.status, a.last_heartbeat,
                   ar.current_task
            FROM descendant_registry dr
            JOIN agents a ON a.agent_id = dr.descendant_id
            LEFT JOIN agent_responsibilities ar ON ar.agent_id = dr.descendant_id
            WHERE dr.ancestor_id = ?
            ORDER BY dr.depth ASC, dr.descendant_id ASC
            """,
            (ancestor_id,),
        ).fetchall()
        return [
            {
                "depth": row["depth"],
                "agent_id": row["descendant_id"],
                "status": row["status"],
                "last_heartbeat": row["last_heartbeat"],
                "current_task": row["current_task"],
            }
            for row in rows
        ]


# ------------------------------------------------------------------ #
# Lifecycle operations
# ------------------------------------------------------------------ #

def register_agent(
    connect: ConnectFn,
    agent_id: str,
    worktree_root: str,
    parent_id: str | None = None,
    pid: int | None = None,
    raw_ide_id: str | None = None,
) -> dict[str, Any]:
    """Register a new agent or update heartbeat if already registered.

    ``raw_ide_id`` stores the raw IDE-specific agent ID (e.g. IDE
    hex ID, Kimi CLI session ID, etc.) so that IDE hooks can map it back
    to the hub child ID created during subagent start.
    """
    now = time.time()
    if pid is None:
        pid = os.getpid()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO agents
            (agent_id, parent_id, worktree_root, pid, started_at, last_heartbeat, status, raw_ide_id)
            VALUES (?, ?, ?, ?, ?, ?, 'active', ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                parent_id    = excluded.parent_id,
                worktree_root= excluded.worktree_root,
                pid          = excluded.pid,
                last_heartbeat = excluded.last_heartbeat,
                status       = 'active',
                raw_ide_id   = COALESCE(excluded.raw_ide_id, agents.raw_ide_id)
            """,
            (agent_id, parent_id, worktree_root, pid, now, now, raw_ide_id),
        )
        if parent_id is not None:
            _record_descendant_relationship(conn, agent_id, parent_id)
    return {"registered": True, "agent_id": agent_id}


def find_agent_by_raw_ide_id(
    connect: ConnectFn,
    raw_ide_id: str,
) -> str | None:
    """Look up a hub agent_id by the raw IDE-specific agent ID.

    Returns the agent_id if found and active, otherwise None.
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT agent_id FROM agents "
            "WHERE raw_ide_id = ? AND status = 'active' "
            "ORDER BY last_heartbeat DESC LIMIT 1",
            (raw_ide_id,),
        ).fetchone()
        return row["agent_id"] if row else None


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


# ------------------------------------------------------------------ #
# Queries
# ------------------------------------------------------------------ #

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
    """Mark stale agents as stopped and orphan their children.

    Children are re-parented to the stale agent's parent (or root if the stale
    agent had no parent).  Stale ``lineage`` rows are deleted so that the
    responsibility-inheritance scan query always joins on the live spawning
    parent, never on a dead agent.
    """
    now = time.time()
    cutoff = now - timeout
    with connect() as conn:
        stale = conn.execute(
            "SELECT agent_id, parent_id FROM agents "
            "WHERE status = 'active' AND last_heartbeat < ?",
            (cutoff,),
        ).fetchall()

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
                # Delete stale lineage rows so scan sees the live parent.
                conn.execute(
                    "DELETE FROM lineage WHERE parent_id = ? AND child_id = ?",
                    (agent_id, child_id),
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

        # Walk down descendants via agents parent_id
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
