"""Task failure tracking and dead letter queue for CoordinationHub.

When a task fails, its failure is recorded here. After max_retries attempts,
the task is moved to dead_letter status. Dead letter tasks can be retried
by resetting them to pending.

Zero internal dependencies — receives connect() from caller.
"""

from __future__ import annotations

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
    """
    now = time.time()
    with connect() as conn:
        # Check existing failure record
        existing = conn.execute(
            "SELECT * FROM task_failures WHERE task_id = ? ORDER BY attempt DESC LIMIT 1",
            (task_id,),
        ).fetchone()

        if existing:
            new_attempt = existing["attempt"] + 1
            is_dead_letter = new_attempt >= max_retries
            dead_letter_at = now if is_dead_letter else None
            status = "dead_letter" if is_dead_letter else "failed"
            conn.execute(
                """UPDATE task_failures
                   SET attempt=?, error=?, last_attempt_at=?, dead_letter_at=?, status=?
                   WHERE task_id=? AND attempt=?""",
                (new_attempt, error, now, dead_letter_at, status, task_id, existing["attempt"]),
            )
            return {"recorded": True, "status": status, "attempt": new_attempt}
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
            return {"recorded": True, "status": status, "attempt": 1}


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
