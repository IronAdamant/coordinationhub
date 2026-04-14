"""Pending sub-agent task storage for CoordinationHub.

Claude Code fires ``PreToolUse`` for ``tool_name == "Agent"`` with the
full task description, then fires ``SubagentStart`` for the spawned
sub-agent with no description at all. These two events need to be
correlated so the sub-agent's ``current_task`` column reflects what
was actually requested.

This module provides a tiny FIFO queue keyed by
``(session_id, subagent_type)``: ``PreToolUse[Agent]`` stashes a row
here, ``SubagentStart`` pops the oldest unconsumed row for its session
and subagent type, and applies the description to the child agent.

Zero internal dependencies — receives ``connect: ConnectFn`` from the
caller, same pattern as ``notifications.py`` and ``conflict_log.py``.
"""

from __future__ import annotations

import time
from typing import Any

from .db import ConnectFn


# Rows older than this are assumed orphaned (e.g. the Agent tool call
# errored before SubagentStart could fire) and get deleted before each
# stash to keep the table small.
_STALE_TTL_SECONDS = 600.0  # 10 minutes


def stash_pending_task(
    connect: ConnectFn,
    tool_use_id: str,
    session_id: str,
    subagent_type: str,
    description: str | None,
    prompt: str | None = None,
) -> dict[str, Any]:
    """Record a pending task from a ``PreToolUse[Agent]`` event.

    Called once per Agent tool invocation. The matching
    ``SubagentStart`` that fires shortly after will consume this row
    via :func:`consume_pending_task` and apply ``description`` as the
    child agent's ``current_task``.

    Also prunes rows older than ``_STALE_TTL_SECONDS`` so the table
    cannot grow without bound if Agent tool calls fail before
    SubagentStart.
    """
    now = time.time()
    cutoff = now - _STALE_TTL_SECONDS
    with connect() as conn:
        conn.execute(
            "DELETE FROM pending_tasks WHERE status = 'pending' AND created_at < ?",
            (cutoff,),
        )
        conn.execute(
            """
            INSERT INTO pending_tasks
            (task_id, scope_id, subagent_type, description, prompt, created_at, status, source)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', 'external')
            ON CONFLICT(task_id) DO UPDATE SET
                description = excluded.description,
                prompt      = excluded.prompt,
                created_at  = excluded.created_at,
                consumed_at = NULL,
                status      = 'pending'
            """,
            (tool_use_id, session_id, subagent_type, description, prompt, now),
        )
    return {"stashed": True, "tool_use_id": tool_use_id}


def consume_pending_task(
    connect: ConnectFn,
    session_id: str,
    subagent_type: str,
) -> dict[str, Any] | None:
    """Pop the oldest unconsumed pending task for this session + type.

    Returns the row dict (with ``description`` and ``prompt``) and
    marks it consumed, or ``None`` if no pending task exists.

    FIFO within ``(session_id, subagent_type)`` — if the user spawns
    two Explore agents in a row, the first ``SubagentStart`` pairs with
    the first ``PreToolUse[Agent]`` and the second with the second.
    """
    now = time.time()
    with connect() as conn:
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
            (session_id, subagent_type),
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE pending_tasks SET consumed_at = ?, status = 'consumed' WHERE task_id = ?",
            (now, row["task_id"]),
        )
        return dict(row)


def prune_consumed_pending_tasks(
    connect: ConnectFn,
    max_age_seconds: float = _STALE_TTL_SECONDS,
) -> dict[str, Any]:
    """Delete consumed pending tasks older than *max_age_seconds*.

    Kept as a separate function for explicit housekeeping calls. The
    stash path already prunes *unconsumed* stale rows on every insert.
    """
    cutoff = time.time() - max_age_seconds
    with connect() as conn:
        cursor = conn.execute(
            "DELETE FROM pending_tasks "
            "WHERE status = 'consumed' AND consumed_at < ?",
            (cutoff,),
        )
        return {"pruned": cursor.rowcount}
