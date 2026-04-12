"""Task registry primitives for CoordinationHub.

Supports a shared task board where parent agents assign work to child agents
via task IDs. Task summaries enable compression chains: child writes a summary
on completion, parent compresses it upward.

Zero internal dependencies — receives connect() from caller.
"""

from __future__ import annotations

import json
import time
from typing import Any

from .db import ConnectFn


def create_task(
    connect: ConnectFn,
    task_id: str,
    parent_agent_id: str,
    description: str,
    depends_on: list[str] | None = None,
) -> dict[str, Any]:
    """Create a new task in the registry."""
    now = time.time()
    with connect() as conn:
        cursor = conn.execute(
            """INSERT INTO tasks
            (id, parent_agent_id, description, created_at, updated_at, depends_on)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (task_id, parent_agent_id, description, now, now,
             json.dumps(depends_on) if depends_on else "[]"),
        )
    return {"created": True, "task_id": task_id}


def assign_task(
    connect: ConnectFn,
    task_id: str,
    assigned_agent_id: str,
) -> dict[str, Any]:
    """Assign a task to an agent."""
    now = time.time()
    with connect() as conn:
        conn.execute(
            "UPDATE tasks SET assigned_agent_id=?, updated_at=? WHERE id=?",
            (assigned_agent_id, now, task_id),
        )
    return {"assigned": True, "task_id": task_id}


def update_task_status(
    connect: ConnectFn,
    task_id: str,
    status: str,
    summary: str | None = None,
    blocked_by: str | None = None,
) -> dict[str, Any]:
    """Update task status, optionally with a completion summary or blocker."""
    now = time.time()
    with connect() as conn:
        if summary is not None:
            conn.execute(
                "UPDATE tasks SET status=?, summary=?, updated_at=? WHERE id=?",
                (status, summary, now, task_id),
            )
        elif blocked_by is not None:
            conn.execute(
                "UPDATE tasks SET status=?, blocked_by=?, updated_at=? WHERE id=?",
                (status, blocked_by, now, task_id),
            )
        else:
            conn.execute(
                "UPDATE tasks SET status=?, updated_at=? WHERE id=?",
                (status, now, task_id),
            )
    return {"updated": True, "task_id": task_id, "status": status}


def get_task(connect: ConnectFn, task_id: str) -> dict[str, Any] | None:
    """Get a single task by ID."""
    with connect() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    if d.get("depends_on"):
        d["depends_on"] = json.loads(d["depends_on"])
    return d


def get_child_tasks(
    connect: ConnectFn,
    parent_agent_id: str,
) -> list[dict[str, Any]]:
    """Get all tasks created by a given agent."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE parent_agent_id=? ORDER BY created_at",
            (parent_agent_id,),
        ).fetchall()
    tasks = []
    for row in rows:
        d = dict(row)
        if d.get("depends_on"):
            d["depends_on"] = json.loads(d["depends_on"])
        tasks.append(d)
    return tasks


def get_tasks_by_agent(
    connect: ConnectFn,
    assigned_agent_id: str,
) -> list[dict[str, Any]]:
    """Get all tasks assigned to a given agent."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE assigned_agent_id=? ORDER BY created_at",
            (assigned_agent_id,),
        ).fetchall()
    tasks = []
    for row in rows:
        d = dict(row)
        if d.get("depends_on"):
            d["depends_on"] = json.loads(d["depends_on"])
        tasks.append(d)
    return tasks


def get_all_tasks(connect: ConnectFn) -> list[dict[str, Any]]:
    """Get all tasks in the registry."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY created_at"
        ).fetchall()
    tasks = []
    for row in rows:
        d = dict(row)
        if d.get("depends_on"):
            d["depends_on"] = json.loads(d["depends_on"])
        tasks.append(d)
    return tasks
