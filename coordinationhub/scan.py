"""File ownership scan for CoordinationHub.

Walks the worktree, assigns files to agents via nearest-ancestor inheritance,
and upserts the file_ownership table. Zero internal dependencies.
"""

from __future__ import annotations

import time as _time
from pathlib import Path
from typing import Any, Callable

DEFAULT_SCAN_EXTENSIONS = [".py", ".md", ".json", ".yaml", ".yml", ".txt", ".toml"]

SKIP_PARTS = frozenset({"__pycache__", ".pytest_cache", "node_modules", ".coordinationhub"})


def _default_owner_agent(connect: Callable[[], Any]) -> str:
    """Return the first-registered active agent, or 'unassigned'."""
    with connect() as conn:
        row = conn.execute(
            "SELECT agent_id FROM agents WHERE status = 'active' ORDER BY started_at ASC LIMIT 1"
        ).fetchone()
    return row["agent_id"] if row else "unassigned"


def scan_project_tool(
    connect: Callable[[], Any],
    project_root: Path | None,
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
