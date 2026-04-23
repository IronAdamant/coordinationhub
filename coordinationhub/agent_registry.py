"""Agent lifecycle: register, heartbeat, deregister, lineage management.

Zero internal dependencies on other coordinationhub modules.
The ``agents`` table is created by ``db.init_schema`` — no per-module init
function is needed here.
"""

from __future__ import annotations

import os
import sqlite3
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

MAX_AGENTS = 10_000  # T3.9: ceiling on active agents per hub

def register_agent(
    connect: ConnectFn,
    agent_id: str,
    worktree_root: str,
    parent_id: str | None = None,
    pid: int | None = None,
    raw_ide_id: str | None = None,
    ide_vendor: str | None = None,
) -> dict[str, Any]:
    """Register a new agent or update heartbeat if already registered.

    ``raw_ide_id`` stores the raw IDE-specific agent ID (e.g. IDE
    hex ID, Kimi CLI session ID, etc.) so that IDE hooks can map it back
    to the hub child ID created during subagent start.

    T1.2 partial fix: if an active agent already exists at ``agent_id``
    with a different PID, reject the registration. This prevents the
    silent-clobber case where two hub processes with colliding agent-id
    generators (in-memory counters, not cross-process safe) would
    overwrite each other's rows. Same-PID re-registrations remain
    idempotent for the common case (agent restart, crash recovery).

    T3.9: if the active-agent count is already at ``MAX_AGENTS``, reject
    the registration with ``reason='max_agents_reached'``. Prevents a
    DoS-via-register-flood from hanging the hub on dashboard rendering
    or memory pressure.
    """
    now = time.time()
    if pid is None:
        pid = os.getpid()
    with connect() as conn:
        # T3.9: ceiling on active registrations
        existing = conn.execute(
            "SELECT pid, status FROM agents WHERE agent_id = ?",
            (agent_id,),
        ).fetchone()
        if existing is None:
            # New registration — check cap before insert
            active_count = conn.execute(
                "SELECT COUNT(*) AS n FROM agents WHERE status = 'active'"
            ).fetchone()["n"]
            if active_count >= MAX_AGENTS:
                return {
                    "registered": False,
                    "agent_id": agent_id,
                    "reason": "max_agents_reached",
                    "max_agents": MAX_AGENTS,
                    "active_count": active_count,
                }
        if (
            existing is not None
            and existing["status"] == "active"
            and existing["pid"] is not None
            and existing["pid"] != pid
        ):
            return {
                "registered": False,
                "agent_id": agent_id,
                "reason": "collision",
                "existing_pid": existing["pid"],
            }
        # T3.12: persist ide_vendor alongside raw_ide_id so the unique
        # index on (raw_ide_id, ide_vendor) can separate Claude Code vs
        # Kimi CLI agents that happen to produce the same raw id.
        conn.execute(
            """
            INSERT INTO agents
            (agent_id, parent_id, worktree_root, pid, started_at, last_heartbeat, status, raw_ide_id, ide_vendor)
            VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                parent_id    = excluded.parent_id,
                worktree_root= excluded.worktree_root,
                pid          = excluded.pid,
                last_heartbeat = excluded.last_heartbeat,
                status       = 'active',
                raw_ide_id   = COALESCE(excluded.raw_ide_id, agents.raw_ide_id),
                ide_vendor   = COALESCE(excluded.ide_vendor, agents.ide_vendor)
            """,
            (agent_id, parent_id, worktree_root, pid, now, now, raw_ide_id, ide_vendor),
        )
        if parent_id is not None:
            _record_descendant_relationship(conn, agent_id, parent_id)
            # T1.20: lineage insert joins the same transaction as the agent
            # insert. Previously the engine wrapper opened a second
            # `with connect()` to write lineage; a crash between the two
            # left an agent registered without a lineage row.
            conn.execute(
                "INSERT OR IGNORE INTO lineage (parent_id, child_id, spawned_at) VALUES (?, ?, ?)",
                (parent_id, agent_id, now),
            )
    return {"registered": True, "agent_id": agent_id}


def find_agent_by_raw_ide_id(
    connect: ConnectFn,
    raw_ide_id: str,
    stale_timeout: float | None = 600.0,
    ide_vendor: str | None = None,
) -> str | None:
    """Look up a hub agent_id by the raw IDE-specific agent ID.

    T3.12: ``ide_vendor`` narrows the search to the calling IDE, so a
    raw_ide_id that happens to be used by two different vendors doesn't
    cross-match. Callers that don't care about vendor (reconciliation,
    debugging) can omit it.

    T1.17: agents with an old heartbeat don't satisfy the lookup so
    hook flows can't re-use a dead hub child id. Pass
    ``stale_timeout=None`` to skip the freshness check entirely.
    """
    import time as _time
    with connect() as conn:
        params: list[Any] = [raw_ide_id]
        where = "raw_ide_id = ? AND status = 'active'"
        if ide_vendor is not None:
            where += " AND ide_vendor = ?"
            params.append(ide_vendor)
        if stale_timeout is not None:
            cutoff = _time.time() - stale_timeout
            where += " AND last_heartbeat >= ?"
            params.append(cutoff)
        row = conn.execute(
            f"SELECT agent_id FROM agents WHERE {where} "
            f"ORDER BY last_heartbeat DESC LIMIT 1",
            tuple(params),
        ).fetchone()
        return row["agent_id"] if row else None


def heartbeat(connect: ConnectFn, agent_id: str) -> dict[str, Any]:
    """Update the last_heartbeat timestamp for a registered agent.

    T1.18: when the UPDATE matches zero rows, reports the reason so the
    caller can decide whether to re-register (agent was reaped or never
    registered). Previously returned ``{"updated": False}`` with no
    context, which made reaped-then-resurrected agents invisible
    forever.
    """
    now = time.time()
    with connect() as conn:
        cursor = conn.execute(
            "UPDATE agents SET last_heartbeat = ?, status = 'active' "
            "WHERE agent_id = ? AND status = 'active'",
            (now, agent_id),
        )
        if cursor.rowcount > 0:
            return {"updated": True}
        # Didn't match an active row — figure out why.
        row = conn.execute(
            "SELECT status FROM agents WHERE agent_id = ?",
            (agent_id,),
        ).fetchone()
        if row is None:
            return {"updated": False, "reason": "not_registered"}
        return {"updated": False, "reason": f"agent_{row['status']}"}


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

    T1.6: if the grandparent is itself stopped/stale, children are
    re-parented to NULL (root) instead of inheriting a dead ancestor.
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT parent_id FROM agents WHERE agent_id = ?",
            (agent_id,),
        ).fetchone()
        parent_id = row["parent_id"] if row else None

        # Walk up the parent chain to find the nearest live ancestor.
        # If grandparent is stopped, skip it; continue until we find one
        # active ancestor or run out of chain (fall back to NULL / root).
        effective_parent: str | None = None
        cursor_id = parent_id
        visited = {agent_id}  # avoid cycles
        while cursor_id is not None and cursor_id not in visited:
            visited.add(cursor_id)
            ancestor_row = conn.execute(
                "SELECT parent_id, status FROM agents WHERE agent_id = ?",
                (cursor_id,),
            ).fetchone()
            if ancestor_row is None:
                break
            if ancestor_row["status"] == "active":
                effective_parent = cursor_id
                break
            cursor_id = ancestor_row["parent_id"]

        conn.execute(
            "UPDATE agents SET status = 'stopped' WHERE agent_id = ?",
            (agent_id,),
        )

        # Children become orphaned: re-parent to the nearest live ancestor
        children = conn.execute(
            "SELECT agent_id, parent_id FROM agents WHERE parent_id = ? AND status = 'active'",
            (agent_id,),
        ).fetchall()
        orphaned = 0
        for child_row in children:
            child_id = child_row["agent_id"]
            conn.execute(
                "UPDATE agents SET parent_id = ? WHERE agent_id = ?",
                (effective_parent, child_id),
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
    include_stale: bool = False,
) -> list[dict[str, Any]]:
    """List registered agents with staleness detection.

    T1.17: when ``active_only`` is True, callers almost always want agents
    that are genuinely alive — status='active' AND last_heartbeat fresh.
    Pre-fix an agent whose process died silently (never called
    ``deregister_agent`` and whose hub missed the reap window) appeared
    forever in the active list. ``include_stale`` defaults to ``False`` so
    rows whose heartbeat is older than ``stale_timeout`` are filtered.
    Dashboards that want the pre-fix "everything that ever registered as
    active" view can pass ``include_stale=True`` explicitly.
    """
    now = time.time()
    cutoff = now - stale_timeout
    # When active_only=True, default to filtering stale rows too. Explicit
    # include_stale=True opts back into pre-fix semantics.
    filter_stale = active_only and not include_stale
    with connect() as conn:
        if active_only and filter_stale:
            rows = conn.execute(
                "SELECT * FROM agents "
                "WHERE status = 'active' AND last_heartbeat >= ? "
                "ORDER BY last_heartbeat DESC",
                (cutoff,),
            ).fetchall()
        elif active_only:
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

    T1.6: all SELECT + UPDATE + DELETE happens inside one BEGIN IMMEDIATE.
    The `last_heartbeat < cutoff` predicate is repeated in the UPDATE WHERE
    clauses so an agent that heartbeats between the initial SELECT and the
    UPDATE is NOT reaped (the UPDATE matches zero rows and we skip its
    children). This closes the TOCTOU where a live agent's locks would
    be wiped by the subsequent reap-stale-locks pass.
    """
    now = time.time()
    cutoff = now - timeout
    conn = connect()
    conn.execute("BEGIN IMMEDIATE")
    try:
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

            # Re-verify the staleness predicate in the UPDATE so an agent
            # that heartbeated between our SELECT and this point is NOT
            # reaped. rowcount tells us whether it actually transitioned.
            cursor = conn.execute(
                "UPDATE agents SET status = 'stopped' "
                "WHERE agent_id = ? AND status = 'active' AND last_heartbeat < ?",
                (agent_id, cutoff),
            )
            if cursor.rowcount == 0:
                # Agent came back to life during the reap window — leave it alone.
                continue
            reaped += 1

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
                conn.execute(
                    "DELETE FROM lineage WHERE parent_id = ? AND child_id = ?",
                    (agent_id, child_id),
                )
                orphaned_children += 1

        conn.execute("COMMIT")
        return {"reaped": reaped, "orphaned_children": orphaned_children}
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.OperationalError as e:
            if "no transaction is active" not in str(e):
                raise
        raise


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
