"""Zero-deps spawner primitives for HA coordinator sub-agent registry.

Tracks a parent agent's intent to spawn a sub-agent before the external
IDE spawns it. When the spawn is reported back, ``consume_pending_spawn``
marks the pending record as ``registered``.

Receives connect: ConnectFn from the caller — no internal pool dependency,
same pattern as ``notifications.py`` and ``conflict_log.py``.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any, NamedTuple

from .db import ConnectFn
from .limits import MAX_DESCRIPTION, MAX_PROMPT, truncate


# Rows older than this with status='pending' are marked expired.
# T6.15: overridable via the ``COORDINATIONHUB_SPAWN_TTL_SECONDS``
# environment variable so deployments running slow CLIs (Kimi, remote
# evaluator, etc.) that can exceed the default 10-minute window don't
# have to patch source code.
import os as _os
import re as _re
try:
    _SPAWN_TTL_SECONDS = float(
        _os.environ.get("COORDINATIONHUB_SPAWN_TTL_SECONDS", "600.0")
    )
except (TypeError, ValueError):
    _SPAWN_TTL_SECONDS = 600.0

# T7.3: ``subagent_type`` is baked into the spawn id via
# ``{parent}.{subagent_type}.{seq}``. A dot in ``subagent_type`` would
# make the id ambiguous — downstream consumers that split on dots could
# pick the wrong seq, and a legitimate ``foo.bar`` agent would look like
# a child of ``foo``. Restrict to a safe character class at the
# boundary.
_SAFE_SUBAGENT_TYPE = _re.compile(r"^[A-Za-z0-9_-]+$")

# T6.9: cap on the number of simultaneously-pending spawn rows. Pre-fix
# a caller (or a chain of failed IDE integrations) could stash unbounded
# pending_tasks rows, each carrying a prompt string that's never read.
# Memory-exhaustion DoS is trivial without a ceiling. Override via the
# ``COORDINATIONHUB_MAX_PENDING_SPAWNS`` environment variable.
try:
    MAX_PENDING_SPAWNS = int(
        _os.environ.get("COORDINATIONHUB_MAX_PENDING_SPAWNS", "1000")
    )
except (TypeError, ValueError):
    MAX_PENDING_SPAWNS = 1000


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
    """Generate a unique spawn ID: ``{parent_agent_id}.{subagent_type}.{seq}``.

    DEPRECATED (T1.9): this function is NOT atomic with the subsequent
    INSERT. Two concurrent callers can observe the same seq and produce
    identical spawn IDs, causing PK collisions in ``pending_tasks``. Use
    :func:`stash_pending_spawn` without passing ``spawn_id`` — it generates
    the seq inside the same ``BEGIN IMMEDIATE`` that inserts the row, so
    the race is closed.

    Retained only for back-compat; callers should migrate.
    """
    prefix = f"{parent_agent_id}.{subagent_type}."
    with conn() as cx:
        cursor = cx.execute(
            """
            SELECT MAX(CAST(substr(task_id, ?) AS INTEGER)) AS max_seq
            FROM pending_tasks
            WHERE scope_id = ?
              AND subagent_type = ?
              AND substr(task_id, ?) GLOB '[0-9]*'
            """,
            (len(prefix) + 1, parent_agent_id, subagent_type, len(prefix) + 1),
        )
        row = cursor.fetchone()
        max_seq = row[0] if row and row[0] is not None else -1
    return f"{prefix}{max_seq + 1}"


def _next_spawn_seq(conn: Any, parent_agent_id: str, subagent_type: str) -> int:
    """Return the next integer seq for (parent_agent_id, subagent_type).

    Must be called inside a BEGIN IMMEDIATE transaction so the MAX read
    and the subsequent INSERT cannot be interleaved with another writer.
    """
    prefix = f"{parent_agent_id}.{subagent_type}."
    row = conn.execute(
        """
        SELECT MAX(CAST(substr(task_id, ?) AS INTEGER)) AS max_seq
        FROM pending_tasks
        WHERE scope_id = ?
          AND subagent_type = ?
          AND substr(task_id, ?) GLOB '[0-9]*'
        """,
        (len(prefix) + 1, parent_agent_id, subagent_type, len(prefix) + 1),
    ).fetchone()
    max_seq = row[0] if row and row[0] is not None else -1
    return int(max_seq) + 1


def stash_pending_spawn(
    connect: ConnectFn,
    spawn_id: str | None = None,
    parent_agent_id: str | None = None,
    subagent_type: str | None = None,
    description: str | None = None,
    prompt: str | None = None,
    source: str = "external",
) -> dict[str, Any]:
    """Record a pending sub-agent spawn from a parent agent.

    Called by a parent agent that intends to spawn a sub-agent. When
    the external IDE (Kimi CLI, Cursor, etc.) spawns the agent,
    it correlates the spawn with this pending record via
    ``report_subagent_spawned``.

    T1.9: if ``spawn_id`` is ``None`` (preferred), the spawn id is generated
    atomically inside the same ``BEGIN IMMEDIATE`` that inserts the row by
    taking ``MAX(seq) + 1`` over existing ``(parent, subagent_type, *)``
    rows. This closes the race where two concurrent callers observed the
    same ``COUNT(*)`` and produced colliding spawn ids.

    If ``spawn_id`` is supplied the caller is responsible for its uniqueness
    (back-compat path only).

    Also prunes rows older than ``_SPAWN_TTL_SECONDS`` so the table
    cannot grow without bound if spawn requests fail before the agent
    is actually spawned.
    """
    if parent_agent_id is None:
        raise TypeError("parent_agent_id is required")
    # T7.3: reject dotted or shell-unsafe subagent_types so the
    # derived spawn id stays unambiguous.
    if subagent_type is not None and not _SAFE_SUBAGENT_TYPE.match(subagent_type):
        return {
            "stashed": False,
            "reason": "invalid_subagent_type",
            "subagent_type": subagent_type,
        }

    # T6.14: bound free-text fields so a runaway IDE integration can't
    # stash multi-MB prompts that then propagate through the dashboard.
    description = truncate(description, MAX_DESCRIPTION)
    prompt = truncate(prompt, MAX_PROMPT)
    now = time.time()
    cutoff = now - _SPAWN_TTL_SECONDS
    with connect() as conn:
        # T1.9: the MAX-seq read and the INSERT must be atomic. Opening
        # BEGIN IMMEDIATE here serializes concurrent callers. Uses the
        # dual-shape pattern — if the outer `with connect()` already
        # began a tx (test_db_safety's custom shim), skip our own
        # BEGIN/COMMIT and let the wrapper handle it.
        try:
            conn.execute("BEGIN IMMEDIATE")
            began = True
        except sqlite3.OperationalError:
            began = False

        # Expire stale pending rows inside the same tx so the seq
        # computation sees a stable snapshot.
        conn.execute(
            "UPDATE pending_tasks SET status = 'expired' "
            "WHERE status = 'pending' AND created_at < ?",
            (cutoff,),
        )
        # T6.9: enforce the pending-spawn ceiling post-expiry so stale
        # rows don't count against live ones.
        pending_count_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM pending_tasks WHERE status = 'pending'"
        ).fetchone()
        pending_count = pending_count_row["cnt"] if pending_count_row else 0
        if pending_count >= MAX_PENDING_SPAWNS:
            if began:
                conn.execute("COMMIT")
            return {
                "stashed": False,
                "reason": "max_pending_spawns_reached",
                "pending_count": pending_count,
                "max_pending_spawns": MAX_PENDING_SPAWNS,
            }
        if spawn_id is None:
            if subagent_type is None:
                raise TypeError(
                    "subagent_type is required when spawn_id is not supplied"
                )
            seq = _next_spawn_seq(conn, parent_agent_id, subagent_type)
            spawn_id = f"{parent_agent_id}.{subagent_type}.{seq}"
        conn.execute(
            """
            INSERT INTO pending_tasks
            (task_id, scope_id, subagent_type, description, prompt, created_at, status, source)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (spawn_id, parent_agent_id, subagent_type, description, prompt, now, source),
        )
        if began:
            conn.execute("COMMIT")
    return {"stashed": True, "spawn_id": spawn_id, "parent_agent_id": parent_agent_id}


def consume_pending_spawn(
    connect: ConnectFn,
    parent_agent_id: str,
    subagent_type: str | None = None,
) -> dict[str, Any] | None:
    """Mark the oldest pending spawn for this parent + type as registered.

    Called after the external IDE reports a sub-agent spawn. The agent
    has already been registered at this point. This call marks
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
                SELECT task_id, description, prompt, created_at
                FROM pending_tasks
                WHERE scope_id = ?
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
                SELECT task_id, description, prompt, created_at
                FROM pending_tasks
                WHERE scope_id = ?
                  AND status = 'pending'
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (parent_agent_id,),
            ).fetchone()

        if row is None:
            return None

        conn.execute(
            "UPDATE pending_tasks SET consumed_at = ?, status = 'registered' "
            "WHERE task_id = ?",
            (now, row["task_id"]),
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
                SELECT task_id, scope_id, subagent_type, description, prompt,
                       created_at, consumed_at, status, source
                FROM pending_tasks
                WHERE scope_id = ?
                ORDER BY created_at ASC
                """,
                (parent_agent_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT task_id, scope_id, subagent_type, description, prompt,
                       created_at, consumed_at, status, source
                FROM pending_tasks
                WHERE scope_id = ? AND status = 'pending'
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
            DELETE FROM pending_tasks
            WHERE status IN ('registered', 'expired') AND consumed_at < ?
            """,
            (cutoff,),
        )
        return {"pruned": cursor.rowcount}


def report_subagent_spawned(
    connect: ConnectFn,
    parent_agent_id: str,
    subagent_type: str | None,
    child_agent_id: str,
    source: str = "external",
) -> dict[str, Any]:
    """Report that a sub-agent has been spawned by an external system.

    Consumes the oldest pending spawn for this parent + type and links
    it to the actual child agent ID. Any IDE/CLI (Kimi,
    Cursor, etc.) can call this after spawning a sub-agent via its
    native mechanism.
    """
    now = time.time()
    with connect() as conn:
        if subagent_type:
            row = conn.execute(
                """
                SELECT task_id, description, prompt, created_at
                FROM pending_tasks
                WHERE scope_id = ? AND subagent_type = ? AND status = 'pending'
                ORDER BY created_at ASC LIMIT 1
                """,
                (parent_agent_id, subagent_type),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT task_id, description, prompt, created_at
                FROM pending_tasks
                WHERE scope_id = ? AND status = 'pending'
                ORDER BY created_at ASC LIMIT 1
                """,
                (parent_agent_id,),
            ).fetchone()

        spawn_id = row["task_id"] if row else None
        description = row["description"] if row else None
        prompt = row["prompt"] if row else None

        if spawn_id:
            conn.execute(
                "UPDATE pending_tasks SET consumed_at = ?, status = 'registered', source = ? WHERE task_id = ?",
                (now, source, spawn_id),
            )

        return {
            "reported": True,
            "parent_agent_id": parent_agent_id,
            "child_agent_id": child_agent_id,
            "spawn_id": spawn_id,
            "description": description,
            "prompt": prompt,
        }


def cancel_spawn(
    connect: ConnectFn,
    spawn_id: str,
    caller_agent_id: str | None = None,
) -> dict[str, Any]:
    """Cancel a pending spawn (mark as 'expired' if still pending).

    Returns ``cancelled`` if the spawn was pending and is now expired.
    Returns ``not_found`` if the spawn does not exist or is already consumed.

    T2.4: ``caller_agent_id`` (optional) — when supplied, must equal the
    pending spawn's ``scope_id`` (the parent agent that stashed the
    spawn). Without this check any agent could cancel any pending spawn
    — including siblings' — which would silently abort sub-agent
    creation on unrelated trees. Omitted = pre-T2.4 permissive
    behaviour, preserved for internal callers (scheduler reaps, CLI
    admin).
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT status, scope_id FROM pending_tasks WHERE task_id = ?",
            (spawn_id,),
        ).fetchone()
        if row is None:
            return {"not_found": True, "spawn_id": spawn_id}
        if row["status"] != "pending":
            return {"not_found": True, "spawn_id": spawn_id, "status": row["status"]}
        if caller_agent_id is not None and caller_agent_id != row["scope_id"]:
            return {
                "cancelled": False,
                "spawn_id": spawn_id,
                "error": "caller_agent_id does not match spawn's parent (scope_id)",
                "reason": "caller_mismatch",
            }
        now = time.time()
        conn.execute(
            "UPDATE pending_tasks SET consumed_at = ?, status = 'expired' WHERE task_id = ?",
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


# T6.5: the polling ``await_agent_stopped`` primitive was superseded by
# :meth:`spawner_subsystem.Spawner.await_subagent_stopped`, which waits
# on the in-memory event bus (and falls back to the SQLite journal for
# cross-process sync via _hybrid_wait). The polling version had no
# callers. Deleted.
