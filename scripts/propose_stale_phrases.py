#!/usr/bin/env python3
"""Propose new ``STALE_PHRASES`` entries for files deleted under coordinationhub/.

This is a *proposal generator*, not an auto-add. The ``STALE_PHRASES``
dict in ``scripts/gen_docs.py`` is a manually curated allowlist of
symbols/files that no longer exist; the linter fails when those phrases
reappear in current-state docs. Adding entries by hand is reliable but
easy to forget — this script walks recent git history, finds files
deleted under ``coordinationhub/``, extracts public top-level names
(functions / classes / module-level constants) from the pre-deletion
revision, and prints proposed dict entries. Review and paste into
``STALE_PHRASES`` manually.

Usage:
    python scripts/propose_stale_phrases.py
        # Walks every commit since the last one that touched STALE_PHRASES.

    python scripts/propose_stale_phrases.py --since HEAD~30
        # Walks the given range explicitly.

The baseline default ("since the last STALE_PHRASES touch") avoids
re-proposing entries that were already added by hand.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GEN_DOCS_PATH = REPO_ROOT / "scripts" / "gen_docs.py"


def _run(cmd: list[str]) -> str:
    """Run a git command and return stdout (or '' on non-zero exit)."""
    result = subprocess.run(
        cmd, cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    return result.stdout if result.returncode == 0 else ""


def _last_stale_phrases_commit() -> str | None:
    """Return the SHA of the most recent commit that touched ``gen_docs.py``'s
    ``STALE_PHRASES`` dict, or ``None`` if no such commit exists."""
    out = _run([
        "git", "log", "-S", "STALE_PHRASES", "--pretty=format:%H",
        "-n", "1", "--", str(GEN_DOCS_PATH.relative_to(REPO_ROOT)),
    ])
    sha = out.strip()
    return sha or None


def _deleted_files_since(rev: str) -> list[tuple[str, str]]:
    """Return [(commit_sha, path), ...] for every file deleted under
    ``coordinationhub/`` in the range ``rev..HEAD``. The commit SHA is
    the deletion commit; the path is the deleted file's pre-deletion
    path (relative to repo root)."""
    out = _run([
        "git", "log", f"{rev}..HEAD",
        "--diff-filter=D", "--name-only", "--pretty=format:COMMIT %H",
        "--", "coordinationhub/",
    ])
    pairs: list[tuple[str, str]] = []
    cur_sha = ""
    for line in out.splitlines():
        if line.startswith("COMMIT "):
            cur_sha = line.split(" ", 1)[1].strip()
            continue
        path = line.strip()
        if path and path.endswith(".py"):
            pairs.append((cur_sha, path))
    return pairs


def _extract_top_level_names(source: str) -> list[str]:
    """Return public function / class / module-constant names defined in
    ``source``. Underscore-prefixed names are skipped (private)."""
    names: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return names
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                names.append(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    names.append(target.id)
    return names


def _file_at(commit: str, path: str) -> str:
    """Return the file contents at ``commit:path``, or '' if unavailable."""
    return _run(["git", "show", f"{commit}^:{path}"])


def propose_entries(since_rev: str) -> list[tuple[str, str]]:
    """Return [(phrase, reason), ...] proposals for files deleted in
    ``since_rev..HEAD``. Both filename and each public top-level name
    become candidate phrases — the human reviewer trims."""
    proposals: list[tuple[str, str]] = []
    seen: set[str] = set()
    for sha, path in _deleted_files_since(since_rev):
        # Filename itself (basename) — references like ``core_locking.py``
        # in prose are the most common drift signal.
        basename = Path(path).name
        if basename not in seen:
            proposals.append((
                basename, f"deleted in commit {sha[:7]} — file no longer exists",
            ))
            seen.add(basename)
        source = _file_at(sha, path)
        if not source:
            continue
        for name in _extract_top_level_names(source):
            if name in seen:
                continue
            proposals.append((
                name,
                f"removed with {basename} in commit {sha[:7]}",
            ))
            seen.add(name)
    return proposals


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Propose STALE_PHRASES entries for deleted files.",
    )
    ap.add_argument(
        "--since", default=None,
        help="Git rev to walk from (default: the last commit that touched "
             "STALE_PHRASES in scripts/gen_docs.py).",
    )
    args = ap.parse_args()

    since_rev = args.since
    if since_rev is None:
        since_rev = _last_stale_phrases_commit()
    if not since_rev:
        print(
            "Could not determine baseline. Pass --since explicitly.",
            file=sys.stderr,
        )
        return 1

    proposals = propose_entries(since_rev)
    if not proposals:
        print(f"No new deletions under coordinationhub/ since {since_rev[:7]}.")
        return 0

    print(f"# Proposed STALE_PHRASES entries (baseline: {since_rev[:7]}):")
    print("# Review and paste into STALE_PHRASES in scripts/gen_docs.py.")
    print()
    for phrase, reason in proposals:
        # Shape matches the existing dict literal so the output can be
        # pasted directly into ``STALE_PHRASES``.
        safe = phrase.replace('"', '\\"')
        print(f'    "{safe}": "{reason}",')
    return 0


if __name__ == "__main__":
    sys.exit(main())
