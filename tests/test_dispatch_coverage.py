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
# Regression guard: every dispatch entry must be referenced in tests/
# --------------------------------------------------------------------- #


def _gather_test_text() -> str:
    """Read every .py under tests/ and return concatenated text.

    Uses raw substring search rather than AST parsing because a dispatch
    name appearing in a docstring or comment still demonstrates intent;
    the actual bug shape we're guarding against is "no test file even
    mentions this tool by name."
    """
    here = os.path.dirname(os.path.abspath(__file__))
    parts = []
    for fn in sorted(os.listdir(here)):
        if not fn.endswith(".py"):
            continue
        with open(os.path.join(here, fn), encoding="utf-8") as fh:
            parts.append(fh.read())
    return "\n".join(parts)


def test_every_dispatch_tool_has_test_coverage():
    """Every entry in ``TOOL_DISPATCH`` must be referenced by at least
    one test file under ``tests/``.

    Substring search — not a real coverage measurement — but catches the
    case where a dispatch entry has zero test mentions at all. The
    ``_handoff`` shadowing bug (commit ``fb9e200``) sat unnoticed for 6
    commits because ``broadcast(handoff_targets=...)`` had zero
    coverage. Don't let it regress.

    If you intentionally remove a test that covered tool X, add a
    replacement before deleting the old one.
    """
    text = _gather_test_text()
    uncovered = sorted(
        tool for tool in TOOL_DISPATCH if tool not in text
    )
    assert not uncovered, (
        "Dispatch tools with zero references in tests/: "
        + ", ".join(uncovered)
        + ". Add at least one test per tool — see "
        "findings/post_opus_review_5_followups/01_test_coverage_gaps for "
        "the rationale and the _handoff regression that motivated this guard."
    )
