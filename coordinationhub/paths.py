"""Path normalization and project-root detection utilities.

Shared by CoordinationEngine and the CLI. Zero internal dependencies.
"""

from __future__ import annotations

from pathlib import Path


def detect_project_root(cwd: str | Path | None = None) -> Path | None:
    """Walk *cwd* upward looking for a ``.git`` directory.

    Returns the first directory containing ``.git``, or None if no root is found
    within 256 levels.
    """
    if cwd is None:
        cwd = Path.cwd()
    else:
        cwd = Path(cwd).resolve()
    path = cwd
    for _ in range(256):
        # T7.26: ``.git`` is a directory in a normal repo and a file in
        # a worktree or submodule (a pointer file containing
        # ``gitdir: ...``). Either counts as a valid root. ``.exists()``
        # also accepts broken symlinks that resolve to nothing, so be
        # explicit about the valid shapes.
        git_marker = path / ".git"
        if git_marker.is_dir() or git_marker.is_file():
            return path
        parent = path.parent
        if parent == path:
            break
        path = parent
    return None


def normalize_path(path: str, project_root: Path | None) -> str:
    """Return a posix-style path for *path*, relativized to *project_root* if possible.

    Absolute paths are returned as-is. Paths inside *project_root* are returned
    as relative paths. Paths outside *project_root* are returned as their
    resolved absolute form.
    """
    p = Path(path).resolve()
    norm = p.as_posix().replace("\\", "/")
    if project_root is not None:
        try:
            rel = p.relative_to(project_root.resolve())
            return rel.as_posix().replace("\\", "/")
        except ValueError:
            pass
    return norm
