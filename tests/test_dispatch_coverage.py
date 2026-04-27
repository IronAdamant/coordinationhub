"""Coverage tests for previously-uncovered MCP dispatch tools.

Closes findings/post_opus_review_5_followups/01_test_coverage_gaps.

Five tools in ``TOOL_DISPATCH`` had zero references anywhere under
``tests/`` before this file existed. The ``_handoff`` shadowing bug
discovered during T6.22 Broadcast extraction (commit ``fb9e200``) sat
unnoticed for ~6 commits because no test exercised
``broadcast(handoff_targets=...)``. The same shape of bug — a code path
the test suite doesn't visit — is statistically likely to be sitting in
any uncovered dispatch entry.

Each tool gets at least one happy-path test plus at least one
failure-path test (timeout, wrong agent, missing row, etc.). The final
``test_every_dispatch_tool_has_test_coverage`` asserts no tool in
``TOOL_DISPATCH`` regresses to zero references.
"""

from __future__ import annotations

import os
import threading
import time

import pytest

from coordinationhub import handoffs as _handoffs
from coordinationhub.dispatch import TOOL_DISPATCH


# --------------------------------------------------------------------- #
# acquire_coordinator_lease
# --------------------------------------------------------------------- #


class TestAcquireCoordinatorLease:
    """Single-writer leadership lease via :class:`Lease`."""

    def test_acquire_succeeds_when_unheld(self, engine):
        a = engine.generate_agent_id()
        engine.register_agent(a)

        result = engine.acquire_coordinator_lease(a, ttl=5.0)

        assert result["acquired"] is True
        assert result["holder_id"] == a
        assert result["ttl"] == 5.0
        assert result["expires_at"] > time.time()

    def test_acquire_fails_when_held_by_other(self, engine):
        a = engine.generate_agent_id()
        engine.register_agent(a)
        b = engine.generate_agent_id()
        engine.register_agent(b)

        first = engine.acquire_coordinator_lease(a, ttl=5.0)
        assert first["acquired"] is True

        second = engine.acquire_coordinator_lease(b, ttl=5.0)
        assert second["acquired"] is False
        assert second["holder"]["holder_id"] == a

    def test_acquire_uses_default_ttl_when_none(self, engine):
        a = engine.generate_agent_id()
        engine.register_agent(a)

        result = engine.acquire_coordinator_lease(a)

        assert result["acquired"] is True
        assert result["ttl"] > 0


# --------------------------------------------------------------------- #
# is_subagent_stop_requested
# --------------------------------------------------------------------- #


class TestIsSubagentStopRequested:
    """Boolean check that flips when the parent calls request_subagent_deregistration."""

    def test_flag_starts_false(self, engine):
        a = engine.generate_agent_id()
        engine.register_agent(a)

        result = engine.is_subagent_stop_requested(a)

        assert result["agent_id"] == a
        assert result["stop_requested"] is False

    def test_flag_flips_after_request_deregistration(self, engine):
        parent = engine.generate_agent_id()
        engine.register_agent(parent)
        child = engine.generate_agent_id(parent)
        engine.register_agent(child, parent)

        before = engine.is_subagent_stop_requested(child)
        assert before["stop_requested"] is False

        engine.request_subagent_deregistration(parent, child)

        after = engine.is_subagent_stop_requested(child)
        assert after["stop_requested"] is True
        assert after["stop_requested_at"] is not None

    def test_unknown_agent_returns_false_not_error(self, engine):
        result = engine.is_subagent_stop_requested("hub.nonexistent.0")

        assert result["stop_requested"] is False


# --------------------------------------------------------------------- #
# await_subagent_stopped
# --------------------------------------------------------------------- #


class TestAwaitSubagentStopped:
    """Wait-with-timeout for child deregistration. Untested means we
    don't know whether it wakes on the right event or hangs."""

    def test_already_stopped_returns_immediately(self, engine):
        parent = engine.generate_agent_id()
        engine.register_agent(parent)
        child = engine.generate_agent_id(parent)
        engine.register_agent(child, parent)
        engine.deregister_agent(child)

        start = time.time()
        result = engine.await_subagent_stopped(child, timeout=5.0)
        elapsed = time.time() - start

        assert result["stopped"] is True
        assert result["child_agent_id"] == child
        assert elapsed < 1.0, "should fast-path on already-stopped child"

    def test_unknown_child_returns_immediately(self, engine):
        start = time.time()
        result = engine.await_subagent_stopped("hub.nonexistent.0", timeout=5.0)
        elapsed = time.time() - start

        assert result["stopped"] is True
        assert elapsed < 1.0, "should fast-path on missing child"

    def test_wakes_on_child_deregistration(self, engine):
        parent = engine.generate_agent_id()
        engine.register_agent(parent)
        child = engine.generate_agent_id(parent)
        engine.register_agent(child, parent)

        # Start the wait in a worker; trigger deregistration shortly after.
        result_box: dict = {}

        def _waiter() -> None:
            result_box["r"] = engine.await_subagent_stopped(child, timeout=5.0)

        t = threading.Thread(target=_waiter)
        t.start()
        time.sleep(0.1)  # let the waiter park on the event bus
        engine.deregister_agent(child)
        t.join(timeout=5.0)

        assert "r" in result_box, "waiter never returned"
        assert result_box["r"]["stopped"] is True

    def test_times_out_and_signals_escalation(self, engine):
        parent = engine.generate_agent_id()
        engine.register_agent(parent)
        child = engine.generate_agent_id(parent)
        engine.register_agent(child, parent)

        start = time.time()
        result = engine.await_subagent_stopped(child, timeout=0.2)
        elapsed = time.time() - start

        assert result.get("timed_out") is True
        assert result.get("escalate") is True
        assert 0.15 < elapsed < 1.5


# --------------------------------------------------------------------- #
# await_agent
# --------------------------------------------------------------------- #


class TestAwaitAgent:
    """Wait-with-timeout for any-agent deregistration (sibling-await pattern)."""

    def test_already_stopped_returns_immediately(self, engine):
        a = engine.generate_agent_id()
        engine.register_agent(a)
        engine.deregister_agent(a)

        start = time.time()
        result = engine.await_agent(a, timeout_s=5.0)
        elapsed = time.time() - start

        assert result["awaited"] is True
        assert result["status"] == "stopped"
        assert elapsed < 1.0

    def test_unknown_agent_returns_immediately(self, engine):
        result = engine.await_agent("hub.nonexistent.0", timeout_s=0.5)

        assert result["awaited"] is True
        assert result["status"] == "not_found"

    def test_wakes_on_deregistration(self, engine):
        a = engine.generate_agent_id()
        engine.register_agent(a)

        result_box: dict = {}

        def _waiter() -> None:
            result_box["r"] = engine.await_agent(a, timeout_s=5.0)

        t = threading.Thread(target=_waiter)
        t.start()
        time.sleep(0.1)
        engine.deregister_agent(a)
        t.join(timeout=5.0)

        assert "r" in result_box
        assert result_box["r"]["awaited"] is True
        assert result_box["r"]["status"] == "stopped"

    def test_times_out_when_agent_stays_active(self, engine):
        a = engine.generate_agent_id()
        engine.register_agent(a)

        start = time.time()
        result = engine.await_agent(a, timeout_s=0.2)
        elapsed = time.time() - start

        assert result["awaited"] is False
        assert result["status"] == "timeout"
        assert 0.15 < elapsed < 1.5


# --------------------------------------------------------------------- #
# wait_for_handoff
# --------------------------------------------------------------------- #


class TestWaitForHandoff:
    """The dual of the broadcast(handoff_targets=...) path that the
    ``_handoff`` shadowing bug (commit fb9e200) corrupted. We exercise
    every mode plus the wake/timeout paths."""

    def _setup_handoff(self, engine):
        a = engine.generate_agent_id()
        engine.register_agent(a)
        b = engine.generate_agent_id()
        engine.register_agent(b)
        h = _handoffs.record_handoff(engine._connect, a, [b])
        return a, b, h["handoff_id"]

    def test_status_mode_returns_record(self, engine):
        _a, _b, hid = self._setup_handoff(engine)

        result = engine.wait_for_handoff(hid, timeout_s=0.0, mode="status")

        assert result["id"] == hid
        assert result["status"] == "pending"
        assert isinstance(result["to_agents"], list)

    def test_status_mode_unknown_handoff(self, engine):
        result = engine.wait_for_handoff(99999, timeout_s=0.0, mode="status")

        assert "error" in result

    def test_ack_mode_requires_agent_id(self, engine):
        _a, _b, hid = self._setup_handoff(engine)

        result = engine.wait_for_handoff(hid, timeout_s=0.0, mode="ack")

        assert "error" in result
        assert "agent_id" in result["error"]

    def test_ack_mode_succeeds_for_recipient(self, engine):
        _a, b, hid = self._setup_handoff(engine)

        result = engine.wait_for_handoff(hid, timeout_s=0.0, agent_id=b, mode="ack")

        assert result["acknowledged"] is True

    def test_ack_mode_rejected_for_non_recipient(self, engine):
        _a, _b, hid = self._setup_handoff(engine)
        outsider = engine.generate_agent_id()
        engine.register_agent(outsider)

        result = engine.wait_for_handoff(
            hid, timeout_s=0.0, agent_id=outsider, mode="ack",
        )

        assert result["acknowledged"] is False
        assert result["reason"] == "not_recipient"

    def test_complete_mode_marks_completed(self, engine):
        _a, b, hid = self._setup_handoff(engine)
        engine.wait_for_handoff(hid, timeout_s=0.0, agent_id=b, mode="ack")

        result = engine.wait_for_handoff(hid, timeout_s=0.0, mode="complete")

        assert result["completed"] is True

    def test_cancel_mode_marks_cancelled(self, engine):
        _a, _b, hid = self._setup_handoff(engine)

        result = engine.wait_for_handoff(hid, timeout_s=0.0, mode="cancel")

        assert result["cancelled"] is True

    def test_completion_mode_fastpath_on_completed(self, engine):
        _a, b, hid = self._setup_handoff(engine)
        engine.wait_for_handoff(hid, timeout_s=0.0, agent_id=b, mode="ack")
        engine.wait_for_handoff(hid, timeout_s=0.0, mode="complete")

        start = time.time()
        result = engine.wait_for_handoff(hid, timeout_s=5.0, mode="completion")
        elapsed = time.time() - start

        assert result["timed_out"] is False
        assert elapsed < 1.0

    def test_completion_mode_wakes_on_complete_event(self, engine):
        _a, b, hid = self._setup_handoff(engine)
        engine.wait_for_handoff(hid, timeout_s=0.0, agent_id=b, mode="ack")

        result_box: dict = {}

        def _waiter() -> None:
            result_box["r"] = engine.wait_for_handoff(
                hid, timeout_s=5.0, mode="completion",
            )

        t = threading.Thread(target=_waiter)
        t.start()
        time.sleep(0.1)
        engine.wait_for_handoff(hid, timeout_s=0.0, mode="complete")
        t.join(timeout=5.0)

        assert "r" in result_box
        assert result_box["r"]["timed_out"] is False

    def test_completion_mode_times_out(self, engine):
        _a, _b, hid = self._setup_handoff(engine)

        start = time.time()
        result = engine.wait_for_handoff(hid, timeout_s=0.2, mode="completion")
        elapsed = time.time() - start

        assert result["timed_out"] is True
        assert 0.15 < elapsed < 1.5

    def test_unknown_mode_returns_error(self, engine):
        _a, _b, hid = self._setup_handoff(engine)

        result = engine.wait_for_handoff(hid, timeout_s=0.0, mode="bogus")

        assert "error" in result


# --------------------------------------------------------------------- #
# Second-pass coverage: tools that the substring meta-test counted as
# "covered" but whose only references were docstring mentions or
# narrow validation-only paths. Discovered by manual audit per the
# follow-up to findings/post_opus_review_5_followups/01.
# --------------------------------------------------------------------- #


class TestAwaitSubagentRegistration:
    """``await_subagent_registration`` had ZERO real test coverage before
    this class — its only mention in ``tests/`` was a docstring word in
    ``test_authz.py``. The substring meta-test passed it as covered."""

    def test_already_registered_returns_immediately(self, engine):
        parent = engine.generate_agent_id()
        engine.register_agent(parent)
        engine.spawn_subagent(parent, "Explore", description="t")
        child = engine.generate_agent_id(parent)
        engine.register_agent(child, parent)
        engine.report_subagent_spawned(parent, "Explore", child)

        start = time.time()
        result = engine.await_subagent_registration(parent, "Explore", timeout=5.0)
        elapsed = time.time() - start

        assert result["registered"] is True
        assert result["spawn"]["status"] == "registered"
        assert elapsed < 1.0, "should fast-path on already-registered spawn"

    def test_wakes_on_report_subagent_spawned(self, engine):
        parent = engine.generate_agent_id()
        engine.register_agent(parent)
        engine.spawn_subagent(parent, "Plan", description="t")

        result_box: dict = {}

        def _waiter() -> None:
            result_box["r"] = engine.await_subagent_registration(
                parent, "Plan", timeout=5.0,
            )

        t = threading.Thread(target=_waiter)
        t.start()
        time.sleep(0.1)  # let waiter park on event bus
        child = engine.generate_agent_id(parent)
        engine.register_agent(child, parent)
        engine.report_subagent_spawned(parent, "Plan", child)
        t.join(timeout=5.0)

        assert "r" in result_box
        assert result_box["r"]["registered"] is True

    def test_subagent_type_filter_ignores_other_types(self, engine):
        """Wait for ``Plan`` type; an ``Explore`` registration should NOT wake it."""
        parent = engine.generate_agent_id()
        engine.register_agent(parent)
        engine.spawn_subagent(parent, "Plan", description="p")
        engine.spawn_subagent(parent, "Explore", description="e")

        result_box: dict = {}

        def _waiter() -> None:
            result_box["r"] = engine.await_subagent_registration(
                parent, "Plan", timeout=0.4,
            )

        t = threading.Thread(target=_waiter)
        t.start()
        time.sleep(0.1)
        # Register Explore — must not wake the Plan waiter.
        explore_child = engine.generate_agent_id(parent)
        engine.register_agent(explore_child, parent)
        engine.report_subagent_spawned(parent, "Explore", explore_child)
        t.join(timeout=2.0)

        assert "r" in result_box
        assert result_box["r"].get("timed_out") is True

    def test_times_out_when_no_registration(self, engine):
        parent = engine.generate_agent_id()
        engine.register_agent(parent)
        engine.spawn_subagent(parent, "Explore", description="t")

        start = time.time()
        result = engine.await_subagent_registration(parent, "Explore", timeout=0.2)
        elapsed = time.time() - start

        assert result.get("timed_out") is True
        assert result["parent_agent_id"] == parent
        assert 0.15 < elapsed < 1.5


class TestManageDependenciesAllModes:
    """Pre-audit, only ``mode='declare'`` was exercised (test_tasks.py:469).
    Every other mode (``check``/``blockers``/``assert``/``satisfy``/``list``/``wait``)
    had no test."""

    def _two_agents_with_dep(self, engine):
        a = engine.generate_agent_id()
        engine.register_agent(a)
        b = engine.generate_agent_id()
        engine.register_agent(b)
        result = engine.manage_dependencies(
            mode="declare",
            dependent_agent_id=a,
            depends_on_agent_id=b,
            condition="agent_stopped",
        )
        return a, b, result["dep_id"]

    def test_check_mode_reports_blocked(self, engine):
        a, _b, _dep = self._two_agents_with_dep(engine)
        result = engine.manage_dependencies(mode="check", agent_id=a)

        assert result["agent_id"] == a
        assert result["blocked"] is True
        assert len(result["unsatisfied"]) == 1

    def test_check_mode_requires_agent_id(self, engine):
        result = engine.manage_dependencies(mode="check")
        assert "error" in result
        assert "agent_id" in result["error"]

    def test_assert_mode_blocks_until_satisfied(self, engine):
        a, _b, dep = self._two_agents_with_dep(engine)

        before = engine.manage_dependencies(mode="assert", agent_id=a)
        assert before["can_start"] is False
        assert len(before["blockers"]) == 1

        engine.manage_dependencies(mode="satisfy", dep_id=dep)

        after = engine.manage_dependencies(mode="assert", agent_id=a)
        assert after["can_start"] is True

    def test_satisfy_requires_dep_id(self, engine):
        result = engine.manage_dependencies(mode="satisfy")
        assert "error" in result
        assert "dep_id" in result["error"]

    def test_list_mode_returns_all_dependencies(self, engine):
        a, _b, _dep = self._two_agents_with_dep(engine)

        result = engine.manage_dependencies(mode="list", agent_id=a)

        assert result["count"] == 1
        assert result["dependencies"][0]["dependent_agent_id"] == a

    def test_declare_requires_both_agent_ids(self, engine):
        result = engine.manage_dependencies(
            mode="declare", dependent_agent_id="hub.x.0",
        )
        assert "error" in result
        assert "depends_on_agent_id" in result["error"]

    def test_unknown_mode_returns_error(self, engine):
        result = engine.manage_dependencies(mode="bogus", agent_id="hub.x.0")
        assert "error" in result
        assert "Unknown mode" in result["error"]


class TestManageWorkIntentsBehaviour:
    """Pre-audit, ``manage_work_intents`` had only validation-shape tests
    (test_validation.py). The actual ``declare → get → clear`` lifecycle
    + multi-file isolation (T1.16) were unverified at the dispatch layer."""

    def test_declare_then_get_returns_intent(self, engine, registered_agent):
        engine.manage_work_intents(
            action="declare",
            agent_id=registered_agent,
            document_path="src/a.py",
            intent="writing",
            ttl=60.0,
        )

        result = engine.manage_work_intents(action="get", agent_id=registered_agent)

        assert result["count"] == 1
        assert result["intents"][0]["intent"] == "writing"

    def test_clear_specific_path_leaves_others(self, engine, registered_agent):
        engine.manage_work_intents(
            action="declare", agent_id=registered_agent,
            document_path="src/a.py", intent="writing",
        )
        engine.manage_work_intents(
            action="declare", agent_id=registered_agent,
            document_path="src/b.py", intent="reading",
        )

        engine.manage_work_intents(
            action="clear", agent_id=registered_agent,
            document_path="src/a.py",
        )

        remaining = engine.manage_work_intents(action="get", agent_id=registered_agent)
        assert remaining["count"] == 1
        assert remaining["intents"][0]["intent"] == "reading"

    def test_clear_without_path_clears_all(self, engine, registered_agent):
        engine.manage_work_intents(
            action="declare", agent_id=registered_agent,
            document_path="src/a.py", intent="writing",
        )
        engine.manage_work_intents(
            action="declare", agent_id=registered_agent,
            document_path="src/b.py", intent="reading",
        )

        engine.manage_work_intents(action="clear", agent_id=registered_agent)

        result = engine.manage_work_intents(action="get", agent_id=registered_agent)
        assert result["count"] == 0

    def test_declare_missing_required_fields_returns_error(self, engine, registered_agent):
        result = engine.manage_work_intents(
            action="declare", agent_id=registered_agent,
        )
        assert "error" in result
        assert "document_path" in result["error"]

    def test_unknown_action_returns_error(self, engine, registered_agent):
        result = engine.manage_work_intents(action="bogus", agent_id=registered_agent)
        assert "error" in result
        assert "Unknown action" in result["error"]


class TestGetContentionHotspotsEmpty:
    """Pre-audit, only the populated path was exercised (test_conflicts.py).
    The empty-state response shape was not pinned."""

    def test_empty_returns_empty_list_not_error(self, engine):
        result = engine.get_contention_hotspots()

        assert isinstance(result, dict)
        # Engine returns a dict containing a list. Keys vary; the
        # invariant is "no exception, list-shaped result".
        list_field = next(
            (v for v in result.values() if isinstance(v, list)), None,
        )
        assert list_field is not None, f"expected a list in {result!r}"
        assert list_field == []


# --------------------------------------------------------------------- #
# Regression guard: every dispatch entry must be referenced in tests/
# --------------------------------------------------------------------- #


import re as _re


def _gather_test_callsites() -> str:
    """Read every .py under tests/ and return code with docstrings AND
    triple-quoted strings stripped, so the meta-test sees only callable
    code.

    The first version of this helper used a naive substring search
    against the raw file text. That passed ``await_subagent_registration``
    as "covered" because the only mention was a docstring word in
    ``test_authz.py``. The strengthened version below removes triple-
    quoted regions before scanning so that a docstring mention no
    longer satisfies the regression guard.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    triple = _re.compile(r'(?s)"""(?:.|\n)*?"""|\'\'\'(?:.|\n)*?\'\'\'')
    parts = []
    for fn in sorted(os.listdir(here)):
        if not fn.endswith(".py"):
            continue
        with open(os.path.join(here, fn), encoding="utf-8") as fh:
            text = fh.read()
        # Drop docstrings + multi-line strings so prose mentions don't count.
        parts.append(triple.sub("", text))
    return "\n".join(parts)


def _tool_is_invoked(tool: str, code: str) -> bool:
    """Return True if ``tool`` appears in a position consistent with
    actually being called from the test:

    1. ``engine.<tool>(``                — direct engine invocation
    2. ``dispatch_tool(..., "<tool>"...`` — MCP dispatch invocation
    3. ``"<tool>"`` followed by ``,`` or ``:`` (TOOL_SCHEMAS keys, etc.)

    The substring fallback (mention anywhere in code) still counts —
    it's the floor, not the ceiling. The point of the structured checks
    is to surface tools whose only mention is a stale comment / module
    docstring, which is what the original meta-test missed.
    """
    if f"engine.{tool}(" in code:
        return True
    if f'dispatch_tool(engine, "{tool}"' in code:
        return True
    if f'"{tool}"' in code or f"'{tool}'" in code:
        return True
    return False


def test_every_dispatch_tool_has_test_coverage():
    """Every entry in ``TOOL_DISPATCH`` must be invoked from at least
    one test file under ``tests/``.

    Strengthened from the original substring meta-test: the post-audit
    pass (findings/post_opus_review_5_followups/01 follow-up) found that
    ``await_subagent_registration`` had only a docstring mention and
    no actual call. The substring check passed it as covered. The
    current version strips docstrings before scanning and prefers
    structured ``engine.<tool>(`` / ``dispatch_tool(... "<tool>" ...)``
    matches.

    If you intentionally remove a test that covered tool X, add a
    replacement before deleting the old one.
    """
    code = _gather_test_callsites()
    uncovered = sorted(
        tool for tool in TOOL_DISPATCH if not _tool_is_invoked(tool, code)
    )
    assert not uncovered, (
        "Dispatch tools without call-site references in tests/: "
        + ", ".join(uncovered)
        + ". Add at least one test per tool — see "
        "findings/post_opus_review_5_followups/01_test_coverage_gaps for "
        "the rationale and the _handoff regression that motivated this guard."
    )
