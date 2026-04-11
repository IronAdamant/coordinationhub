"""Tests for hooks/claude_code.py — hook handlers with synthetic event dicts."""

from __future__ import annotations

import json
import os
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch

from coordinationhub.hooks.claude_code import (
    _session_agent_id,
    _subagent_id,
    _resolve_agent_id,
    _log_error,
    handle_session_start,
    handle_user_prompt_submit,
    handle_pre_write,
    handle_post_write,
    handle_subagent_start,
    handle_subagent_stop,
    handle_session_end,
    handle_post_stele_index,
    handle_post_trammel_claim,
)


@pytest.fixture
def hook_cwd(tmp_path):
    """Create a temp project dir with .git so detect_project_root works."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    return str(tmp_path)


def _make_event(hook_event, tool_name="", session_id="sess-12345678", cwd=".", **extra):
    event = {
        "hook_event_name": hook_event,
        "tool_name": tool_name,
        "session_id": session_id,
        "cwd": cwd,
    }
    event.update(extra)
    return event


class TestAgentIdHelpers:
    def test_session_agent_id(self):
        assert _session_agent_id("abcdef123456") == "hub.cc.abcdef123456"

    def test_session_agent_id_short(self):
        assert _session_agent_id("abc") == "hub.cc.abc"

    def test_session_agent_id_empty(self):
        assert _session_agent_id("") == "hub.cc.unknown"

    def test_subagent_id_with_tool_use_id(self):
        event = {"tool_input": {"subagent_type": "explorer"}, "tool_use_id": "toolu_abc123xyz"}
        result = _subagent_id("hub.cc.parent", event)
        assert result == "hub.cc.parent.explorer.toolu_"

    def test_subagent_id_without_tool_use_id(self):
        event = {"tool_input": {"subagent_type": "coder"}, "tool_use_id": ""}
        result = _subagent_id("hub.cc.parent", event)
        assert result == "hub.cc.parent.coder.0"

    def test_subagent_id_default_type(self):
        event = {"tool_input": {}, "tool_use_id": "toolu_xyz789abc"}
        result = _subagent_id("hub.cc.parent", event)
        assert result == "hub.cc.parent.agent.toolu_"

    def test_resolve_agent_id_prefers_subagent(self):
        event = {"subagent_id": "sub.1", "session_id": "sess123"}
        assert _resolve_agent_id(event) == "sub.1"

    def test_resolve_agent_id_falls_back_to_session(self):
        event = {"session_id": "sess12345678"}
        assert _resolve_agent_id(event) == "hub.cc.sess12345678"


class TestResolveAgentIdMapping:
    """Tests for _resolve_agent_id mapping raw Claude Code IDs to hub.cc.* IDs."""

    def test_maps_raw_claude_id_to_hub_child(self, hook_cwd):
        """SubagentStart registers with claude_agent_id; PreToolUse resolves it."""
        session_id = "sess-12345678"
        raw_claude_id = "ac70a34bf2d2264d4"

        # Step 1: SessionStart registers root agent
        handle_session_start(_make_event("SessionStart", cwd=hook_cwd, session_id=session_id))

        # Step 2: SubagentStart registers child with raw Claude ID mapping
        event_start = _make_event(
            "SubagentStart", cwd=hook_cwd, session_id=session_id,
            tool_input={"subagent_type": "agent", "description": "test task"},
            tool_use_id="toolu_abc123xyz",
            subagent_id=raw_claude_id,
        )
        handle_subagent_start(event_start)

        # Step 3: PreToolUse with the raw Claude ID should resolve to hub.cc.* child
        from coordinationhub.hooks.claude_code import _get_engine
        engine = _get_engine(hook_cwd)
        try:
            event_write = {"subagent_id": raw_claude_id, "session_id": session_id}
            resolved = _resolve_agent_id(event_write, engine=engine)
            assert resolved.startswith("hub.cc."), f"Expected hub.cc.* ID, got {resolved}"
            assert resolved != raw_claude_id
        finally:
            engine.close()

    def test_subagent_lock_uses_hub_id(self, hook_cwd):
        """Sub-agent PreToolUse Write acquires lock under hub.cc.* ID, not raw hex."""
        session_id = "sess-12345678"
        raw_claude_id = "af2d34ada2a39871c"

        handle_session_start(_make_event("SessionStart", cwd=hook_cwd, session_id=session_id))
        handle_subagent_start(_make_event(
            "SubagentStart", cwd=hook_cwd, session_id=session_id,
            tool_input={"subagent_type": "coder"},
            tool_use_id="toolu_xyz789abc",
            subagent_id=raw_claude_id,
        ))

        # PreToolUse Write from the sub-agent using raw Claude ID
        result = handle_pre_write(_make_event(
            "PreToolUse", tool_name="Write", cwd=hook_cwd, session_id=session_id,
            tool_input={"file_path": "/tmp/test_subagent_lock.py"},
            subagent_id=raw_claude_id,
        ))
        assert result is not None
        assert result["hookSpecificOutput"]["permissionDecision"] == "allow"

    def test_no_ghost_agents(self, hook_cwd):
        """After SubagentStart + PreToolUse, only ONE agent entry for the subagent."""
        session_id = "sess-12345678"
        raw_claude_id = "a60112d5f3898cadc"

        handle_session_start(_make_event("SessionStart", cwd=hook_cwd, session_id=session_id))
        handle_subagent_start(_make_event(
            "SubagentStart", cwd=hook_cwd, session_id=session_id,
            tool_input={"subagent_type": "explorer"},
            tool_use_id="toolu_def456ghi",
            subagent_id=raw_claude_id,
        ))
        handle_pre_write(_make_event(
            "PreToolUse", tool_name="Write", cwd=hook_cwd, session_id=session_id,
            tool_input={"file_path": "/tmp/test_no_ghost.py"},
            subagent_id=raw_claude_id,
        ))

        # Check: no agent registered with the raw Claude ID as agent_id
        from coordinationhub.hooks.claude_code import _get_engine
        engine = _get_engine(hook_cwd)
        try:
            agents = engine.list_agents(active_only=False)
            agent_ids = [a["agent_id"] for a in agents["agents"]]
            assert raw_claude_id not in agent_ids, \
                f"Ghost agent {raw_claude_id} should not exist"
        finally:
            engine.close()

    def test_post_write_uses_hub_id(self, hook_cwd):
        """PostToolUse change notifications use hub.cc.* ID, not raw hex."""
        session_id = "sess-12345678"
        raw_claude_id = "b1234567890abcdef"

        handle_session_start(_make_event("SessionStart", cwd=hook_cwd, session_id=session_id))
        handle_subagent_start(_make_event(
            "SubagentStart", cwd=hook_cwd, session_id=session_id,
            tool_input={"subagent_type": "writer"},
            tool_use_id="toolu_post123abc",
            subagent_id=raw_claude_id,
        ))
        handle_post_write(_make_event(
            "PostToolUse", tool_name="Write", cwd=hook_cwd, session_id=session_id,
            tool_input={"file_path": "/tmp/test_post_write_id.py"},
            subagent_id=raw_claude_id,
        ))

        # Verify notification was recorded under hub.cc.* ID
        from coordinationhub.hooks.claude_code import _get_engine
        engine = _get_engine(hook_cwd)
        try:
            notifs = engine.get_notifications()
            matching = [n for n in notifs.get("notifications", [])
                        if n["agent_id"] == raw_claude_id]
            assert len(matching) == 0, \
                f"Notification should NOT use raw ID {raw_claude_id}"
        finally:
            engine.close()

    def test_unmapped_raw_id_falls_back(self):
        """Without engine, _resolve_agent_id returns raw ID (backward compat)."""
        event = {"subagent_id": "ac70a34bf2d2264d4", "session_id": "sess123"}
        assert _resolve_agent_id(event) == "ac70a34bf2d2264d4"


class TestSessionStart:
    def test_registers_root_agent(self, hook_cwd):
        event = _make_event("SessionStart", cwd=hook_cwd)
        result = handle_session_start(event)
        assert result is None  # no output on success

    def test_idempotent(self, hook_cwd):
        event = _make_event("SessionStart", cwd=hook_cwd)
        handle_session_start(event)
        handle_session_start(event)  # should not raise


class TestUserPromptSubmit:
    """UserPromptSubmit stamps the root agent's current_task with the prompt."""

    def _current_task(self, hook_cwd, agent_id):
        from coordinationhub.hooks.claude_code import _get_engine
        engine = _get_engine(hook_cwd)
        try:
            with engine._connect() as conn:
                row = conn.execute(
                    "SELECT current_task FROM agent_responsibilities WHERE agent_id = ?",
                    (agent_id,),
                ).fetchone()
                return row["current_task"] if row else None
        finally:
            engine.close()

    def test_sets_current_task_from_prompt(self, hook_cwd):
        session_id = "sess-prompt00001"
        handle_session_start(_make_event("SessionStart", cwd=hook_cwd, session_id=session_id))

        prompt = "Fix the login bug in auth.py"
        handle_user_prompt_submit(_make_event(
            "UserPromptSubmit", cwd=hook_cwd, session_id=session_id, prompt=prompt,
        ))

        task = self._current_task(hook_cwd, _session_agent_id(session_id))
        assert task == prompt

    def test_truncates_long_prompts(self, hook_cwd):
        session_id = "sess-prompt00002"
        handle_session_start(_make_event("SessionStart", cwd=hook_cwd, session_id=session_id))

        long_prompt = "x" * 500
        handle_user_prompt_submit(_make_event(
            "UserPromptSubmit", cwd=hook_cwd, session_id=session_id, prompt=long_prompt,
        ))

        task = self._current_task(hook_cwd, _session_agent_id(session_id))
        assert len(task) <= 120
        assert task.endswith("...")

    def test_collapses_multiline_whitespace(self, hook_cwd):
        """Multi-line prompts render as a single line in the agent tree."""
        session_id = "sess-prompt00003"
        handle_session_start(_make_event("SessionStart", cwd=hook_cwd, session_id=session_id))

        handle_user_prompt_submit(_make_event(
            "UserPromptSubmit", cwd=hook_cwd, session_id=session_id,
            prompt="line one\n\n  line two\n\nline three",
        ))

        task = self._current_task(hook_cwd, _session_agent_id(session_id))
        assert "\n" not in task
        assert task == "line one line two line three"

    def test_empty_prompt_is_noop(self, hook_cwd):
        """Empty/whitespace prompts leave current_task unchanged."""
        session_id = "sess-prompt00004"
        handle_session_start(_make_event("SessionStart", cwd=hook_cwd, session_id=session_id))

        # Seed a known task first
        handle_user_prompt_submit(_make_event(
            "UserPromptSubmit", cwd=hook_cwd, session_id=session_id,
            prompt="initial",
        ))
        # Empty prompt should not overwrite
        handle_user_prompt_submit(_make_event(
            "UserPromptSubmit", cwd=hook_cwd, session_id=session_id, prompt="   ",
        ))

        task = self._current_task(hook_cwd, _session_agent_id(session_id))
        assert task == "initial"

    def test_latest_prompt_overwrites_previous(self, hook_cwd):
        """A new prompt replaces the root agent's current_task."""
        session_id = "sess-prompt00005"
        handle_session_start(_make_event("SessionStart", cwd=hook_cwd, session_id=session_id))

        handle_user_prompt_submit(_make_event(
            "UserPromptSubmit", cwd=hook_cwd, session_id=session_id,
            prompt="first thing",
        ))
        handle_user_prompt_submit(_make_event(
            "UserPromptSubmit", cwd=hook_cwd, session_id=session_id,
            prompt="second thing",
        ))

        task = self._current_task(hook_cwd, _session_agent_id(session_id))
        assert task == "second thing"

    def test_registers_root_when_session_start_missed(self, hook_cwd):
        """If UserPromptSubmit fires without a prior SessionStart, the handler
        still registers the root agent rather than silently dropping the task."""
        session_id = "sess-prompt00006"
        # No SessionStart — go straight to UserPromptSubmit
        handle_user_prompt_submit(_make_event(
            "UserPromptSubmit", cwd=hook_cwd, session_id=session_id,
            prompt="ad-hoc prompt",
        ))

        task = self._current_task(hook_cwd, _session_agent_id(session_id))
        assert task == "ad-hoc prompt"


class TestPreWrite:
    def test_acquires_lock(self, hook_cwd):
        handle_session_start(_make_event("SessionStart", cwd=hook_cwd))
        event = _make_event("PreToolUse", tool_name="Write", cwd=hook_cwd,
                           tool_input={"file_path": "/tmp/test_write.py"})
        result = handle_pre_write(event)
        assert result is not None
        output = result["hookSpecificOutput"]
        assert output["permissionDecision"] == "allow"
        assert "Lock acquired" in output["additionalContext"]

    def test_no_file_path_returns_none(self, hook_cwd):
        event = _make_event("PreToolUse", tool_name="Write", cwd=hook_cwd,
                           tool_input={})
        result = handle_pre_write(event)
        assert result is None

    def test_same_agent_reacquires(self, hook_cwd):
        handle_session_start(_make_event("SessionStart", cwd=hook_cwd))
        event = _make_event("PreToolUse", tool_name="Write", cwd=hook_cwd,
                           tool_input={"file_path": "/tmp/test_reacquire.py"})
        handle_pre_write(event)
        result = handle_pre_write(event)
        # Should succeed (same agent refreshes)
        assert result is None or result["hookSpecificOutput"]["permissionDecision"] == "allow"

    def test_different_agent_blocked(self, hook_cwd):
        # Agent 1 acquires
        event1 = _make_event("PreToolUse", tool_name="Write", cwd=hook_cwd,
                            session_id="sess-aaaa11111111",
                            tool_input={"file_path": "/tmp/test_blocked.py"})
        handle_session_start(_make_event("SessionStart", cwd=hook_cwd, session_id="sess-aaaa11111111"))
        handle_pre_write(event1)

        # Agent 2 tries same file
        handle_session_start(_make_event("SessionStart", cwd=hook_cwd, session_id="sess-bbbb22222222"))
        event2 = _make_event("PreToolUse", tool_name="Write", cwd=hook_cwd,
                            session_id="sess-bbbb22222222",
                            tool_input={"file_path": "/tmp/test_blocked.py"})
        result = handle_pre_write(event2)
        assert result is not None
        output = result["hookSpecificOutput"]
        assert output["permissionDecision"] == "deny"
        assert "locked by" in output["permissionDecisionReason"]


class TestPostWrite:
    def test_notifies_change(self, hook_cwd):
        handle_session_start(_make_event("SessionStart", cwd=hook_cwd))
        event = _make_event("PostToolUse", tool_name="Write", cwd=hook_cwd,
                           tool_input={"file_path": "/tmp/test_notify.py"})
        result = handle_post_write(event)
        assert result is None  # no output

    def test_no_file_path_returns_none(self, hook_cwd):
        event = _make_event("PostToolUse", tool_name="Write", cwd=hook_cwd,
                           tool_input={})
        result = handle_post_write(event)
        assert result is None

    def test_post_write_claims_file_ownership(self, hook_cwd):
        """First write to a file populates file_ownership table."""
        handle_session_start(_make_event("SessionStart", cwd=hook_cwd))
        handle_post_write(_make_event(
            "PostToolUse", tool_name="Write", cwd=hook_cwd,
            tool_input={"file_path": "/tmp/test_ownership.py"}))

        from coordinationhub.hooks.claude_code import _get_engine
        engine = _get_engine(hook_cwd)
        try:
            with engine._connect() as conn:
                row = conn.execute(
                    "SELECT assigned_agent_id FROM file_ownership WHERE document_path LIKE '%test_ownership%'"
                ).fetchone()
                assert row is not None, "file_ownership should be populated"
                assert row["assigned_agent_id"].startswith("hub.cc.")
        finally:
            engine.close()

    def test_file_ownership_first_write_wins(self, hook_cwd):
        """Second agent writing same file does not overwrite ownership."""
        session_a = "sess-aaaa11111111"
        session_b = "sess-bbbb22222222"
        handle_session_start(_make_event("SessionStart", cwd=hook_cwd, session_id=session_a))
        handle_session_start(_make_event("SessionStart", cwd=hook_cwd, session_id=session_b))

        # Agent A writes first
        handle_post_write(_make_event(
            "PostToolUse", tool_name="Write", cwd=hook_cwd, session_id=session_a,
            tool_input={"file_path": "/tmp/test_fww.py"}))
        # Agent B writes same file later
        handle_post_write(_make_event(
            "PostToolUse", tool_name="Write", cwd=hook_cwd, session_id=session_b,
            tool_input={"file_path": "/tmp/test_fww.py"}))

        from coordinationhub.hooks.claude_code import _get_engine, _session_agent_id
        engine = _get_engine(hook_cwd)
        try:
            with engine._connect() as conn:
                row = conn.execute(
                    "SELECT assigned_agent_id FROM file_ownership WHERE document_path LIKE '%test_fww%'"
                ).fetchone()
                assert row is not None
                assert row["assigned_agent_id"] == _session_agent_id(session_a)
        finally:
            engine.close()


class TestSubagentLifecycle:
    def test_subagent_start_registers(self, hook_cwd):
        handle_session_start(_make_event("SessionStart", cwd=hook_cwd))
        event = _make_event("SubagentStart", cwd=hook_cwd,
                           tool_input={"subagent_type": "explorer", "description": "Search codebase"},
                           tool_use_id="toolu_abc123xyz")
        result = handle_subagent_start(event)
        assert result is None

    def test_subagent_stop_deregisters(self, hook_cwd):
        handle_session_start(_make_event("SessionStart", cwd=hook_cwd))
        event_start = _make_event("SubagentStart", cwd=hook_cwd,
                                 tool_input={"subagent_type": "explorer"},
                                 tool_use_id="toolu_abc123xyz")
        handle_subagent_start(event_start)
        event_stop = _make_event("SubagentStop", cwd=hook_cwd,
                                tool_input={"subagent_type": "explorer"},
                                tool_use_id="toolu_abc123xyz")
        result = handle_subagent_stop(event_stop)
        assert result is None

    def test_subagent_stop_sets_status_stopped_via_claude_id(self, hook_cwd):
        """SubagentStop with raw claude hex ID resolves to hub.cc.* and sets status='stopped'."""
        session_id = "sess-12345678"
        raw_claude_id = "deadbeef12345abcd"

        handle_session_start(_make_event("SessionStart", cwd=hook_cwd, session_id=session_id))
        handle_subagent_start(_make_event(
            "SubagentStart", cwd=hook_cwd, session_id=session_id,
            tool_input={"subagent_type": "builder", "description": "Build feature"},
            tool_use_id="toolu_stop123abc",
            subagent_id=raw_claude_id,
        ))

        # SubagentStop with only raw claude ID (no tool_use_id) — mirrors production events
        handle_subagent_stop(_make_event(
            "SubagentStop", cwd=hook_cwd, session_id=session_id,
            subagent_id=raw_claude_id,
        ))

        from coordinationhub.hooks.claude_code import _get_engine
        engine = _get_engine(hook_cwd)
        try:
            agents = engine.list_agents(active_only=False)
            child = [a for a in agents["agents"]
                     if a.get("claude_agent_id") == raw_claude_id]
            assert len(child) == 1
            assert child[0]["status"] == "stopped"
        finally:
            engine.close()

    def test_background_agent_dedup(self, hook_cwd):
        """run_in_background agents fire SubagentStart twice — second call should heartbeat, not duplicate."""
        session_id = "sess-12345678"
        raw_claude_id = "ac366dfcabf01578c"

        handle_session_start(_make_event("SessionStart", cwd=hook_cwd, session_id=session_id))

        # First SubagentStart (launch)
        handle_subagent_start(_make_event(
            "SubagentStart", cwd=hook_cwd, session_id=session_id,
            tool_input={"subagent_type": "agent", "description": "NutritionEnricher"},
            subagent_id=raw_claude_id,
        ))

        # Second SubagentStart (background completion notification) — same claude_agent_id
        handle_subagent_start(_make_event(
            "SubagentStart", cwd=hook_cwd, session_id=session_id,
            tool_input={"subagent_type": "agent", "description": "NutritionEnricher"},
            subagent_id=raw_claude_id,
        ))

        from coordinationhub.hooks.claude_code import _get_engine
        engine = _get_engine(hook_cwd)
        try:
            agents = engine.list_agents(active_only=False)
            matches = [a for a in agents["agents"]
                       if a.get("claude_agent_id") == raw_claude_id]
            assert len(matches) == 1, (
                f"Expected 1 agent for {raw_claude_id}, got {len(matches)}: "
                f"{[m['agent_id'] for m in matches]}"
            )
        finally:
            engine.close()


class TestSessionEnd:
    def test_releases_locks_and_deregisters(self, hook_cwd):
        handle_session_start(_make_event("SessionStart", cwd=hook_cwd))
        # Acquire a lock
        event = _make_event("PreToolUse", tool_name="Edit", cwd=hook_cwd,
                           tool_input={"file_path": "/tmp/test_end.py"})
        handle_pre_write(event)
        # End session — now returns summary
        result = handle_session_end(_make_event("SessionEnd", cwd=hook_cwd))
        assert result is not None
        assert "Session summary" in result["hookSpecificOutput"]["additionalContext"]


class TestErrorLogging:
    def test_log_error_creates_log_file(self, tmp_path):
        """_log_error writes to hook.log in the specified directory."""
        with patch("coordinationhub.hooks.claude_code.Path") as mock_path:
            mock_path.home.return_value = tmp_path
            log_dir = tmp_path / ".coordinationhub"
            try:
                exc = RuntimeError("test error")
                _log_error("TestEvent", exc)
                log_file = log_dir / "hook.log"
                assert log_file.exists()
                content = log_file.read_text()
                assert "TestEvent" in content
                assert "test error" in content
            finally:
                pass

    def test_log_error_never_raises(self):
        """_log_error must never raise, even with pathological inputs."""
        # This should not raise
        _log_error("Test", RuntimeError("x"))


class TestSessionSummary:
    def test_session_end_returns_summary(self, hook_cwd):
        """SessionEnd should return a summary with counts."""
        handle_session_start(_make_event("SessionStart", cwd=hook_cwd))
        # Make some activity
        handle_pre_write(_make_event(
            "PreToolUse", tool_name="Write", cwd=hook_cwd,
            tool_input={"file_path": "/tmp/test_summary.py"}))
        handle_post_write(_make_event(
            "PostToolUse", tool_name="Write", cwd=hook_cwd,
            tool_input={"file_path": "/tmp/test_summary.py"}))

        result = handle_session_end(_make_event("SessionEnd", cwd=hook_cwd))
        assert result is not None
        ctx = result["hookSpecificOutput"]["additionalContext"]
        assert "Session summary" in ctx
        assert "agents tracked" in ctx
        assert "notifications" in ctx


class TestBridges:
    def test_stele_index_notifies(self, hook_cwd):
        handle_session_start(_make_event("SessionStart", cwd=hook_cwd))
        event = _make_event("PostToolUse", tool_name="mcp__stele-context__index", cwd=hook_cwd,
                           tool_input={"paths": ["/tmp/a.py", "/tmp/b.py"]})
        result = handle_post_stele_index(event)
        assert result is None

    def test_stele_index_single_path(self, hook_cwd):
        handle_session_start(_make_event("SessionStart", cwd=hook_cwd))
        event = _make_event("PostToolUse", tool_name="mcp__stele-context__index", cwd=hook_cwd,
                           tool_input={"document_path": "/tmp/single.py"})
        result = handle_post_stele_index(event)
        assert result is None

    def test_stele_index_no_paths(self, hook_cwd):
        event = _make_event("PostToolUse", tool_name="mcp__stele-context__index", cwd=hook_cwd,
                           tool_input={})
        result = handle_post_stele_index(event)
        assert result is None

    def test_trammel_claim_updates_status(self, hook_cwd):
        handle_session_start(_make_event("SessionStart", cwd=hook_cwd))
        event = _make_event("PostToolUse", tool_name="mcp__trammel__claim_step", cwd=hook_cwd,
                           tool_input={"step_id": "step_1", "plan_id": "plan_abc"})
        result = handle_post_trammel_claim(event)
        assert result is None


# ---------------------------------------------------------------------------
# Contract tests — validate handlers against fixture event shapes
# ---------------------------------------------------------------------------

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "claude_code_events"

# Map fixture filename stem → (handler, needs_session_first, required_fields)
#
# ``required_fields`` documents the minimum event shape each handler reads.
# Each entry is a dot-path into the event dict (e.g. "tool_input.file_path"
# means ``event["tool_input"]["file_path"]`` must exist and be non-empty).
# These assertions catch schema drift if Claude Code renames or removes
# fields the handlers depend on.
_FIXTURE_HANDLERS = {
    "SessionStart": (
        handle_session_start, False,
        ["hook_event_name", "session_id"],
    ),
    "UserPromptSubmit": (
        handle_user_prompt_submit, True,
        ["hook_event_name", "session_id", "prompt"],
    ),
    "PreToolUse_Write": (
        handle_pre_write, True,
        ["hook_event_name", "session_id", "tool_name", "tool_input.file_path"],
    ),
    "PostToolUse_Write": (
        handle_post_write, True,
        ["hook_event_name", "session_id", "tool_name", "tool_input.file_path"],
    ),
    "SubagentStart": (
        handle_subagent_start, True,
        ["hook_event_name", "session_id", "subagent_id", "tool_input.subagent_type"],
    ),
    "SubagentStop": (
        handle_subagent_stop, True,
        ["hook_event_name", "session_id", "subagent_id"],
    ),
    "SessionEnd": (
        handle_session_end, True,
        ["hook_event_name", "session_id"],
    ),
}


def _get_dotted(d: dict, path: str):
    """Walk a dotted path through a nested dict.  Returns None if missing."""
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


class TestEventContract:
    """Validate hook handlers accept the documented event shape without errors.

    Fixtures in tests/fixtures/claude_code_events/ represent the minimum
    contract CoordinationHub depends on from Claude Code.  Replace these
    with real captured events (COORDINATIONHUB_CAPTURE_EVENTS=1) to
    catch schema drift.
    """

    @pytest.fixture(params=list(_FIXTURE_HANDLERS.keys()))
    def event_and_handler(self, request, hook_cwd):
        fixture_path = _FIXTURE_DIR / f"{request.param}.json"
        if not fixture_path.exists():
            pytest.skip(f"No fixture for {request.param}")
        event = json.loads(fixture_path.read_text())
        event["cwd"] = hook_cwd  # redirect to test dir
        handler, needs_session, required = _FIXTURE_HANDLERS[request.param]
        if needs_session:
            handle_session_start({"hook_event_name": "SessionStart",
                                  "session_id": event.get("session_id", ""),
                                  "cwd": hook_cwd})
        return event, handler, required

    def test_required_fields_present(self, event_and_handler):
        """Every field the handler reads must be present in the fixture."""
        event, _, required = event_and_handler
        for field_path in required:
            value = _get_dotted(event, field_path)
            assert value not in (None, ""), (
                f"Required field {field_path!r} missing or empty in fixture"
            )

    def test_handler_does_not_crash(self, event_and_handler):
        """Handler processes the fixture event without raising."""
        event, handler, _ = event_and_handler
        handler(event)  # should not raise

    def test_subagent_id_is_hex_string(self):
        """SubagentStart fixture's subagent_id must match Claude Code's hex format."""
        fixture = _FIXTURE_DIR / "SubagentStart.json"
        if not fixture.exists():
            pytest.skip("No fixture")
        event = json.loads(fixture.read_text())
        subagent_id = event.get("subagent_id", "")
        assert len(subagent_id) >= 16, "Claude Code subagent IDs are long hex strings"
        assert all(c in "0123456789abcdef" for c in subagent_id), (
            f"Expected hex characters in subagent_id, got {subagent_id!r}"
        )


class TestEventCapture:
    """Tests for the COORDINATIONHUB_CAPTURE_EVENTS=1 snapshot mechanism."""

    def test_save_event_snapshot_writes_file(self, tmp_path, monkeypatch):
        """_save_event_snapshot writes a JSON file under ~/.coordinationhub/event_snapshots/."""
        monkeypatch.setenv("HOME", str(tmp_path))
        # The function uses Path.home() which reads HOME
        from coordinationhub.hooks.claude_code import _save_event_snapshot
        event = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "session_id": "sess-test",
            "tool_input": {"file_path": "/tmp/test.py"},
        }
        _save_event_snapshot(event)
        snap_dir = tmp_path / ".coordinationhub" / "event_snapshots"
        assert snap_dir.exists()
        files = list(snap_dir.glob("PreToolUse_Write_*.json"))
        assert len(files) == 1
        captured = json.loads(files[0].read_text())
        assert captured["hook_event_name"] == "PreToolUse"
        assert captured["tool_input"]["file_path"] == "/tmp/test.py"

    def test_save_event_snapshot_never_raises(self, monkeypatch):
        """Capture failure must never crash the hook."""
        from coordinationhub.hooks.claude_code import _save_event_snapshot
        # Point HOME at a non-writable location — should silently no-op
        monkeypatch.setenv("HOME", "/nonexistent/readonly/path")
        _save_event_snapshot({"hook_event_name": "Test"})  # should not raise
