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
    file-inventory       Full table of source files with LOC and summaries.
    directory-tree       ASCII directory listing with per-file LOC.
    largest-files        Top-N table of largest source files annotated with LOC tier.
    mcp-tools            Table of all MCP tools with descriptions.
    dispatch-coverage    Per-tool branch+line coverage table from coverage.json.
    test-count           Integer test count from pytest --collect-only.
    test-count-baseline  Pre-cleanup test count (frozen at 633 — see SECURITY_FIXES.md).
    audit-closed-count   Closed-tier-item count (frozen at 153 — gitignored audit doc).
    schema-version       Integer from _CURRENT_SCHEMA_VERSION in db_migrations.py.
    tool-count           Integer count from len(TOOL_SCHEMAS).
    cli-count            Integer count from len(cli._COMMANDS).
    version              Version string from pyproject.toml.

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


def get_schema_version() -> str:
    """Parse ``_CURRENT_SCHEMA_VERSION`` from db_migrations.py without import."""
    path = REPO_ROOT / "coordinationhub" / "db_migrations.py"
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^_CURRENT_SCHEMA_VERSION\s*=\s*(\d+)\s*$", line)
        if m:
            return m.group(1)
    raise RuntimeError(f"_CURRENT_SCHEMA_VERSION not found in {path}")


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

# Each entry maps a path suffix to a tier name and soft cap (None means
# exempt). Kept as a dataclass-shaped tuple so future fields (e.g. a
# rationale string) slot in without churning every callsite. Order
# matters: the first matching suffix wins.
from dataclasses import dataclass


@dataclass(frozen=True)
class TierEntry:
    """A single LOC-Policy tier assignment for a file path suffix."""
    suffix: str           # path-suffix (matched via str.endswith)
    tier: str             # tier name surfaced in the largest-files table
    cap: int | None       # soft LOC cap, or None for exempt


LOC_TIERS: list[TierEntry] = [
    TierEntry("coordinationhub/core.py", "engine", None),
    TierEntry("coordinationhub/mcp_server.py", "transport", 700),
    TierEntry("coordinationhub/mcp_stdio.py", "transport", 700),
    TierEntry("coordinationhub/cli_parser.py", "transport", 700),
    TierEntry("coordinationhub/db_migrations.py", "migrations", 800),
    TierEntry("plugins/dashboard/dashboard_js.py", "data", None),
    TierEntry("plugins/dashboard/dashboard_css.py", "data", None),
    TierEntry("plugins/dashboard/dashboard_html.py", "data", None),
]


# Inference patterns — used by ``--check-tier-coverage`` to flag files
# whose path looks like a non-primitive tier but which are NOT listed in
# ``LOC_TIERS``. The first matching pattern wins. Patterns are intentionally
# conservative: only file shapes the policy explicitly recognizes today
# (transport / migrations / data). A new file matching one of these
# patterns is a nudge to update LOC_TIERS, not a hard error.
TIER_INFERENCE: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^coordinationhub/mcp_.*\.py$"), "transport"),
    (re.compile(r"^coordinationhub/cli_parser\.py$"), "transport"),
    (re.compile(r"^coordinationhub/db_migrations\.py$"), "migrations"),
    (re.compile(
        r"^coordinationhub/plugins/dashboard/dashboard_.*\.py$",
    ), "data"),
]


def _tier_for(path: str) -> tuple[str, int | None]:
    for entry in LOC_TIERS:
        if path.endswith(entry.suffix):
            return entry.tier, entry.cap
    return "primitive", 550


def _explicit_paths() -> set[str]:
    """Return the set of path suffixes explicitly listed in LOC_TIERS."""
    return {e.suffix for e in LOC_TIERS}


def check_tier_coverage(entries: list[dict]) -> list[tuple[str, str]]:
    """Return [(path, inferred_tier), ...] for files whose name pattern
    matches a non-primitive tier but which are NOT explicitly listed in
    ``LOC_TIERS``. Used by ``--check-tier-coverage`` to surface drift
    when a new file lands that should probably be on the policy table.

    Returns an empty list when every non-primitive-shaped file already
    has an explicit entry — that is the steady state after each new
    file is registered."""
    listed = _explicit_paths()
    nudges: list[tuple[str, str]] = []
    for entry in entries:
        path = entry["path"]
        # Already explicit — fine.
        if any(path.endswith(s) for s in listed):
            continue
        for pattern, inferred_tier in TIER_INFERENCE:
            if pattern.match(path):
                nudges.append((path, inferred_tier))
                break
    return nudges


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
# Dispatch coverage (plan 01 of post_self_review_followups/)
# ------------------------------------------------------------------ #
#
# The substring meta-test in tests/test_dispatch_coverage.py only
# checks that each TOOL_DISPATCH entry is *invoked* somewhere under
# tests/. That floor is honest about reach but silent about depth: a
# tool whose only test calls it once and discards the return value
# scores the same as a tool with happy + failure-path coverage.
#
# This generator reads coverage.json (produced by
# ``pytest --cov=coordinationhub --cov-report=json --cov-branch``) and
# renders a per-tool table:
#
#   1. Each tool name from TOOL_DISPATCH.
#   2. Mapped via core.py to its subsystem target (e.g. spawn_subagent
#      → Spawner.spawn_subagent in spawner_subsystem.py).
#   3. Looked up in coverage.json's ``files[F]["functions"][C.M]``
#      summary block for honest line + branch percentages.
#
# When coverage.json is absent (e.g. ``--check`` run from an env
# without the dev extra), the table renders a one-line placeholder
# instead of failing — see ``--check`` exit semantics in main().

COVERAGE_JSON = REPO_ROOT / "coverage.json"


def _build_engine_subsystem_map() -> dict[str, tuple[str, str]]:
    """Walk core.py and return {attr_name: (class_name, module_name)} for
    every ``self._<sub> = <Class>(...)`` assignment in __init__. The
    module is inferred from the matching ``from .<module> import <Class>``
    line at the top of the file.

    Example return value:
        {
          "_spawner": ("Spawner", "spawner_subsystem"),
          "_work_intent": ("WorkIntent", "work_intent_subsystem"),
          ...
        }
    """
    src = (REPO_ROOT / "coordinationhub" / "core.py").read_text(
        encoding="utf-8",
    )
    tree = ast.parse(src)
    class_to_module: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                # Only record imports from sibling modules (relative).
                if node.level >= 1:
                    class_to_module[alias.name] = node.module

    out: dict[str, tuple[str, str]] = {}
    cls = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "CoordinationEngine"
    )
    init = next(
        (n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"),
        None,
    )
    if init is None:
        return out
    for stmt in ast.walk(init):
        if not isinstance(stmt, ast.Assign):
            continue
        if len(stmt.targets) != 1:
            continue
        target = stmt.targets[0]
        if not (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
            and target.attr.startswith("_")
        ):
            continue
        # Right-hand side must be a Call against a bare class name.
        rhs = stmt.value
        if not isinstance(rhs, ast.Call):
            continue
        if not isinstance(rhs.func, ast.Name):
            continue
        cls_name = rhs.func.id
        module = class_to_module.get(cls_name)
        if module is None:
            continue
        out[target.attr] = (cls_name, module)
    return out


def _engine_facade_target(method_name: str) -> tuple[str, str] | None:
    """For an engine facade method, return (subsystem_attr, target_method)
    or None if the method isn't a one-liner facade. Mirrors the shape
    check in tests/test_core_facade_invariant.py."""
    src = (REPO_ROOT / "coordinationhub" / "core.py").read_text(
        encoding="utf-8",
    )
    tree = ast.parse(src)
    cls = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "CoordinationEngine"
    )
    fn = next(
        (
            n for n in cls.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == method_name
        ),
        None,
    )
    if fn is None:
        return None
    body = [
        s for s in fn.body
        if not (
            isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)
        )
    ]
    if len(body) != 1 or not isinstance(body[0], ast.Return):
        return None
    call = body[0].value
    if not isinstance(call, ast.Call):
        return None
    func = call.func
    if not isinstance(func, ast.Attribute):
        return None
    receiver = func.value
    if not (
        isinstance(receiver, ast.Attribute)
        and isinstance(receiver.value, ast.Name)
        and receiver.value.id == "self"
        and receiver.attr.startswith("_")
    ):
        return None
    return receiver.attr, func.attr


def _coverage_for_function(
    coverage_data: dict, file_path: str, qualified_name: str,
) -> tuple[str, str] | None:
    """Return (line_pct, branch_pct) display strings for the named
    function in the given file, or None if not present in coverage.json."""
    file_entry = coverage_data.get("files", {}).get(file_path)
    if not file_entry:
        return None
    fn_entry = file_entry.get("functions", {}).get(qualified_name)
    if not fn_entry:
        return None
    summary = fn_entry.get("summary", {})
    line_pct = summary.get("percent_covered_display", "?")
    branch_pct = summary.get("percent_branches_covered_display", "?")
    return f"{line_pct}%", f"{branch_pct}%"


def render_dispatch_coverage() -> str:
    """Return the dispatch-coverage GEN block content.

    Skips gracefully when coverage.json is missing — rendering a
    one-line placeholder so ``--check`` doesn't fail in CI envs that
    haven't run with ``--cov``."""
    if not COVERAGE_JSON.exists():
        return (
            "_No `coverage.json` found. Run `pytest --cov=coordinationhub "
            "--cov-report=json --cov-branch` then `python scripts/gen_docs.py` "
            "to regenerate this table._"
        )
    try:
        coverage_data = _import_json_file(COVERAGE_JSON)
    except Exception as exc:
        return f"_coverage.json present but unreadable: {exc!r}_"

    sub_map = _build_engine_subsystem_map()
    schemas = _import_schemas()  # used to know which dispatch tools exist
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from coordinationhub.dispatch import TOOL_DISPATCH
    finally:
        sys.path.pop(0)

    rows: list[tuple[str, str, str, str, float]] = []
    # (tool, target_label, line_pct, branch_pct, sort_key=branch_float)
    for tool in sorted(TOOL_DISPATCH):
        engine_method, _allowed = TOOL_DISPATCH[tool]
        target = _engine_facade_target(engine_method)
        if target is None:
            rows.append((tool, "_(non-facade)_", "—", "—", 999.0))
            continue
        attr, method = target
        sub = sub_map.get(attr)
        if sub is None:
            rows.append((tool, f"_(unknown subsystem `{attr}`)_", "—", "—", 999.0))
            continue
        cls_name, module = sub
        file_path = f"coordinationhub/{module}.py"
        cov = _coverage_for_function(
            coverage_data, file_path, f"{cls_name}.{method}",
        )
        if cov is None:
            rows.append(
                (tool, f"`{module}.{cls_name}.{method}`", "—", "—", 999.0),
            )
            continue
        line_pct, branch_pct = cov
        # Sort key: numeric branch percentage (lowest first); fall back
        # to 999 when unparseable so unknowns sink.
        try:
            br = float(branch_pct.rstrip("%"))
        except ValueError:
            br = 999.0
        rows.append((
            tool, f"`{module}.{cls_name}.{method}`", line_pct, branch_pct, br,
        ))

    rows.sort(key=lambda r: (r[4], r[0]))  # lowest branch first, then name

    out = [
        "| Tool | Subsystem method | Line cov | Branch cov |",
        "|------|------------------|----------|------------|",
    ]
    for tool, target_label, line_pct, branch_pct, _br in rows:
        out.append(
            f"| `{tool}` | {target_label} | {line_pct} | {branch_pct} |",
        )
    return "\n".join(out)


def _import_json_file(path: Path) -> dict:
    import json as _json
    return _json.loads(path.read_text(encoding="utf-8"))


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


# A valid escape hatch must carry a non-empty reason after the colon, e.g.
# ``<!-- ALLOW-STALE: historical changelog -->``. Bare ``<!-- ALLOW-STALE -->``
# was previously accepted but it became a way to silence the linter without
# documenting *why* the stale phrase is legitimate. The strict regex below
# requires at least one non-whitespace character before the closing ``-->``.
_ALLOW_STALE_VALID = re.compile(r"<!--\s*ALLOW-STALE:\s*\S[^>]*-->")
_ALLOW_STALE_TOKEN = re.compile(r"<!--\s*ALLOW-STALE\b")


def find_stale_phrases() -> list[tuple[str, int, str, str]]:
    """Return (path, line_no, phrase, reason) for every stale-phrase hit
    in a current-state doc. Lines with a valid ``<!-- ALLOW-STALE: <reason> -->``
    are skipped. A bare ``<!-- ALLOW-STALE -->`` (no reason) is itself a
    hit — it surfaces with phrase ``<malformed-allow-stale>`` so the
    lint also reports escapes that evaded the rationale requirement."""
    hits: list[tuple[str, int, str, str]] = []
    for rel in STALE_SCAN_TARGETS:
        path = REPO_ROOT / rel
        if not path.exists():
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            has_token = _ALLOW_STALE_TOKEN.search(line) is not None
            has_valid = _ALLOW_STALE_VALID.search(line) is not None
            if has_token and not has_valid:
                hits.append((
                    rel, n, "<malformed-allow-stale>",
                    "ALLOW-STALE escape requires a reason: "
                    "<!-- ALLOW-STALE: <one-line reason> -->",
                ))
                # Don't skip phrase scanning — a malformed escape shouldn't
                # mask the underlying drift it was trying to silence.
            if has_valid:
                continue
            for phrase, reason in STALE_PHRASES.items():
                if phrase in line:
                    hits.append((rel, n, phrase, reason))
    return hits


def list_allow_stale() -> list[tuple[str, int, str]]:
    """Return (path, line_no, reason) for every valid ALLOW-STALE escape
    in a scan target. Used by ``--list-allow-stale`` to audit the set."""
    out: list[tuple[str, int, str]] = []
    reason_re = re.compile(r"<!--\s*ALLOW-STALE:\s*(\S[^>]*?)\s*-->")
    for rel in STALE_SCAN_TARGETS:
        path = REPO_ROOT / rel
        if not path.exists():
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            m = reason_re.search(line)
            if m:
                out.append((rel, n, m.group(1).strip()))
    return out


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
    "SECURITY_FIXES.md",
    "wiki-local/spec-project.md",
]


def build_generators() -> dict[str, str]:
    entries = scan_package()
    return {
        "file-inventory": render_file_inventory(entries),
        "directory-tree": render_directory_tree(entries),
        "largest-files": render_largest_files(entries),
        "mcp-tools": render_mcp_tools(get_mcp_tools()),
        "dispatch-coverage": render_dispatch_coverage(),
        "test-count": str(get_test_count()),
        "test-count-baseline": "633",  # frozen — pre-cleanup baseline
        "audit-closed-count": "153",   # frozen — gitignored audit doc not in CI
        "schema-version": get_schema_version(),
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
    ap.add_argument(
        "--list-allow-stale", action="store_true",
        help="Print every active <!-- ALLOW-STALE: ... --> escape with "
             "its reason and exit. Useful for periodic audit.",
    )
    ap.add_argument(
        "--check-tier-coverage", action="store_true",
        help="Report files whose name pattern suggests a non-primitive "
             "tier but which aren't explicitly listed in LOC_TIERS. "
             "Exit 1 if any nudges are produced.",
    )
    args = ap.parse_args()

    if args.check_tier_coverage:
        nudges = check_tier_coverage(scan_package())
        if not nudges:
            print("LOC_TIERS coverage is clean — every non-primitive-"
                  "shaped file is explicitly listed.")
            return 0
        for path, inferred_tier in nudges:
            print(
                f"NUDGE: {path} pattern-matches tier {inferred_tier!r} "
                f"but is not in LOC_TIERS. Add a TierEntry or move the "
                f"file out of the matched namespace.",
                file=sys.stderr,
            )
        return 1

    if args.list_allow_stale:
        rows = list_allow_stale()
        if not rows:
            print("No ALLOW-STALE escapes in current-state docs.")
            return 0
        for rel, n, reason in rows:
            print(f"{rel}:{n}: {reason}")
        return 0

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
