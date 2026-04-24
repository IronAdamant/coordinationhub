"""Agent status and file-map query helpers for CoordinationHub.

Zero internal dependencies on other coordinationhub modules.
"""

from __future__ import annotations

import json
from typing import Any, Callable


def update_agent_status_tool(
    connect: Callable[[], Any],
    agent_id: str,
    current_task: str | None = None,
    scope: list[str] | None = None,
) -> dict[str, Any]:
    """Tool implementation: update current_task and/or scope for an agent.

    T6.14: ``current_task`` is truncated to ``MAX_CURRENT_TASK`` before
    writing so an attacker-controlled IDE prompt can't wedge multiple
    megabytes into agent_responsibilities.
    """
    import time as _time
    import json as _json
    from .limits import MAX_CURRENT_TASK, truncate as _truncate
    current_task = _truncate(current_task, MAX_CURRENT_TASK)
    now = _time.time()
    with connect() as conn:
        row = conn.execute(
            "SELECT agent_id FROM agents WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        if row is None:
            return {"updated": False, "error": f"Agent not found: {agent_id}"}
        if current_task is not None:
            conn.execute("""
                INSERT INTO agent_responsibilities (agent_id, current_task, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    current_task = excluded.current_task,
                    updated_at = excluded.updated_at
            """, (agent_id, current_task, now))
        if scope is not None:
            scope_json = _json.dumps(scope)
            conn.execute("""
                INSERT INTO agent_responsibilities (agent_id, scope, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    scope = excluded.scope,
                    updated_at = excluded.updated_at
            """, (agent_id, scope_json, now))
    return {"updated": True, "agent_id": agent_id}


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
        agent_row = dict(agent_row)
        resp_row = conn.execute(
            "SELECT * FROM agent_responsibilities WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        resp = dict(resp_row) if resp_row else {}
        lineage = lineage_fn(agent_id)
        owned_files = conn.execute(
            "SELECT document_path, task_description FROM file_ownership "
            "WHERE assigned_agent_id = ?", (agent_id,)
        ).fetchall()
        owned_files = [dict(f) for f in owned_files]
        locks = conn.execute(
            "SELECT document_path, lock_type FROM document_locks WHERE locked_by = ?",
            (agent_id,)
        ).fetchall()
        locks = [dict(l) for l in locks]
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


def get_agent_tree_tool(
    connect: Callable[[], Any],
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Tool implementation: hierarchical agent tree for human/LLM review.

    If ``agent_id`` is None, returns the tree rooted at the oldest active root agent.
    """
    import time as _time

    def _find_oldest_root(conn):
        """Find the active root agent with the earliest last_heartbeat."""
        row = conn.execute(
            "SELECT agent_id FROM agents WHERE parent_id IS NULL AND status = 'active' "
            "ORDER BY last_heartbeat ASC LIMIT 1"
        ).fetchone()
        return row["agent_id"] if row else None

    def _build_node(conn, aid, now):
        """Build a single tree node for agent aid (no children filled yet)."""
        agent_row = conn.execute(
            "SELECT * FROM agents WHERE agent_id = ?", (aid,)
        ).fetchone()
        if agent_row is None:
            return None
        resp_row = conn.execute(
            "SELECT * FROM agent_responsibilities WHERE agent_id = ?", (aid,)
        ).fetchone()
        resp = dict(resp_row) if resp_row else {}
        responsibilities = json.loads(resp.get("responsibilities") or "[]") if resp else []
        # Active locks held by this agent
        lock_rows = conn.execute(
            "SELECT document_path, lock_type, region_start, region_end "
            "FROM document_locks WHERE locked_by = ? AND locked_at + lock_ttl > ?",
            (aid, now),
        ).fetchall()
        locks = []
        for lr in lock_rows:
            lock_entry: dict[str, Any] = {
                "path": lr["document_path"], "type": lr["lock_type"],
            }
            if lr["region_start"] is not None:
                lock_entry["region"] = f"L{lr['region_start']}-{lr['region_end']}"
            locks.append(lock_entry)
        # Check for boundary warnings on locked files
        if locks:
            lock_paths = [lk["path"] for lk in locks]
            placeholders = ",".join("?" * len(lock_paths))
            # T7.4: string concatenation instead of f-string. The
            # placeholders are ``?`` so no injection is possible today,
            # but dropping the f-string removes the footgun if someone
            # later edits the builder to interpolate anything else.
            ownership_rows = conn.execute(
                "SELECT document_path, assigned_agent_id FROM file_ownership "
                "WHERE document_path IN (" + placeholders + ")", lock_paths,
            ).fetchall()
            owner_map = {r["document_path"]: r["assigned_agent_id"] for r in ownership_rows}
            for lk in locks:
                owner = owner_map.get(lk["path"])
                if owner and owner != aid:
                    lk["boundary_warning"] = owner
        return {
            "agent_id": aid,
            "status": agent_row["status"],
            "graph_agent_id": resp.get("graph_agent_id"),
            "role": resp.get("role"),
            "current_task": resp.get("current_task"),
            "responsibilities": responsibilities,
            "locks": locks,
            "children": [],
        }

    # T1.14: cap recursion depth and track visited set so a cycle in
    # the parent_id chain (reparenting bugs under reap) can't blow the
    # Python stack.
    MAX_AGENT_TREE_DEPTH = 100
    visited: set[str] = set()

    def _build_tree(conn, aid, now, depth: int = 0):
        """Recursively build tree rooted at aid, filling in children."""
        if depth >= MAX_AGENT_TREE_DEPTH or aid in visited:
            return None
        visited.add(aid)
        node = _build_node(conn, aid, now)
        if node is None:
            return None
        child_rows = conn.execute(
            "SELECT agent_id FROM agents WHERE parent_id = ? AND status = 'active'",
            (aid,)
        ).fetchall()
        for child_row in child_rows:
            child_node = _build_tree(conn, child_row["agent_id"], now, depth + 1)
            if child_node is not None:
                node["children"].append(child_node)
        return node

    now = _time.time()

    with connect() as conn:
        if agent_id is None:
            agent_id = _find_oldest_root(conn)
            if agent_id is None:
                return {"error": "No active root agent found"}
        else:
            row = conn.execute(
                "SELECT agent_id FROM agents WHERE agent_id = ?", (agent_id,)
            ).fetchone()
            if row is None:
                return {"error": f"Agent not found: {agent_id}"}

        root = _build_tree(conn, agent_id, now)
        if root is None:
            return {"error": f"Agent not found: {agent_id}"}

        # Collect ancestors chain
        ancestors = []
        current = agent_id
        while True:
            row = conn.execute(
                "SELECT parent_id FROM agents WHERE agent_id = ?", (current,)
            ).fetchone()
            if row is None or row["parent_id"] is None:
                break
            ancestors.append({"agent_id": row["parent_id"]})
            current = row["parent_id"]

        text_tree = _render_rich_tree(root)

    return {"root": root, "ancestors": ancestors, "text_tree": text_tree}


def _render_rich_tree(root: dict[str, Any]) -> str:
    """Render a project-management-style agent tree with work items and locks.

    Output format:
        hub.99.0 [root] observing...
        ├── hub.99.0.0 [active] — "service consolidation"
        │   ├─ ◆ src/services/probe.js [exclusive]
        │   └─ ◆ routes.js [exclusive] ⚠ owned by hub.99.0.1
        └── hub.99.0.1 [active] — "route simplification"
            └─ ◆ routeLoader.js [shared L10-50]
    """
    lines: list[str] = []
    _render_node(root, lines, prefix="", is_root=True)
    return "\n".join(lines)


def _render_node(
    node: dict[str, Any], lines: list[str],
    prefix: str = "", is_root: bool = False, is_last: bool = True,
) -> None:
    # Agent header line
    if is_root:
        connector = ""
        child_prefix = ""
    else:
        connector = "└── " if is_last else "├── "
        child_prefix = prefix + ("    " if is_last else "│   ")

    aid = node["agent_id"]
    tag = node.get("graph_agent_id") or node["status"]
    header = f"{prefix}{connector}{aid} [{tag}]"
    if node.get("current_task"):
        header += f' — "{node["current_task"]}"'
    elif node.get("role"):
        header += f" — {node['role']}"
    lines.append(header)

    if is_root:
        child_prefix = ""

    # Work items: locks held by this agent
    locks = node.get("locks", [])
    children = node.get("children", [])
    detail_items = locks  # items rendered below the header
    total_sub = len(detail_items) + len(children)

    for i, lk in enumerate(locks):
        is_last_item = (i == len(locks) - 1) and not children
        item_connector = "└─ " if is_last_item else "├─ "
        path = lk["path"]
        lock_info = lk["type"]
        if lk.get("region"):
            lock_info += f" {lk['region']}"
        line = f"{child_prefix}{item_connector}◆ {path} [{lock_info}]"
        if lk.get("boundary_warning"):
            line += f" ⚠ owned by {lk['boundary_warning']}"
        lines.append(line)

    # Children (sub-agents)
    for i, child in enumerate(children):
        is_last_child = (i == len(children) - 1)
        _render_node(child, lines, child_prefix, is_root=False, is_last=is_last_child)


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
                "document_path": resp["document_path"],
                "assigned_agent_id": resp["assigned_agent_id"],
                "graph_agent_id": resp["graph_agent_id"] or "unknown",
                "role": resp["role"] or "unknown",
                "responsibilities": responsibilities,
                "task_description": resp["task_description"],
            })
        return {"files": entries, "total": len(entries)}
