"""Floor ratchet for dispatch-tool test coverage.

Closes plan 02 of ``findings/post_self_review_followups/``.

The plain meta-test in ``tests/test_dispatch_coverage.py`` only checks
that each ``TOOL_DISPATCH`` entry is *invoked* from at least one test
file. That floor is "zero references" — a regression that deletes one
of two tests covering a tool (e.g. removes the failure-path test but
leaves the happy-path test) goes unnoticed because the substring
match still fires from the surviving call-site. The audit-of-the-audit
in commit ``a4ce5a5`` landed 17 tests for the four weakest tools; this
test ratchets that work into a hard floor so a future regression has
to surface as an explicit edit to ``dispatch_coverage_floor.json``.

How it works:

- ``tests/dispatch_coverage_floor.json`` is checked-in. Each key is a
  dispatch tool name; the value is a sorted list of qualified test
  names (``module.py::TestClass::test_method`` or
  ``module.py::test_function``) that call the tool via
  ``engine.<tool>(...)`` or ``dispatch_tool(engine, "<tool>", ...)``.
- This test parses the test directory with AST, collects the same
  qualified names from the current suite, and asserts that every
  entry in the floor still exists in the current set.
- New tests are NOT required to be added to the floor — only that the
  recorded ones still exist. The floor is a one-way ratchet.

When you intentionally remove a covered test:

    python scripts/update_dispatch_floor.py

This rewrites the JSON from the current suite. Commit the diff in the
same change as the test removal — the diff is the audit trail.

This guard is complementary to plan 01 (real coverage measurement via
``coverage.py``); plan 01's branch-coverage ratchet would subsume
this, but until that lands the test-name floor catches the most
common regression with zero coupling to coverage tooling.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"
FLOOR_PATH = TESTS_DIR / "dispatch_coverage_floor.json"


def _collect_callsites_in(
    tree: ast.Module, mod_name: str,
) -> set[tuple[str, str]]:
    """Walk ``tree`` and return {(tool_name, qualified_test_name), ...}.

    Mirrors the logic in ``scripts/update_dispatch_floor.py``; kept
    inline here so the test has no import dependency on a script under
    ``scripts/`` (those are not on the test path)."""
    out: set[tuple[str, str]] = set()

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self._class_stack: list[str] = []
            self._func_stack: list[str] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self._class_stack.append(node.name)
            self.generic_visit(node)
            self._class_stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._func_stack.append(node.name)
            self.generic_visit(node)
            self._func_stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

        def _enclosing_test(self) -> str | None:
            if not self._func_stack:
                return None
            fn = self._func_stack[-1]
            if not fn.startswith("test_"):
                return None
            if self._class_stack:
                return f"{self._class_stack[-1]}::{fn}"
            return fn

        def visit_Call(self, node: ast.Call) -> None:
            test = self._enclosing_test()
            if test is not None:
                tool = _tool_from_call(node)
                if tool is not None:
                    out.add((tool, f"{mod_name}::{test}"))
            self.generic_visit(node)

    _Visitor().visit(tree)
    return out


def _tool_from_call(call: ast.Call) -> str | None:
    func = call.func
    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "engine"
    ):
        return func.attr
    if isinstance(func, ast.Name) and func.id == "dispatch_tool":
        if len(call.args) >= 2:
            second = call.args[1]
            if isinstance(second, ast.Constant) and isinstance(second.value, str):
                return second.value
    return None


def _current_callsites() -> dict[str, set[str]]:
    """tool -> set of qualified test names currently in tests/."""
    out: dict[str, set[str]] = {}
    for path in sorted(TESTS_DIR.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for tool, qualified in _collect_callsites_in(tree, path.name):
            out.setdefault(tool, set()).add(qualified)
    return out


def test_dispatch_coverage_floor_holds():
    """Every test name recorded in ``dispatch_coverage_floor.json`` must
    still exist in the current test suite. If you intentionally
    removed a test, regenerate the floor and commit the diff:

        python scripts/update_dispatch_floor.py

    The diff lets a reviewer see exactly which test was dropped and
    confirm the coverage isn't silently regressing.
    """
    floor: dict[str, list[str]] = json.loads(
        FLOOR_PATH.read_text(encoding="utf-8"),
    )
    current = _current_callsites()
    missing: dict[str, list[str]] = {}
    for tool, required_names in floor.items():
        have = current.get(tool, set())
        gone = [name for name in required_names if name not in have]
        if gone:
            missing[tool] = gone

    if missing:
        lines = [
            "Dispatch coverage floor regression — these test names were "
            "removed without updating dispatch_coverage_floor.json:",
        ]
        for tool, names in sorted(missing.items()):
            lines.append(f"  {tool}:")
            for n in names:
                lines.append(f"    - {n}")
        lines.append(
            "\nIf the removal was intentional, run "
            "`python scripts/update_dispatch_floor.py` and commit the diff.",
        )
        raise AssertionError("\n".join(lines))


def test_floor_file_shape_is_well_formed():
    """The floor file is checked-in and machine-edited; this guards
    against a malformed write (e.g. someone hand-edits and breaks JSON
    or stuffs a non-string into the list)."""
    raw = json.loads(FLOOR_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw, dict), "floor file must be a JSON object"
    for tool, names in raw.items():
        assert isinstance(tool, str) and tool, f"bad tool key: {tool!r}"
        assert isinstance(names, list), f"{tool!r} value must be a list"
        for name in names:
            assert isinstance(name, str), (
                f"{tool!r} contains non-string entry: {name!r}"
            )
            # Sanity: must look like a pytest qualified name.
            assert "::" in name, (
                f"{tool!r} entry {name!r} is not a qualified test "
                "name (expected ``module.py::TestClass::test_method`` or "
                "``module.py::test_function``)"
            )


def test_floor_covers_every_dispatch_tool():
    """The floor file must list every tool in TOOL_DISPATCH so adding a
    tool is a forcing function for adding test coverage. Empty lists are
    allowed — they record 'no structured callsites yet' explicitly,
    which is more honest than silent omission. The plain meta-test
    still flags zero-coverage tools at a higher level."""
    # Import lazily so the test doesn't depend on runtime side effects
    # of the package import order.
    from coordinationhub.dispatch import TOOL_DISPATCH
    floor: dict[str, list[str]] = json.loads(
        FLOOR_PATH.read_text(encoding="utf-8"),
    )
    missing = sorted(set(TOOL_DISPATCH) - set(floor))
    assert not missing, (
        f"dispatch tools missing from floor: {missing}. "
        "Run `python scripts/update_dispatch_floor.py` to refresh."
    )
