"""File ownership scan for CoordinationHub.

Walks the worktree, assigns files to agents via nearest-ancestor inheritance,
optionally guided by the coordination graph roles (e.g., planner owns docs,
executor owns code), and upserts the file_ownership table.

Excluded path components: .git, __pycache__, .pytest_cache, node_modules,
.coordinationhub, .venv, venv, .env, .eggs, *.egg-info, .mypy_cache, .tox, .ruff_cache.

Zero internal dependencies on other coordinationhub modules.
"""

from __future__ import annotations

import json
import time as _time
from pathlib import Path
from typing import Any, Callable

DEFAULT_SCAN_EXTENSIONS = [".py", ".md", ".json", ".yaml", ".yml", ".txt", ".toml"]

# Paths containing these directory/file name components are skipped during scan.
SKIP_PARTS = frozenset({
    "__pycache__", ".pytest_cache", "node_modules", ".coordinationhub",
    ".git", ".venv", "venv", ".env", ".eggs", "*.egg-info",
    ".mypy_cache", ".tox", ".ruff_cache",
})


def _default_owner_agent(connect: Callable[[], Any]) -> str:
    """Return the first-registered active agent, or 'unassigned'."""
    with connect() as conn:
        row = conn.execute(
            "SELECT agent_id FROM agents WHERE status = 'active' ORDER BY started_at ASC LIMIT 1"
        ).fetchone()
    return row["agent_id"] if row else "unassigned"


def _get_spawned_agent_responsibilities(
    connect: Callable[[], Any],
    assigned_agent_id: str,
) -> tuple[str | None, list[str]]:
    """For a spawned agent, return (parent_graph_agent_id, inherited_responsibilities).

    If the agent has a parent defined in the lineage table, look up the parent's
    graph_agent_id and responsibilities from agent_responsibilities.
    """
    with connect() as conn:
        row = conn.execute("""
            SELECT ar.graph_agent_id, ar.responsibilities
            FROM lineage l
            JOIN agent_responsibilities ar ON l.parent_id = ar.agent_id
            WHERE l.child_id = ?
        """, (assigned_agent_id,)).fetchone()
    if row:
        import json
        graph_id = row["graph_agent_id"]
        resp = json.loads(row["responsibilities"]) if row["responsibilities"] else []
        return graph_id, resp
    return None, []


def _role_based_agent(
    graph: Any,
    path: Path,
) -> str | None:
    """Suggest an agent ID from the coordination graph based on file extension and role.

    Returns None if the graph is not loaded or no matching role is found.
    Extension-to-responsibility heuristics:
      .py  -> agent whose responsibilities include 'implement', 'write', 'code'
      .md/.yaml/.yml -> agent whose responsibilities include 'document', 'plan', 'spec'
      .json/.toml/.txt -> agent whose responsibilities include 'config', 'data'
    """
    if graph is None:
        return None
    ext = path.suffix.lower()
    # Keyword mapping from extension to relevant responsibility keywords
    ext_keywords: dict[str, tuple[str, ...]] = {
        ".py": ("implement", "write", "code", "develop"),
        ".md": ("document", "write", "spec", "plan", "note"),
        ".yaml": ("document", "spec", "plan", "config"),
        ".yml": ("document", "spec", "plan", "config"),
        ".json": ("config", "data", "implement"),
        ".toml": ("config", "data"),
        ".txt": ("document", "data", "note"),
    }
    keywords = ext_keywords.get(ext, ())
    for graph_id, agent_def in graph.agents.items():
        responsibilities = agent_def.get("responsibilities", [])
        if not isinstance(responsibilities, list):
            continue
        for kw in keywords:
            for resp in responsibilities:
                if kw in resp.lower():
                    return graph_id
    return None


def scan_project_tool(
    connect: Callable[[], Any],
    project_root: Path | None,
    worktree_root: str | None = None,
    extensions: list[str] | None = None,
    graph: Any = None,
) -> dict[str, Any]:
    """Perform a file ownership scan of the worktree.

    Assigns every tracked file to its nearest responsible Agent ID based on:
    1. Exact path match in file_ownership (preserves prior assignment)
    2. Nearest ancestor directory with an owner
    3. Coordination graph role (if loaded): assign .py to 'implement' roles, .md/.yaml
       to 'document' roles, etc.
    4. First-registered active agent as fallback

    Skips paths with hidden or cache directory components (see SKIP_PARTS).
    Spawned agents (agents with a parent_id in lineage) are resolved by looking up
    their parent's graph role for role-based assignment.
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
                if assigned is None:
                    # Try role-based assignment from coordination graph
                    role_agent = _role_based_agent(graph, path)
                    if role_agent is not None:
                        # Resolve graph_agent_id to the registered agent that implements this role
                        with connect() as conn:
                            row = conn.execute("""
                                SELECT agent_id FROM agent_responsibilities
                                WHERE graph_agent_id = ? AND agent_id IN (
                                    SELECT agent_id FROM agents WHERE status = 'active'
                                )
                                LIMIT 1
                            """, (role_agent,)).fetchone()
                            if row:
                                assigned = row["agent_id"]
                    # Fallback: spawned agent inherits parent's responsibility slice
                    if assigned is None and fallback_agent != "unassigned":
                        parent_graph_id, parent_resp = _get_spawned_agent_responsibilities(
                            connect, fallback_agent
                        )
                        if parent_graph_id:
                            role_agent = _role_based_agent(graph, path)
                            if role_agent == parent_graph_id:
                                # fallback_agent is a spawned agent inheriting parent role — keep it
                                pass
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


def store_responsibilities(
    connect: Callable[[], Any],
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
