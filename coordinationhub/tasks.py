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
    priority: int = 0,
) -> dict[str, Any]:
    """Create a new task in the registry."""
    now = time.time()
    with connect() as conn:
        cursor = conn.execute(
            """INSERT INTO tasks
            (id, parent_agent_id, description, created_at, updated_at, depends_on, priority)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (task_id, parent_agent_id, description, now, now,
             json.dumps(depends_on) if depends_on else "[]", priority),
        )
    return {"created": True, "task_id": task_id, "priority": priority}


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
        # Sync agent state: update current_task in agent_responsibilities
        row = conn.execute(
            "SELECT description FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
        if row:
            conn.execute("""
                INSERT INTO agent_responsibilities (agent_id, current_task, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    current_task = excluded.current_task,
                    updated_at = excluded.updated_at
            """, (assigned_agent_id, row["description"], now))
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
            "SELECT * FROM tasks WHERE parent_agent_id=? ORDER BY priority DESC, created_at",
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
            "SELECT * FROM tasks WHERE assigned_agent_id=? ORDER BY priority DESC, created_at",
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
            "SELECT * FROM tasks ORDER BY priority DESC, created_at"
        ).fetchall()
        tasks = []
        for row in rows:
            d = dict(row)
            if d.get("depends_on"):
                d["depends_on"] = json.loads(d["depends_on"])
            tasks.append(d)
    return tasks


def create_subtask(
    connect: ConnectFn,
    task_id: str,
    parent_task_id: str,
    parent_agent_id: str,
    description: str,
    depends_on: list[str] | None = None,
    priority: int = 0,
) -> dict[str, Any]:
    """Create a new subtask under an existing parent task."""
    now = time.time()
    with connect() as conn:
        conn.execute(
            """INSERT INTO tasks
            (id, parent_task_id, parent_agent_id, description, created_at, updated_at, depends_on, priority)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (task_id, parent_task_id, parent_agent_id, description, now, now,
             json.dumps(depends_on) if depends_on else "[]", priority),
        )
    return {"created": True, "task_id": task_id, "parent_task_id": parent_task_id, "priority": priority}


def get_subtasks(connect: ConnectFn, parent_task_id: str) -> list[dict[str, Any]]:
    """Get all direct subtasks of a given task."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE parent_task_id=? ORDER BY priority DESC, created_at",
            (parent_task_id,),
        ).fetchall()
        tasks = []
        for row in rows:
            d = dict(row)
            if d.get("depends_on"):
                d["depends_on"] = json.loads(d["depends_on"])
            tasks.append(d)
    return tasks


def get_task_tree(connect: ConnectFn, root_task_id: str) -> dict[str, Any]:
    """Get a task with all its subtasks recursively.

    Returns a dict with task data + 'subtasks' key containing list of child task trees.
    """
    def _build_tree(task_id: str) -> dict[str, Any] | None:
        with connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                return None
            d = dict(row)
            if d.get("depends_on"):
                d["depends_on"] = json.loads(d["depends_on"])
            children_rows = conn.execute(
                "SELECT id FROM tasks WHERE parent_task_id=? ORDER BY created_at",
                (task_id,),
            ).fetchall()
            child_ids = [child_row["id"] for child_row in children_rows]
        d["subtasks"] = []
        for child_id in child_ids:
            child_tree = _build_tree(child_id)
            if child_tree:
                d["subtasks"].append(child_tree)
        return d

    return _build_tree(root_task_id) or {}


def wait_for_task(
    connect: ConnectFn,
    task_id: str,
    timeout_s: float = 60.0,
    poll_interval_s: float = 2.0,
) -> dict[str, Any]:
    """Poll until a task reaches a terminal state (completed/failed) or timeout expires.

    Returns {"waited": True, "task_id": ..., "status": ..., "timed_out": False}
    or {"waited": False, "task_id": ..., "timed_out": True} if timeout expired.
    """
    import time
    start = time.time()
    terminal_states = {"completed", "failed"}
    while True:
        task = get_task(connect, task_id)
        if task is None:
            return {"waited": False, "task_id": task_id, "timed_out": True, "reason": "task_not_found"}
        if task.get("status") in terminal_states:
            return {"waited": True, "task_id": task_id, "status": task["status"], "timed_out": False}
        elapsed = time.time() - start
        if elapsed >= timeout_s:
            return {"waited": False, "task_id": task_id, "timed_out": True, "status": task.get("status")}
        time.sleep(min(poll_interval_s, timeout_s - elapsed))


def get_available_tasks(
    connect: ConnectFn,
    agent_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return tasks whose depends_on are all satisfied (completed) and not currently claimed.

    A task is "available" if:
    - Its status is "pending" (not yet claimed)
    - All tasks in its depends_on list have status "completed"
    - Optionally filtered to a specific agent_id
    """
    import json
    all_tasks = get_all_tasks(connect)
    available = []
    for task in all_tasks:
        if task.get("status") not in (None, "pending"):
            continue
        if agent_id and task.get("assigned_agent_id") != agent_id:
            continue
        depends_on = task.get("depends_on") or []
        if isinstance(depends_on, str):
            try:
                depends_on = json.loads(depends_on)
            except json.JSONDecodeError:
                depends_on = []
        if not depends_on:
            available.append(task)
            continue
        # Check all dependencies are satisfied (completed)
        deps_satisfied = True
        for dep_id in depends_on:
            dep_task = get_task(connect, dep_id)
            if dep_task is None or dep_task.get("status") != "completed":
                deps_satisfied = False
                break
        if deps_satisfied:
            available.append(task)
    return available


def suggest_task_assignments(connect: ConnectFn) -> list[dict[str, Any]]:
    """Suggest available tasks for idle agents.

    Returns a list of {task_id, description, suggested_agents} where each
    suggested agent has no currently assigned pending/in_progress tasks.
    """
    available = get_available_tasks(connect)
    if not available:
        return []

    with connect() as conn:
        # Find agents with no active tasks
        agent_rows = conn.execute(
            "SELECT agent_id FROM agents WHERE status = 'active'"
        ).fetchall()
        all_agent_ids = {r["agent_id"] for r in agent_rows}

        busy_rows = conn.execute(
            "SELECT assigned_agent_id FROM tasks WHERE status IN ('pending', 'in_progress')"
        ).fetchall()
        busy_agents = {r["assigned_agent_id"] for r in busy_rows if r["assigned_agent_id"]}

        idle_agents = sorted(all_agent_ids - busy_agents)
    suggestions: list[dict[str, Any]] = []
    for task in available:
        task_id = task["id"]
        description = task.get("description", "")
        # If task is already assigned to an idle agent, highlight that first
        assigned = task.get("assigned_agent_id")
        suggested = []
        if assigned and assigned in idle_agents:
            suggested.append(assigned)
        for aid in idle_agents:
            if aid not in suggested:
                suggested.append(aid)
        suggestions.append({
            "task_id": task_id,
            "description": description,
            "suggested_agents": suggested,
        })
    return suggestions
