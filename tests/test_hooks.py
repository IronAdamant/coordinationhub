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
    handle_session_start,
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


class TestSessionEnd:
    def test_releases_locks_and_deregisters(self, hook_cwd):
        handle_session_start(_make_event("SessionStart", cwd=hook_cwd))
        # Acquire a lock
        event = _make_event("PreToolUse", tool_name="Edit", cwd=hook_cwd,
                           tool_input={"file_path": "/tmp/test_end.py"})
        handle_pre_write(event)
        # End session
        result = handle_session_end(_make_event("SessionEnd", cwd=hook_cwd))
        assert result is None


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
