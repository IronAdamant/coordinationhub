#!/usr/bin/env python3
"""Regenerate machine-owned sections in CoordinationHub docs.

Scans the source tree and rewrites content between marker comments in
target docs. Pure stdlib, zero third-party deps (consistent with the
rest of the project).

Usage:
    python scripts/gen_docs.py          # rewrite docs in place
    python scripts/gen_docs.py --check  # exit 1 if any doc would change

Marker conventions:

    Block content (tables, trees — content on its own lines):
        <!-- GEN:section-name -->
        ...generated content...
        <!-- /GEN -->

    Inline values (counts, version strings — content on same line):
        <!-- GEN:test-count -->297<!-- /GEN -->

The script is idempotent. Running twice produces the same output.
Unknown marker names raise an error to catch typos.

Available generators:
    file-inventory    Full table of source files with LOC and summaries.
    directory-tree    ASCII directory listing with per-file LOC.
    largest-files     Top-N table of largest source files annotated with LOC tier.
    mcp-tools         Table of all MCP tools with descriptions.
    test-count        Integer test count from pytest --collect-only.
    tool-count        Integer count from len(TOOL_SCHEMAS).
    cli-count         Integer count from len(cli._COMMANDS).
    version           Version string from pyproject.toml.

The ``--check`` mode also runs a stale-phrase scan against current-state
docs (AGENTS.md + wiki-local/spec-project.md). Phrases in
``STALE_PHRASES`` reference symbols/files that have been deleted or
renamed; if they reappear in current-state prose, the hook fails. This
guards against the doc-drift pattern that motivated
findings/post_opus_review_5_followups/02.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG_DIR = REPO_ROOT / "coordinationhub"


# ------------------------------------------------------------------ #
# Source scanners
# ------------------------------------------------------------------ #

def count_loc(path: Path) -> int:
    """Count non-blank, non-comment lines."""
    loc = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            loc += 1
    return loc


def module_docstring(path: Path) -> str:
    """Return the first line of the module docstring, or ''."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return ""
    doc = ast.get_docstring(tree)
    if not doc:
        return ""
    return doc.strip().split("\n")[0].rstrip(".")


def scan_package() -> list[dict]:
    """Return sorted list of {path, loc, summary} for each .py in coordinationhub/."""
    entries: list[dict] = []
    for path in sorted(PKG_DIR.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT)
        if "__pycache__" in rel.parts:
            continue
        entries.append({
            "path": str(rel),
            "loc": count_loc(path),
            "summary": module_docstring(path),
        })
    return entries


def get_version() -> str:
    init_path = REPO_ROOT / "coordinationhub" / "__init__.py"
    for line in init_path.read_text().splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError(f"__version__ not found in {init_path}")


def get_test_count() -> int:
    """Run pytest --collect-only -q and parse the summary line."""
    result = subprocess.run(
        ["python", "-m", "pytest", "--collect-only", "-q", "tests/"],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    for line in reversed(result.stdout.strip().splitlines()):
        m = re.search(r"(\d+)\s+tests?\s+collected", line)
        if m:
            return int(m.group(1))
        # Newer pytest: "297 tests collected in 0.42s" — already matched
        # Fallback: bare "297 tests" line
        m2 = re.match(r"^(\d+)\s+tests?$", line.strip())
        if m2:
            return int(m2.group(1))
    return 0


def _import_schemas():
    """Import coordinationhub.schemas without polluting the path permanently."""
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from coordinationhub.schemas import TOOL_SCHEMAS
        return TOOL_SCHEMAS
    finally:
        sys.path.pop(0)


def get_tool_count() -> int:
    return len(_import_schemas())


def get_cli_count() -> int:
    """Return the number of CLI subcommands declared in coordinationhub.cli."""
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from coordinationhub.cli import _COMMANDS
        return len(_COMMANDS)
    finally:
        sys.path.pop(0)


def get_mcp_tools() -> list[tuple[str, str]]:
    """Return [(name, first-sentence description), ...] for all MCP tools."""
    schemas = _import_schemas()
    out: list[tuple[str, str]] = []
    for name, spec in schemas.items():
        desc = spec.get("description", "").strip()
        # First sentence, capped at 120 chars to keep tables readable
        first = desc.split(". ")[0].rstrip(".")
        if len(first) > 120:
            first = first[:117] + "..."
        out.append((name, first))
    return out


# ------------------------------------------------------------------ #
# Renderers
# ------------------------------------------------------------------ #

def _escape_pipe(s: str) -> str:
    return s.replace("|", "\\|")


def render_file_inventory(entries: list[dict]) -> str:
    lines = ["| Path | LOC | Purpose |", "|------|-----|---------|"]
    for e in entries:
        summary = _escape_pipe(e["summary"] or "—")
        lines.append(f"| `{e['path']}` | {e['loc']} | {summary} |")
    return "\n".join(lines)


def render_directory_tree(entries: list[dict]) -> str:
    """ASCII tree grouped by directory under coordinationhub/."""
    lines = ["```", "coordinationhub/"]
    by_dir: dict[str, list[dict]] = {}
    for e in entries:
        parts = Path(e["path"]).parts
        if len(parts) == 2:  # coordinationhub/xxx.py
            dir_key = ""
        else:
            dir_key = "/".join(parts[1:-1])
        by_dir.setdefault(dir_key, []).append(e)

    # Top-level files, sorted by name
    for e in sorted(by_dir.get("", []), key=lambda x: Path(x["path"]).name):
        name = Path(e["path"]).name
        summary = e["summary"] or ""
        pad = max(1, 22 - len(name))
        lines.append(f"  {name}{' ' * pad}— {summary} (~{e['loc']} LOC)")

    # Subdirectories
    for dir_key in sorted(k for k in by_dir if k):
        lines.append(f"  {dir_key}/")
        for e in sorted(by_dir[dir_key], key=lambda x: Path(x["path"]).name):
            name = Path(e["path"]).name
            summary = e["summary"] or ""
            pad = max(1, 20 - len(name))
            lines.append(f"    {name}{' ' * pad}— {summary} (~{e['loc']} LOC)")

    lines.append("```")
    return "\n".join(lines)


def render_mcp_tools(tools: list[tuple[str, str]]) -> str:
    lines = ["| Tool | Description |", "|------|-------------|"]
    for name, desc in tools:
        lines.append(f"| `{name}` | {_escape_pipe(desc)} |")
    return "\n".join(lines)


# ------------------------------------------------------------------ #
# LOC tier policy (kept in sync with AGENTS.md "LOC Policy" section)
# ------------------------------------------------------------------ #

# (path-suffix match, tier name, soft cap; None = exempt)
LOC_TIERS: list[tuple[str, str, int | None]] = [
    ("coordinationhub/core.py", "engine", None),
    ("coordinationhub/mcp_server.py", "transport", 700),
    ("coordinationhub/mcp_stdio.py", "transport", 700),
    ("coordinationhub/cli_parser.py", "transport", 700),
    ("coordinationhub/db_migrations.py", "migrations", 800),
    ("plugins/dashboard/dashboard_js.py", "data", None),
    ("plugins/dashboard/dashboard_css.py", "data", None),
]


def _tier_for(path: str) -> tuple[str, int | None]:
    for suffix, tier, cap in LOC_TIERS:
        if path.endswith(suffix):
            return tier, cap
    return "primitive", 550


def render_largest_files(entries: list[dict], top_n: int = 8) -> str:
    """Render top-N files by code-LOC with their tier + cap status.

    Used in ``AGENTS.md`` so the LOC-policy section's "today's snapshot"
    table is regenerated by the pre-commit hook instead of drifting.
    """
    rows = ["| Path | Code-LOC | Tier | Status |",
            "|------|----------|------|--------|"]
    biggest = sorted(entries, key=lambda e: -e["loc"])[:top_n]
    for e in biggest:
        tier, cap = _tier_for(e["path"])
        if cap is None:
            status = "exempt"
        elif e["loc"] <= cap:
            status = f"OK (≤ {cap})"
        else:
            status = f"OVER (cap {cap}) — split planned"
        rows.append(f"| `{e['path']}` | {e['loc']} | {tier} | {status} |")
    return "\n".join(rows)


# ------------------------------------------------------------------ #
# Stale-phrase scanner (B2 from post_opus_review_5_followups/02)
# ------------------------------------------------------------------ #

# Phrase -> reason. A phrase listed here MUST NOT appear in any
# current-state doc (see ``STALE_SCAN_TARGETS``). Historical-changelog
# docs are exempt because the phrases are accurate as records of past
# state. Add an entry every time a symbol/file is removed or renamed.
STALE_PHRASES: dict[str, str] = {
    "core_locking.py": "deleted in T6.22 — primitives moved to locking_subsystem.py",
    "core_broadcasts.py": "deleted in T6.22 — primitives moved to broadcast_subsystem.py",
    "LockingMixin": "extracted to LockingSubsystem in T6.22 (no longer a mixin)",
    "BroadcastMixin": "extracted to BroadcastSubsystem in T6.22 (no longer a mixin)",
}

# Docs whose CURRENT prose must be free of stale phrases. Changelog-
# shaped docs (COMPLETE_PROJECT_DOCUMENTATION.md, LLM_Development.md)
# legitimately mention deleted symbols inside dated version sections,
# so they're excluded from this scan.
STALE_SCAN_TARGETS: list[str] = [
    "AGENTS.md",
    "wiki-local/spec-project.md",
]


def find_stale_phrases() -> list[tuple[str, int, str, str]]:
    """Return (path, line_no, phrase, reason) for every stale-phrase hit
    in a current-state doc. A line that includes ``<!-- ALLOW-STALE -->``
    is skipped — escape hatch for genuine cross-references."""
    hits: list[tuple[str, int, str, str]] = []
    for rel in STALE_SCAN_TARGETS:
        path = REPO_ROOT / rel
        if not path.exists():
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            # Escape hatch: ``<!-- ALLOW-STALE -->`` or
            # ``<!-- ALLOW-STALE: <reason> -->`` skips the line.
            if "<!-- ALLOW-STALE" in line:
                continue
            for phrase, reason in STALE_PHRASES.items():
                if phrase in line:
                    hits.append((rel, n, phrase, reason))
    return hits


# ------------------------------------------------------------------ #
# Marker-based rewriter
# ------------------------------------------------------------------ #

# Inline markers have no newline between open and content:
#   <!-- GEN:test-count -->297<!-- /GEN -->
# Block markers have newlines and multi-line content:
#   <!-- GEN:file-inventory -->
#   | Path | LOC | ... |
#   <!-- /GEN -->

# The negative lookahead on the body prevents a malformed or prose-embedded
# ``<!-- GEN:name -->`` token (with no matching closer before a real marker's
# closer) from greedily swallowing unrelated content across intervening
# markers.  If the body ever contains another ``<!-- GEN:`` or ``<!-- /GEN``
# token, the match fails and the regex engine moves on.
BLOCK_RE = re.compile(
    r"<!-- GEN:(?P<name>[\w-]+) -->"
    r"(?P<body>(?:(?!<!-- /?GEN).)*?)"
    r"<!-- /GEN -->",
    re.DOTALL,
)


def rewrite_markers(text: str, generators: dict[str, str], source: str) -> str:
    """Replace every <!-- GEN:name --> block with the generator's output."""

    def replace(match: re.Match) -> str:
        name = match.group("name")
        if name not in generators:
            raise KeyError(f"{source}: unknown GEN marker {name!r}")
        content = generators[name]
        old_body = match.group("body")
        # Detect inline vs block by whether old body spans multiple lines
        if "\n" in old_body:
            return f"<!-- GEN:{name} -->\n{content}\n<!-- /GEN -->"
        return f"<!-- GEN:{name} -->{content}<!-- /GEN -->"

    return BLOCK_RE.sub(replace, text)


# ------------------------------------------------------------------ #
# Driver
# ------------------------------------------------------------------ #

DOC_TARGETS = [
    "README.md",
    "AGENTS.md",
    "COMPLETE_PROJECT_DOCUMENTATION.md",
    "LLM_Development.md",
    "wiki-local/spec-project.md",
]


def build_generators() -> dict[str, str]:
    entries = scan_package()
    return {
        "file-inventory": render_file_inventory(entries),
        "directory-tree": render_directory_tree(entries),
        "largest-files": render_largest_files(entries),
        "mcp-tools": render_mcp_tools(get_mcp_tools()),
        "test-count": str(get_test_count()),
        "tool-count": str(get_tool_count()),
        "cli-count": str(get_cli_count()),
        "version": get_version(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Regenerate machine-owned doc sections."
    )
    ap.add_argument("--check", action="store_true",
                    help="Exit 1 if any doc would change, don't write.")
    args = ap.parse_args()

    generators = build_generators()

    any_drift = False
    for rel in DOC_TARGETS:
        path = REPO_ROOT / rel
        if not path.exists():
            print(f"skip (missing): {rel}")
            continue
        old = path.read_text(encoding="utf-8")
        new = rewrite_markers(old, generators, source=rel)
        if old != new:
            any_drift = True
            if args.check:
                print(f"DRIFT: {rel}")
            else:
                path.write_text(new, encoding="utf-8")
                print(f"updated: {rel}")
        else:
            if not args.check:
                print(f"clean:   {rel}")

    # Stale-phrase scan runs in both check and write modes, but only
    # `--check` returns a non-zero exit. In write mode it just warns
    # since the script can't auto-fix prose.
    stale_hits = find_stale_phrases()
    if stale_hits:
        for rel, n, phrase, reason in stale_hits:
            msg = f"STALE: {rel}:{n}: {phrase!r} — {reason}"
            print(msg, file=sys.stderr)
        if args.check:
            print(
                "\nStale-phrase hits detected in current-state docs. "
                "Reword the prose, or annotate the line with "
                "'<!-- ALLOW-STALE -->' if it is a deliberate cross-reference.",
                file=sys.stderr,
            )
            return 1

    if args.check and any_drift:
        print(
            "\nDoc drift detected. Run 'python scripts/gen_docs.py' to regenerate.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
