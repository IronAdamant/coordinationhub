"""Task failure tracking and dead letter queue for CoordinationHub.

When a task fails, its failure is recorded here. After max_retries attempts,
the task is moved to dead_letter status. Dead letter tasks can be retried
by resetting them to pending.

Zero internal dependencies — receives connect() from caller.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from .db import ConnectFn


def record_task_failure(
    connect: ConnectFn,
    task_id: str,
    error: str | None = None,
    max_retries: int = 3,
) -> dict[str, Any]:
    """Record a task failure, incrementing attempt count and moving to dead_letter if max_retries exceeded.

    Returns {"recorded": True, "status": "failed"|"dead_letter", "attempt": N}.

    T1.7: the SELECT + UPDATE are now wrapped in one BEGIN IMMEDIATE so
    two concurrent failure recordings can't both read the same attempt
    number and race their UPDATEs (the loser's UPDATE used to match zero
    rows and silently lose the failure). Also: when a prior row exists
    its stored max_retries is authoritative — the call-site default is
    only used on the first insert.
    """
    now = time.time()
    with connect() as conn:
        # T1.7: BEGIN IMMEDIATE + explicit COMMIT make the SELECT +
        # UPDATE/INSERT atomic. The surrounding `with connect()` still
        # manages connection lifecycle (test_db_safety uses a custom
        # context-manager shape), but the transaction boundaries are
        # ours.
        try:
            conn.execute("BEGIN IMMEDIATE")
            began = True
        except sqlite3.OperationalError:
            # Already in a transaction (test shim opens one on enter).
            began = False

        # T1.8: ignore 'retried' rows when computing next attempt, so a
        # retry-from-DLQ resets the retry budget as intended.
        existing = conn.execute(
            "SELECT * FROM task_failures WHERE task_id = ? AND status != 'retried' "
            "ORDER BY attempt DESC LIMIT 1",
            (task_id,),
        ).fetchone()

        if existing:
            new_attempt = existing["attempt"] + 1
            # T1.7: prefer the stored max_retries; fall back to the arg
            # only if the column is NULL (should never be — schema has a default).
            effective_max = existing["max_retries"] if existing["max_retries"] is not None else max_retries
            is_dead_letter = new_attempt >= effective_max
            dead_letter_at = now if is_dead_letter else None
            status = "dead_letter" if is_dead_letter else "failed"
            conn.execute(
                """UPDATE task_failures
                   SET attempt=?, error=?, last_attempt_at=?, dead_letter_at=?, status=?
                   WHERE task_id=? AND attempt=? AND status != 'retried'""",
                (new_attempt, error, now, dead_letter_at, status,
                 task_id, existing["attempt"]),
            )
            result = {"recorded": True, "status": status, "attempt": new_attempt}
        else:
            # First failure
            is_dead_letter = 1 >= max_retries
            status = "dead_letter" if is_dead_letter else "failed"
            dead_letter_at = now if is_dead_letter else None
            conn.execute(
                """INSERT INTO task_failures
                   (task_id, error, attempt, max_retries, first_attempt_at, last_attempt_at, dead_letter_at, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (task_id, error, 1, max_retries, now, now, dead_letter_at, status),
            )
            result = {"recorded": True, "status": status, "attempt": 1}

        if began:
            conn.execute("COMMIT")
        return result


def get_dead_letter_tasks(
    connect: ConnectFn,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return tasks currently in dead_letter status."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM task_failures WHERE status = 'dead_letter' ORDER BY dead_letter_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


def retry_from_dead_letter(
    connect: ConnectFn,
    task_id: str,
) -> dict[str, Any]:
    """Retry a task from the dead letter queue.

    Resets task status to 'pending' and task_failures entry to 'retried'.
    Returns {"retried": True} if successful, {"retried": False, "reason": ...} if not in DLQ.

    T1.8: after marking the prior attempts as 'retried', subsequent calls
    to `record_task_failure` will skip the retried row (via the
    `status != 'retried'` filter added in T1.7) and start a fresh row
    at attempt=1. Before the fix, the 'retried' row was still matched
    by `ORDER BY attempt DESC LIMIT 1` in record_task_failure, so the
    next failure incremented the stale attempt and could immediately
    re-enter dead_letter. The retry budget was effectively zero.
    """
    now = time.time()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM task_failures WHERE task_id = ? AND status = 'dead_letter'",
            (task_id,),
        ).fetchone()
        if not row:
            return {"retried": False, "reason": "not_in_dead_letter"}

        # Update task_failures status
        conn.execute(
            "UPDATE task_failures SET status = 'retried', dead_letter_at = NULL WHERE task_id = ?",
            (task_id,),
        )
        # Reset task status to pending
        conn.execute(
            "UPDATE tasks SET status = 'pending', updated_at = ? WHERE id = ?",
            (now, task_id),
        )
        return {"retried": True, "task_id": task_id}


def get_task_failure_history(
    connect: ConnectFn,
    task_id: str,
) -> list[dict[str, Any]]:
    """Return all failure records for a task."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM task_failures WHERE task_id = ? ORDER BY attempt ASC",
            (task_id,),
        ).fetchall()
        return [dict(row) for row in rows]
