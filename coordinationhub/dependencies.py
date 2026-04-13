"""Cross-agent dependency declaration and satisfaction tracking.

Declare that agent A needs agent B to complete task X before starting Y.
Before starting significant work, caller checks `check_dependencies` to
determine if dependencies are satisfied.

Zero internal dependencies — receives connect() from caller.
"""

from __future__ import annotations

import time
from typing import Any

from .db import ConnectFn


def declare_dependency(
    connect: ConnectFn,
    dependent_agent_id: str,
    depends_on_agent_id: str,
    depends_on_task_id: str | None = None,
    condition: str = "task_completed",
) -> dict[str, Any]:
    """Declare a cross-agent dependency."""
    now = time.time()
    with connect() as conn:
        cursor = conn.execute(
            """INSERT INTO agent_dependencies
            (dependent_agent_id, depends_on_agent_id, depends_on_task_id, condition, created_at)
            VALUES (?, ?, ?, ?, ?)""",
            (dependent_agent_id, depends_on_agent_id, depends_on_task_id, condition, now),
        )
        dep_id = cursor.lastrowid
    return {"declared": True, "dep_id": dep_id}


def check_dependencies(connect: ConnectFn, agent_id: str) -> list[dict[str, Any]]:
    """Return unsatisfied dependencies for agent_id."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT * FROM agent_dependencies
            WHERE dependent_agent_id=? AND satisfied=0""",
            (agent_id,),
        ).fetchall()
    unsatisfied = []
    for r in rows:
        d = dict(r)
        # Check if condition is met
        if d["condition"] == "task_completed" and d["depends_on_task_id"]:
            task_row = conn.execute(
                "SELECT status FROM tasks WHERE id=?",
                (d["depends_on_task_id"],),
            ).fetchone()
            if task_row and task_row["status"] == "completed":
                continue  # satisfied
        elif d["condition"] in ("agent_stopped", "agent_registered"):
            agent_row = conn.execute(
                "SELECT status FROM agents WHERE agent_id=?",
                (d["depends_on_agent_id"],),
            ).fetchone()
            if d["condition"] == "agent_stopped" and agent_row and agent_row["status"] == "stopped":
                continue  # satisfied
            elif d["condition"] == "agent_registered" and agent_row and agent_row["status"] == "active":
                continue  # satisfied
        unsatisfied.append(d)
    return unsatisfied


def satisfy_dependency(connect: ConnectFn, dep_id: int) -> dict[str, Any]:
    """Mark a dependency as satisfied."""
    now = time.time()
    with connect() as conn:
        conn.execute(
            "UPDATE agent_dependencies SET satisfied=1, satisfied_at=? WHERE id=?",
            (now, dep_id),
        )
    return {"satisfied": True, "dep_id": dep_id}


def get_blockers(connect: ConnectFn, agent_id: str) -> list[dict[str, Any]]:
    """Return dependency info blocking agent_id from starting."""
    return check_dependencies(connect, agent_id)


def get_all_dependencies(
    connect: ConnectFn,
    dependent_agent_id: str | None = None,
) -> list[dict[str, Any]]:
    """Get all declared dependencies, optionally filtered."""
    with connect() as conn:
        if dependent_agent_id is not None:
            rows = conn.execute(
                "SELECT * FROM agent_dependencies WHERE dependent_agent_id=?",
                (dependent_agent_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM agent_dependencies"
            ).fetchall()
    return [dict(r) for r in rows]


def satisfy_dependencies_for_task(connect: ConnectFn, task_id: str) -> dict[str, Any]:
    """Mark all dependencies with depends_on_task_id=task_id as satisfied.

    Called automatically by TaskMixin.update_task_status when a task completes.
    """
    now = time.time()
    with connect() as conn:
        cursor = conn.execute(
            """UPDATE agent_dependencies
               SET satisfied=1, satisfied_at=?
               WHERE depends_on_task_id=? AND satisfied=0""",
            (now, task_id),
        )
    return {"satisfied": cursor.rowcount}
