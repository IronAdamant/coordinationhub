"""Agent registry queries: list, lineage, siblings, stale reaping.

Zero internal dependencies on other coordinationhub modules.
"""

from __future__ import annotations

import time
from typing import Any

from .db import ConnectFn


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
