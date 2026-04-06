"""Visibility helpers for CoordinationHub: file ownership scan, agent status, file map.

Zero third-party dependencies.
"""

from __future__ import annotations

import json
import time as _time
from pathlib import Path
from typing import Any

from . import graphs as _graphs

# Default file extensions for project scan
DEFAULT_SCAN_EXTENSIONS = [".py", ".md", ".json", ".yaml", ".yml", ".txt", ".toml"]

SKIP_PARTS = frozenset({"__pycache__", ".pytest_cache", "node_modules", ".coordinationhub"})


def _default_owner_agent(connect) -> str:
    """Return the first-registered active agent, or 'unassigned'."""
    with connect() as conn:
        row = conn.execute(
            "SELECT agent_id FROM agents WHERE status = 'active' ORDER BY started_at ASC LIMIT 1"
        ).fetchone()
    return row["agent_id"] if row else "unassigned"


def store_responsibilities(
    connect,
    agent_id: str,
    graph_agent_id: str,
    role: str,
    model: str,
    responsibilities: list[str],
) -> None:
    """Store or update an agent's responsibilities from the coordination graph."""
    now = _time.time()
    with connect() as conn:
        conn.execute("""
            INSERT INTO agent_responsibilities
            (agent_id, graph_agent_id, role, model, responsibilities, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                graph_agent_id = excluded.graph_agent_id,
                role = excluded.role,
                model = excluded.model,
                responsibilities = excluded.responsibilities,
                updated_at = excluded.updated_at
        """, (agent_id, graph_agent_id, role, model, json.dumps(responsibilities), now))


def update_agent_status_tool(
    connect,
    agent_id: str,
    current_task: str,
) -> dict[str, Any]:
    """Tool implementation: update current_task for an agent."""
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
    connect,
    agent_id: str,
    lineage_fn,
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
        "active_locks": [l["document_path"] for l in locks],
        "lineage": lineage,
    }


def get_file_agent_map_tool(
    connect,
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Tool implementation: map of file → agent + responsibility summary."""
    with connect() as conn:
        if agent_id:
            rows = conn.execute("""
                SELECT fo.document_path, fo.assigned_agent_id, fo.task_description,
                       ar.role, ar.responsibilities
                FROM file_ownership fo
                LEFT JOIN agent_responsibilities ar ON fo.assigned_agent_id = ar.agent_id
                WHERE fo.assigned_agent_id = ?
                ORDER BY fo.document_path
            """, (agent_id,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT fo.document_path, fo.assigned_agent_id, fo.task_description,
                       ar.role, ar.responsibilities
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
            "role": row["role"] or "unknown",
            "responsibilities": responsibilities,
            "task_description": row["task_description"],
        })
    return {"files": entries, "total": len(entries)}


def scan_project_tool(
    connect,
    project_root,
    worktree_root: str | None = None,
    extensions: list[str] | None = None,
) -> dict[str, Any]:
    """Perform a file ownership scan of the worktree.

    Assigns every tracked file to its nearest responsible Agent ID based on:
    1. Exact path match in file_ownership (preserves prior assignment)
    2. Nearest ancestor directory with an owner
    3. First-registered active agent as fallback

    Skips paths with hidden or cache directory components.
    """
    root = Path(worktree_root) if worktree_root else project_root
    if root is None:
        return {"scanned": 0, "owned": 0, "error": "No project root"}
    exts = set(extensions) if extensions else set(DEFAULT_SCAN_EXTENSIONS)
    now = _time.time()

    with connect() as conn:
        ownership_rows = conn.execute(
            "SELECT document_path, assigned_agent_id FROM file_ownership"
        ).fetchall()
    path_to_agent: dict[str, str] = {
        row["document_path"]: row["assigned_agent_id"] for row in ownership_rows
    }

    # Build dir -> agent for nearest-ancestor lookup
    dir_to_agent: dict[str, str] = {}
    for doc_path, aid in path_to_agent.items():
        d = str(Path(doc_path).parent)
        while d:
            if d not in dir_to_agent:
                dir_to_agent[d] = aid
            parent = Path(d).parent
            if parent == Path(d):
                break
            d = str(parent)

    fallback_agent = _default_owner_agent(connect)
    scanned = 0
    owned = 0
    to_upsert: list[tuple[str, str, float]] = []

    for ext in exts:
        for path in root.rglob(f"*{ext}"):
            if any(part.startswith(".") or part in SKIP_PARTS for part in path.parts):
                continue
            scanned += 1
            rel = path.relative_to(root).as_posix()
            assigned = path_to_agent.get(rel)
            if assigned is None:
                d = str(path.parent)
                assigned = None
                while d:
                    if d in dir_to_agent:
                        assigned = dir_to_agent[d]
                        break
                    parent = Path(d).parent
                    if parent == Path(d):
                        break
                    d = str(parent)
                assigned = assigned or fallback_agent
            to_upsert.append((rel, assigned, now))
            owned += 1

    if to_upsert:
        with connect() as conn:
            conn.executemany(
                "INSERT INTO file_ownership (document_path, assigned_agent_id, assigned_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(document_path) DO UPDATE SET "
                "assigned_agent_id = excluded.assigned_agent_id, assigned_at = excluded.assigned_at",
                to_upsert,
            )

    return {"scanned": scanned, "owned": owned}
