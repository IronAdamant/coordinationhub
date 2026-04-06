"""Agent status and file-map query helpers for CoordinationHub.

Zero internal dependencies on other coordinationhub modules.
"""

from __future__ import annotations

import json
from typing import Any, Callable


def update_agent_status_tool(
    connect: Callable[[], Any],
    agent_id: str,
    current_task: str,
) -> dict[str, Any]:
    """Tool implementation: update current_task for an agent."""
    import time as _time
    now = _time.time()
    with connect() as conn:
        row = conn.execute(
            "SELECT agent_id FROM agents WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        if row is None:
            return {"updated": False, "error": f"Agent not found: {agent_id}"}
        conn.execute("""
            INSERT INTO agent_responsibilities (agent_id, current_task, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                current_task = excluded.current_task,
                updated_at = excluded.updated_at
        """, (agent_id, current_task, now))
    return {"updated": True, "agent_id": agent_id, "current_task": current_task}


def get_agent_status_tool(
    connect: Callable[[], Any],
    agent_id: str,
    lineage_fn: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    """Tool implementation: full status for a specific agent."""
    with connect() as conn:
        agent_row = conn.execute(
            "SELECT * FROM agents WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        if agent_row is None:
            return {"error": f"Agent not found: {agent_id}"}
        resp_row = conn.execute(
            "SELECT * FROM agent_responsibilities WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        lineage = lineage_fn(agent_id)
        owned_files = conn.execute(
            "SELECT document_path, task_description FROM file_ownership "
            "WHERE assigned_agent_id = ?", (agent_id,)
        ).fetchall()
        locks = conn.execute(
            "SELECT document_path, lock_type FROM document_locks WHERE locked_by = ?",
            (agent_id,)
        ).fetchall()
    resp = dict(resp_row) if resp_row else {}
    responsibilities = json.loads(resp.get("responsibilities") or "[]") if resp else []
    # owned_files_with_tasks: Agent ID -> file -> task description
    owned_files_with_tasks = [
        {"file": f["document_path"], "task": f["task_description"] or ""}
        for f in owned_files
    ]
    return {
        "agent_id": agent_id,
        "status": agent_row["status"],
        "parent_id": agent_row["parent_id"],
        "graph_agent_id": resp.get("graph_agent_id"),
        "role": resp.get("role"),
        "model": resp.get("model"),
        "responsibilities": responsibilities,
        "current_task": resp.get("current_task"),
        "owned_files": [f["document_path"] for f in owned_files],
        "owned_files_with_tasks": owned_files_with_tasks,
        "active_locks": [l["document_path"] for l in locks],
        "lineage": lineage,
    }


def get_file_agent_map_tool(
    connect: Callable[[], Any],
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Tool implementation: map of file → agent + responsibility summary."""
    with connect() as conn:
        if agent_id:
            rows = conn.execute("""
                SELECT fo.document_path, fo.assigned_agent_id, fo.task_description,
                       ar.graph_agent_id, ar.role, ar.responsibilities
                FROM file_ownership fo
                LEFT JOIN agent_responsibilities ar ON fo.assigned_agent_id = ar.agent_id
                WHERE fo.assigned_agent_id = ?
                ORDER BY fo.document_path
            """, (agent_id,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT fo.document_path, fo.assigned_agent_id, fo.task_description,
                       ar.graph_agent_id, ar.role, ar.responsibilities
                FROM file_ownership fo
                LEFT JOIN agent_responsibilities ar ON fo.assigned_agent_id = ar.agent_id
                ORDER BY fo.assigned_agent_id, fo.document_path
            """).fetchall()
    entries = []
    for row in rows:
        resp = dict(row)
        responsibilities = json.loads(resp.get("responsibilities") or "[]") if resp.get("responsibilities") else []
        entries.append({
            "document_path": row["document_path"],
            "assigned_agent_id": row["assigned_agent_id"],
            "graph_agent_id": row["graph_agent_id"] or "unknown",
            "role": row["role"] or "unknown",
            "responsibilities": responsibilities,
            "task_description": row["task_description"],
        })
    return {"files": entries, "total": len(entries)}
