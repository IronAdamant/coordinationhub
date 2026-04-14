"""Tests for hooks/base.py — BaseHook abstraction."""

from __future__ import annotations

import pytest

from coordinationhub.hooks.base import BaseHook
from coordinationhub.hooks.claude_code import ClaudeCodeHook
from coordinationhub.hooks.kimi_cli import KimiCliHook
from coordinationhub.hooks.cursor import CursorHook


@pytest.fixture
def hook_cwd(tmp_path):
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    return str(tmp_path)


class TestBaseHookIds:
    def test_session_agent_id(self, hook_cwd):
        hook = BaseHook(project_root=hook_cwd)
        try:
            assert hook.session_agent_id("abcdef123456").startswith("hub.ide.")
        finally:
            hook.close()

    def test_resolve_agent_id_falls_back_to_session(self, hook_cwd):
        hook = BaseHook(project_root=hook_cwd)
        try:
            assert hook.resolve_agent_id("sess12345678").startswith("hub.ide.")
        finally:
            hook.close()

    def test_subagent_id_with_tool_use_id(self, hook_cwd):
        hook = BaseHook(project_root=hook_cwd)
        try:
            sid = hook.subagent_id("hub.ide.parent", "Explore", tool_use_id="toolu_abc123")
            assert sid == "hub.ide.parent.Explore.toolu_"
        finally:
            hook.close()


class TestClaudeCodeHook:
    def test_ide_prefix(self, hook_cwd):
        hook = ClaudeCodeHook(project_root=hook_cwd)
        try:
            assert hook.session_agent_id("abc") == "hub.cc.abc"
        finally:
            hook.close()


class TestKimiCliHook:
    def test_ide_prefix(self, hook_cwd):
        hook = KimiCliHook(project_root=hook_cwd)
        try:
            assert hook.session_agent_id("abc") == "hub.kimi.abc"
        finally:
            hook.close()


class TestCursorHook:
    def test_ide_prefix(self, hook_cwd):
        hook = CursorHook(project_root=hook_cwd)
        try:
            assert hook.session_agent_id("abc") == "hub.cursor.abc"
        finally:
            hook.close()


class TestBaseHookLifecycle:
    def test_session_start_end(self, hook_cwd):
        hook = BaseHook(project_root=hook_cwd)
        try:
            hook.on_session_start("sess-abc")
            result = hook.on_session_end("sess-abc")
            assert result is not None
            assert "SessionEnd" in str(result)
        finally:
            hook.close()

    def test_pre_write_allow_and_release(self, hook_cwd):
        hook = BaseHook(project_root=hook_cwd)
        try:
            hook.on_session_start("sess-abc")
            result = hook.on_pre_write("sess-abc", "/test.py")
            assert result is not None
            assert result["hookSpecificOutput"]["permissionDecision"] == "allow"
            hook.on_post_write("sess-abc", "/test.py")
            status = hook.engine.get_lock_status("/test.py")
            assert status["locked"] is False
        finally:
            hook.close()
