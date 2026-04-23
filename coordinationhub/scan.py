"""File ownership scan for CoordinationHub.

Walks the worktree, assigns files to agents via nearest-ancestor inheritance,
optionally guided by the coordination graph roles (e.g., planner owns docs,
executor owns code), and upserts the file_ownership table.

Excluded path components: .git, __pycache__, .pytest_cache, node_modules,
.coordinationhub, .venv, venv, .env, .eggs, *.egg-info, .mypy_cache, .tox, .ruff_cache.

Zero internal dependencies on other coordinationhub modules.
"""

from __future__ import annotations

import fnmatch
import json
import time as _time
from pathlib import Path
from typing import Any, Callable

DEFAULT_SCAN_EXTENSIONS = [".py", ".md", ".json", ".yaml", ".yml", ".txt", ".toml"]

# Paths whose components exactly match one of these names are skipped.
SKIP_PARTS = frozenset({
    "__pycache__", ".pytest_cache", "node_modules", ".coordinationhub",
    ".git", ".venv", "venv", ".env", ".eggs",
    ".mypy_cache", ".tox", ".ruff_cache",
})

# T2.2: glob patterns against path components. Previously ``"*.egg-info"``
# was in SKIP_PARTS and compared with ``part in SKIP_PARTS``, so
# ``mypkg.egg-info`` was NOT skipped (literal match required). Now we
# fnmatch each component against this list.
SKIP_GLOBS: tuple[str, ...] = ("*.egg-info",)

# T2.2: safety ceilings applied during traversal. The pre-fix scan would
# walk an attacker-supplied ``worktree_root`` indefinitely.
MAX_SCAN_FILES = 50_000
MAX_SCAN_DEPTH = 20
MAX_SCAN_SECONDS = 30.0


def _is_skipped_part(name: str) -> bool:
    """True if *name* (a single path component) matches SKIP_PARTS or SKIP_GLOBS."""
    if name.startswith("."):
        return True
    if name in SKIP_PARTS:
        return True
    for pattern in SKIP_GLOBS:
        if fnmatch.fnmatchcase(name, pattern):
            return True
    return False


def _validate_scan_root(
    worktree_root: str | Path | None,
    project_root: Path | None,
) -> tuple[Path | None, str | None]:
    """T2.2: validate the caller-supplied worktree_root before walking.

    Returns ``(resolved_root, error_message)``. When ``error_message`` is
    not None the scan must abort — the caller should surface it to the
    client (or log it) and return a no-op result.

    Rules:
    - ``worktree_root`` must resolve to a real directory.
    - ``worktree_root`` must be equal to, or a descendant of, ``project_root``.
      Scanning ``/``, ``/etc``, or ``~`` is therefore rejected.
    - The resolved path itself must not be a symlink (we don't walk
      through one even if its target is inside the project root — if the
      caller wants it scanned they can pass the resolved path).
    """
    if worktree_root is None:
        return (project_root, None)
    root = Path(worktree_root)
    if not root.exists():
        return (None, f"worktree_root does not exist: {worktree_root!r}")
    if root.is_symlink():
        return (None, "worktree_root may not be a symlink")
    if not root.is_dir():
        return (None, f"worktree_root must be a directory: {worktree_root!r}")
    if project_root is None:
        # No project_root configured: accept any directory (caller-configured).
        return (root.resolve(), None)
    try:
        resolved = root.resolve()
        pr = project_root.resolve()
    except (OSError, RuntimeError) as exc:
        return (None, f"failed to resolve scan paths: {exc}")
    # resolved must be pr or a descendant
    try:
        resolved.relative_to(pr)
    except ValueError:
        return (None, "worktree_root must be inside project_root")
    return (resolved, None)


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
    # T2.2: validate worktree_root is a real directory inside project_root.
    # Reject symlinks and paths outside the configured project root.
    root, error = _validate_scan_root(worktree_root, project_root)
    if error is not None:
        return {"scanned": 0, "owned": 0, "error": error}
    if root is None:
        return {"scanned": 0, "owned": 0, "error": "No project root"}
    exts = set(extensions) if extensions else set(DEFAULT_SCAN_EXTENSIONS)
    now = _time.time()
    scan_deadline = now + MAX_SCAN_SECONDS

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

    # T2.2: manual walk instead of rglob so we can (a) skip symlinks,
    # (b) honour MAX_SCAN_DEPTH, and (c) bail out on count/time budgets.
    truncated = False

    def _iter_files():
        nonlocal truncated
        import os
        root_parts_len = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            # Enforce depth budget.
            depth = len(Path(dirpath).parts) - root_parts_len
            if depth > MAX_SCAN_DEPTH:
                dirnames[:] = []  # stop descending
                continue
            # Filter out skipped subdirectories in-place so os.walk never
            # descends into them.
            dirnames[:] = [d for d in dirnames if not _is_skipped_part(d)]
            for name in filenames:
                if _is_skipped_part(name):
                    continue
                if not any(name.endswith(ext) for ext in exts):
                    continue
                yield Path(dirpath) / name

    for path in _iter_files():
        if scanned >= MAX_SCAN_FILES:
            truncated = True
            break
        if _time.time() >= scan_deadline:
            truncated = True
            break
        # Don't follow symlinked files either — if it's a symlink we skip.
        if path.is_symlink():
            continue
        scanned += 1
        rel = path.relative_to(root).as_posix()
        assigned = path_to_agent.get(rel)
        # Re-entering the original per-file logic below.
        if True:  # preserve original control flow without another level of indent change
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

    result: dict[str, Any] = {"scanned": scanned, "owned": owned}
    if truncated:
        result["truncated"] = True
    return result


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
