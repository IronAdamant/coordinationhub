"""Zero-deps spawner primitives for HA coordinator sub-agent registry.

Tracks a parent agent's intent to spawn a sub-agent before Claude Code
fires ``SubagentStart``. When the hook fires, it calls ``consume_pending_spawn``
to mark the pending spawn as ``registered``.

Receives connect: ConnectFn from the caller — no internal pool dependency,
same pattern as ``notifications.py`` and ``conflict_log.py``.
"""

from __future__ import annotations

import time
from typing import Any, NamedTuple

from .db import ConnectFn


# Rows older than this with status='pending' are marked expired.
_SPAWN_TTL_SECONDS = 600.0  # 10 minutes


class PendingSpawn(NamedTuple):
    """A pending sub-agent spawn record."""
    id: str
    parent_agent_id: str
    subagent_type: str | None
    description: str | None
    prompt: str | None
    created_at: float
    consumed_at: float | None
    status: str  # pending | registered | expired


def generate_spawn_id(
    conn: ConnectFn,
    parent_agent_id: str,
    subagent_type: str,
) -> str:
    """Generate a unique spawn ID: ``{parent_agent_id}.{subagent_type}.{seq}``."""
    with conn() as cx:
        cursor = cx.execute(
            """
            SELECT COUNT(*) FROM pending_spawner_tasks
            WHERE parent_agent_id = ? AND subagent_type = ?
            """,
            (parent_agent_id, subagent_type),
        )
        seq = cursor.fetchone()[0]
    return f"{parent_agent_id}.{subagent_type}.{seq}"


def stash_pending_spawn(
    connect: ConnectFn,
    spawn_id: str,
    parent_agent_id: str,
    subagent_type: str | None = None,
    description: str | None = None,
    prompt: str | None = None,
) -> dict[str, Any]:
    """Record a pending sub-agent spawn from a parent agent.

    Called by a parent agent that intends to spawn a sub-agent. When
    Claude Code fires ``SubagentStart``, the hook correlates it with
    this pending record via ``consume_pending_spawn``.

    Also prunes rows older than ``_SPAWN_TTL_SECONDS`` so the table
    cannot grow without bound if spawn requests fail before SubagentStart.
    """
    now = time.time()
    cutoff = now - _SPAWN_TTL_SECONDS
    with connect() as conn:
        # Expire stale pending rows
        conn.execute(
            "UPDATE pending_spawner_tasks SET status = 'expired' "
            "WHERE status = 'pending' AND created_at < ?",
            (cutoff,),
        )
        conn.execute(
            """
            INSERT INTO pending_spawner_tasks
            (id, parent_agent_id, subagent_type, description, prompt, created_at, status)
            VALUES (?, ?, ?, ?, ?, ?, 'pending')
            """,
            (spawn_id, parent_agent_id, subagent_type, description, prompt, now),
        )
    return {"stashed": True, "spawn_id": spawn_id, "parent_agent_id": parent_agent_id}


def consume_pending_spawn(
    connect: ConnectFn,
    parent_agent_id: str,
    subagent_type: str | None = None,
) -> dict[str, Any] | None:
    """Mark the oldest pending spawn for this parent + type as registered.

    Called by the Claude Code hook when ``SubagentStart`` fires. The hook
    has already registered the sub-agent at this point. This call marks
    the pending spawn as ``registered`` so the parent knows the sub-agent
    is alive.

    Returns the row dict (with ``description`` and ``prompt``) or ``None``
    if no pending spawn exists for this parent + type.
    """
    now = time.time()
    with connect() as conn:
        if subagent_type:
            row = conn.execute(
                """
                SELECT id, description, prompt, created_at
                FROM pending_spawner_tasks
                WHERE parent_agent_id = ?
                  AND subagent_type = ?
                  AND status = 'pending'
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (parent_agent_id, subagent_type),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT id, description, prompt, created_at
                FROM pending_spawner_tasks
                WHERE parent_agent_id = ?
                  AND status = 'pending'
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (parent_agent_id,),
            ).fetchone()

        if row is None:
            return None

        conn.execute(
            "UPDATE pending_spawner_tasks SET consumed_at = ?, status = 'registered' "
            "WHERE id = ?",
            (now, row["id"]),
        )
        return dict(row)


def get_pending_spawns(
    connect: ConnectFn,
    parent_agent_id: str,
    include_consumed: bool = False,
) -> list[dict[str, Any]]:
    """Return all pending (or all) spawn records for this parent agent."""
    with connect() as conn:
        if include_consumed:
            rows = conn.execute(
                """
                SELECT id, parent_agent_id, subagent_type, description, prompt,
                       created_at, consumed_at, status
                FROM pending_spawner_tasks
                WHERE parent_agent_id = ?
                ORDER BY created_at ASC
                """,
                (parent_agent_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, parent_agent_id, subagent_type, description, prompt,
                       created_at, consumed_at, status
                FROM pending_spawner_tasks
                WHERE parent_agent_id = ? AND status = 'pending'
                ORDER BY created_at ASC
                """,
                (parent_agent_id,),
            ).fetchall()
        return [dict(row) for row in rows]


def prune_stale_spawns(
    connect: ConnectFn,
    max_age_seconds: float = _SPAWN_TTL_SECONDS,
) -> dict[str, Any]:
    """Delete consumed or expired spawn records older than *max_age_seconds*."""
    cutoff = time.time() - max_age_seconds
    with connect() as conn:
        cursor = conn.execute(
            """
            DELETE FROM pending_spawner_tasks
            WHERE status IN ('registered', 'expired') AND consumed_at < ?
            """,
            (cutoff,),
        )
        return {"pruned": cursor.rowcount}


def cancel_spawn(
    connect: ConnectFn,
    spawn_id: str,
) -> dict[str, Any]:
    """Cancel a pending spawn (mark as 'expired' if still pending).

    Returns ``cancelled`` if the spawn was pending and is now expired.
    Returns ``not_found`` if the spawn does not exist or is already consumed.
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT status FROM pending_spawner_tasks WHERE id = ?",
            (spawn_id,),
        ).fetchone()
        if row is None:
            return {"not_found": True, "spawn_id": spawn_id}
        if row["status"] != "pending":
            return {"not_found": True, "spawn_id": spawn_id, "status": row["status"]}
        now = time.time()
        conn.execute(
            "UPDATE pending_spawner_tasks SET consumed_at = ?, status = 'expired' WHERE id = ?",
            (now, spawn_id),
        )
        return {"cancelled": True, "spawn_id": spawn_id}


def request_deregistration(
    connect: ConnectFn,
    child_agent_id: str,
    requested_by: str,
) -> dict[str, Any]:
    """Request graceful deregistration of a child agent.

    Sets ``stop_requested_at`` on the child agent. The child is expected
    to poll ``is_stop_requested`` and call ``deregister_agent`` if it sees
    the flag set. After *timeout* seconds, the spawner should escalate to
    ``deregister_agent`` directly.

    Returns ``requested`` if the stop flag was set.
    Returns ``not_found`` if the child agent does not exist or is not active.
    """
    now = time.time()
    with connect() as conn:
        row = conn.execute(
            "SELECT agent_id, status FROM agents WHERE agent_id = ?",
            (child_agent_id,),
        ).fetchone()
        if row is None:
            return {"not_found": True, "child_agent_id": child_agent_id}
        if row["status"] != "active":
            return {"not_found": True, "child_agent_id": child_agent_id, "status": row["status"]}
        conn.execute(
            "UPDATE agents SET stop_requested_at = ? WHERE agent_id = ?",
            (now, child_agent_id),
        )
        return {"requested": True, "child_agent_id": child_agent_id, "requested_by": requested_by}


def is_stop_requested(
    connect: ConnectFn,
    agent_id: str,
) -> dict[str, Any]:
    """Check if a stop has been requested for this agent.

    Returns ``stop_requested: True`` and ``stop_requested_at`` timestamp if a
    parent has requested graceful shutdown. The agent should call
    ``deregister_agent`` in response.
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT stop_requested_at FROM agents WHERE agent_id = ?",
            (agent_id,),
        ).fetchone()
        if row is None:
            return {"agent_id": agent_id, "stop_requested": False}
        return {
            "agent_id": agent_id,
            "stop_requested": row["stop_requested_at"] is not None,
            "stop_requested_at": row["stop_requested_at"],
        }


def await_agent_stopped(
    connect: ConnectFn,
    child_agent_id: str,
    timeout: float = 30.0,
    poll_interval: float = 0.5,
) -> dict[str, Any]:
    """Poll until a child agent is stopped or the timeout is reached.

    Returns ``stopped: True`` if the child called ``deregister_agent`` within
    the timeout. Returns ``timed_out: True`` if the child did not stop in time —
    the caller should then call ``deregister_agent`` directly to force cleanup.

    The child agent is responsible for polling ``is_stop_requested`` and
    calling ``deregister_agent`` when it sees the stop flag.
    """
    deadline = time.time() + timeout

    while time.time() < deadline:
        with connect() as conn:
            row = conn.execute(
                "SELECT status FROM agents WHERE agent_id = ?",
                (child_agent_id,),
            ).fetchone()
            if row is None or row["status"] == "stopped":
                return {"stopped": True, "child_agent_id": child_agent_id}
        time.sleep(poll_interval)

    return {
        "timed_out": True,
        "child_agent_id": child_agent_id,
        "timeout": timeout,
        "escalate": True,  # Caller should call deregister_agent as hard fallback
    }
