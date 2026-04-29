"""Facade-shape invariant for ``CoordinationEngine`` in ``core.py``.

Closes plan 03 (Thread B) of
``findings/post_self_review_followups/``.

T6.22 collapsed the twelve mixins into composed subsystem attributes,
and ``core.py`` shrank to a "facade only" host. The LOC Policy in
``AGENTS.md`` exempts ``core.py`` from the 550-LOC primitive cap on the
explicit promise that it contains only ``__init__`` wiring, lifecycle
methods, and one-liner facades that delegate to a ``self._<subsystem>``
attribute. That promise was load-bearing for the cap exemption but
unverified — this test makes it executable.

For every public method on :class:`CoordinationEngine` (anything not
starting with ``_``), the body must either be:

1. A single ``return self._<sub>.<method>(...)`` call (the facade
   shape), OR
2. A name listed in :data:`LIFECYCLE_ALLOWLIST` with rationale.

Anything else is a sign that real logic has drifted into ``core.py``,
which is the exact failure mode the LOC exemption was guarding
against. The fix is either to move the logic into a subsystem file or
to add a new allowlist entry with one-line rationale (which makes the
drift visible in the diff).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


# Each entry: method-name -> rationale. The rationale is the audit
# trail — adding a new entry here is what makes a non-facade method
# legitimate. New entries should be rare and require the same kind of
# review as an LOC-cap waiver.
LIFECYCLE_ALLOWLIST: dict[str, str] = {
    "start": (
        "Lifecycle: starts storage, warms the lock cache, loads the "
        "coordination graph, and conditionally starts housekeeping. "
        "Spans subsystems by design — this IS the wiring step."
    ),
    "close": (
        "Lifecycle: stops the housekeeping scheduler then closes "
        "storage. Two-step shutdown so close() can't race a prune."
    ),
    "read_only_engine": (
        "Replica factory. Constructs a fresh CoordinationEngine and "
        "rebinds each subsystem's _connect to the read-only "
        "connection. The per-subsystem rebinds are intentional and "
        "documented in-method; collapsing them into a single helper "
        "would hide the audit trail."
    ),
}

CORE_PATH = Path(__file__).resolve().parent.parent / "coordinationhub" / "core.py"


def _strip_docstring(body: list[ast.stmt]) -> list[ast.stmt]:
    """Drop a leading docstring expression if present."""
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1:]
    return body


def _is_facade_shape(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True iff the method body is a single ``return self._<sub>.<m>(...)``.

    The shape we accept:
        return self._<subsystem>.<anything>(<args>)

    Where ``self._<subsystem>`` is an attribute access on ``self`` whose
    name starts with an underscore (matching the composed-subsystem
    convention from T6.22). The actual subsystem name and the called
    method are not validated — that would over-fit; the point is only
    "single delegation, no logic".
    """
    body = _strip_docstring(node.body)
    if len(body) != 1:
        return False
    stmt = body[0]
    if not isinstance(stmt, ast.Return) or stmt.value is None:
        return False
    call = stmt.value
    if not isinstance(call, ast.Call):
        return False
    func = call.func
    # Shape:  self._<sub> . <method>
    if not isinstance(func, ast.Attribute):
        return False
    receiver = func.value
    if not isinstance(receiver, ast.Attribute):
        return False
    if not (isinstance(receiver.value, ast.Name) and receiver.value.id == "self"):
        return False
    if not receiver.attr.startswith("_"):
        return False
    return True


def _public_methods() -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    tree = ast.parse(CORE_PATH.read_text(encoding="utf-8"))
    cls = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "CoordinationEngine"
    )
    out: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                out.append(node)
    return out


def test_every_public_method_is_facade_or_allowlisted():
    """The LOC-Policy exemption for ``core.py`` requires that public
    methods on ``CoordinationEngine`` be either one-liner facades or
    explicitly allowlisted as lifecycle / replica wiring.

    If you hit this test, you have two options:

    1. Move the new logic into a subsystem file and have
       ``core.py`` delegate. This is the default and what the
       LOC-Policy expects.
    2. If the method genuinely belongs on the engine itself (rare —
       see ``start`` / ``close`` / ``read_only_engine`` for the bar),
       add an entry to ``LIFECYCLE_ALLOWLIST`` above with a one-line
       rationale describing why subsystem decomposition isn't right.
    """
    violations: list[tuple[str, int]] = []
    for method in _public_methods():
        if method.name in LIFECYCLE_ALLOWLIST:
            continue
        if _is_facade_shape(method):
            continue
        violations.append((method.name, len(_strip_docstring(method.body))))

    assert not violations, (
        "core.py public methods must be one-liner facades or "
        "explicitly allowlisted in LIFECYCLE_ALLOWLIST. "
        f"Violations (name, stmt_count): {violations}. "
        "See tests/test_core_facade_invariant.py for the bar."
    )


def test_allowlist_entries_are_actually_used():
    """Every allowlist entry must correspond to a real method on the
    engine. Stale entries silently weaken the gate — they let a future
    method named ``start`` (a hypothetical second one) sneak through."""
    public = {m.name for m in _public_methods()}
    stale = sorted(name for name in LIFECYCLE_ALLOWLIST if name not in public)
    assert not stale, (
        f"LIFECYCLE_ALLOWLIST entries no longer present on "
        f"CoordinationEngine: {stale}. Remove them — a stale "
        "allowlist is the same shape of bug the test is guarding "
        "against."
    )


@pytest.mark.parametrize("name,reason", sorted(LIFECYCLE_ALLOWLIST.items()))
def test_allowlist_rationale_is_non_empty(name: str, reason: str):
    """An empty rationale defeats the audit-trail purpose of the
    allowlist. Force every entry to carry a real explanation."""
    assert reason.strip(), f"empty rationale for {name!r}"
    # Heuristic: a rationale shorter than ~30 chars is almost certainly
    # a placeholder. Loose bound, not a strict format check.
    assert len(reason.strip()) >= 30, (
        f"rationale for {name!r} is suspiciously short — write "
        "one or two sentences explaining why this method legitimately "
        "lives on the engine instead of in a subsystem."
    )
