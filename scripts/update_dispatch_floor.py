#!/usr/bin/env python3
"""Regenerate ``tests/dispatch_coverage_floor.json`` from the current suite.

The floor file maps each MCP dispatch tool to the test functions /
class methods that currently call it via ``engine.<tool>(...)`` or
``dispatch_tool(engine, "<tool>", ...)``. The companion test
(``tests/test_dispatch_coverage_floor.py``) loads the JSON and fails
if any listed test name has been removed without an explicit floor
update — the regression that the plain meta-test in
``tests/test_dispatch_coverage.py`` cannot catch (a happy-path test
left behind covers the substring check but the failure-path test for
the same tool was deleted).

Usage:
    python scripts/update_dispatch_floor.py

Run after intentionally removing a covered test (e.g. when
collapsing redundant happy-path coverage) so the floor reflects the
new ground truth. The diff in ``dispatch_coverage_floor.json`` then
shows exactly which test names were dropped — visible in code review.

This script imports nothing from coordinationhub at runtime to keep
the floor reproducible from a clean checkout, but it does need
``TOOL_DISPATCH`` to know the universe of tools — so it parses
``coordinationhub/dispatch.py`` with AST and pulls the dict keys.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"
FLOOR_PATH = TESTS_DIR / "dispatch_coverage_floor.json"


def _load_tool_dispatch_keys() -> set[str]:
    """Parse ``TOOL_DISPATCH`` from ``coordinationhub/dispatch.py`` without
    importing it (so the script runs even if the package has runtime
    dependencies the floor-update env doesn't have)."""
    src = (REPO_ROOT / "coordinationhub" / "dispatch.py").read_text(
        encoding="utf-8",
    )
    tree = ast.parse(src)
    for node in tree.body:
        # ``TOOL_DISPATCH`` is annotated (``TOOL_DISPATCH: dict[...] = {...}``)
        # so handle both annotated-assignment and bare-assignment shapes.
        if isinstance(node, ast.AnnAssign):
            target = node.target
            value = node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            value = node.value
        else:
            continue
        if not (isinstance(target, ast.Name) and target.id == "TOOL_DISPATCH"):
            continue
        if not isinstance(value, ast.Dict):
            continue
        keys: set[str] = set()
        for key in value.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                keys.add(key.value)
        return keys
    raise RuntimeError(
        "Could not locate TOOL_DISPATCH = {...} in coordinationhub/dispatch.py",
    )


def _collect_callsites(
    tree: ast.Module, mod_name: str,
) -> list[tuple[str, str]]:
    """Walk ``tree`` and return [(tool_name, qualified_test_name), ...].

    A "qualified test name" is ``module.py::TestClass::test_method`` for
    methods on a test class, or ``module.py::test_function`` for
    top-level test functions. Non-test functions are not recorded
    (we only care about names pytest can actually run)."""
    out: list[tuple[str, str]] = []

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
            """Return ``ClassName::method_name`` or ``test_function``,
            or None if we're not inside a recognized test container."""
            if not self._func_stack:
                return None
            # Innermost function is what pytest collects.
            fn = self._func_stack[-1]
            # Test functions / methods conventionally start with ``test_``.
            if not fn.startswith("test_"):
                return None
            if self._class_stack:
                # Nearest enclosing test class.
                cls = self._class_stack[-1]
                return f"{cls}::{fn}"
            return fn

        def visit_Call(self, node: ast.Call) -> None:
            test = self._enclosing_test()
            if test is not None:
                tool = _tool_from_call(node)
                if tool is not None:
                    out.append((tool, f"{mod_name}::{test}"))
            self.generic_visit(node)

    _Visitor().visit(tree)
    return out


def _tool_from_call(call: ast.Call) -> str | None:
    """Recognize the two callsite shapes that count as 'invokes tool X':

    1. ``engine.<tool>(...)``
    2. ``dispatch_tool(engine, "<tool>", ...)``

    Returns the tool name on match, or None.
    """
    func = call.func
    # Shape 1: engine.<tool>(...)
    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "engine"
    ):
        return func.attr
    # Shape 2: dispatch_tool(engine, "<tool>", ...)
    if isinstance(func, ast.Name) and func.id == "dispatch_tool":
        if len(call.args) >= 2:
            second = call.args[1]
            if isinstance(second, ast.Constant) and isinstance(second.value, str):
                return second.value
    return None


def build_floor() -> dict[str, list[str]]:
    """Build the floor: tool -> sorted list of qualified test names."""
    tools = _load_tool_dispatch_keys()
    floor: dict[str, set[str]] = {tool: set() for tool in tools}
    for path in sorted(TESTS_DIR.glob("*.py")):
        if path.name.startswith("test_") is False and path.name != "conftest.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for tool, qualified in _collect_callsites(tree, path.name):
            if tool in floor:
                floor[tool].add(qualified)
    return {tool: sorted(names) for tool, names in floor.items()}


def main() -> int:
    floor = build_floor()
    FLOOR_PATH.write_text(
        json.dumps(floor, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    total = sum(len(v) for v in floor.values())
    empty = sorted(t for t, v in floor.items() if not v)
    print(f"Wrote {FLOOR_PATH.relative_to(REPO_ROOT)} — "
          f"{len(floor)} tools, {total} test references.")
    if empty:
        print(
            f"NOTE: {len(empty)} tool(s) have zero structured callsites in "
            f"tests/. They still pass the meta-test if a docstring or "
            f"string-literal mention exists, but the floor cannot ratchet "
            f"on them: {', '.join(empty)}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
