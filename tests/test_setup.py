"""Tests for cli_setup.py — doctor, init, and watch commands."""

from __future__ import annotations

import json
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch

from coordinationhub.cli_setup import (
    run_doctor,
    _merge_hooks,
    _fill_hook_command,
    _HOOKS_CONFIG,
)


class TestDoctor:
    def test_import_check_passes(self):
        results = run_doctor()
        import_check = next(r for r in results if r["name"] == "import")
        assert import_check["ok"] is True

    def test_returns_all_check_names(self):
        results = run_doctor()
        names = {r["name"] for r in results}
        assert names == {"import", "hooks_config", "storage_dir", "schema_version", "hook_python"}


class TestMergeHooks:
    def test_merge_into_empty(self):
        hooks = _fill_hook_command(_HOOKS_CONFIG, "/usr/bin/python3")
        merged = _merge_hooks({}, hooks)
        assert "SessionStart" in merged
        assert "PreToolUse" in merged
        assert "SessionEnd" in merged
        # Verify command was filled
        cmd = merged["SessionStart"][0]["hooks"][0]["command"]
        assert "coordinationhub" in cmd
        assert "/usr/bin/python3" in cmd

    def test_merge_preserves_existing_non_hub_hooks(self):
        existing = {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "my-linter"}]}
            ]
        }
        hooks = _fill_hook_command(_HOOKS_CONFIG, "/usr/bin/python3")
        merged = _merge_hooks(existing, hooks)
        # Original Bash hook preserved
        bash_hooks = [m for m in merged["PreToolUse"] if m["matcher"] == "Bash"]
        assert len(bash_hooks) == 1
        assert bash_hooks[0]["hooks"][0]["command"] == "my-linter"
        # New Write|Edit hook added
        write_hooks = [m for m in merged["PreToolUse"] if m["matcher"] == "Write|Edit"]
        assert len(write_hooks) == 1

    def test_merge_updates_existing_hub_hook(self):
        existing = {
            "SessionStart": [
                {"matcher": "", "hooks": [
                    {"type": "command", "command": "python3 -m coordinationhub.hooks.claude_code", "timeout": 10}
                ]}
            ]
        }
        hooks = _fill_hook_command(_HOOKS_CONFIG, "/home/user/.venv/bin/python3")
        merged = _merge_hooks(existing, hooks)
        # Command should be updated to new python path
        cmd = merged["SessionStart"][0]["hooks"][0]["command"]
        assert "/home/user/.venv/bin/python3" in cmd

    def test_merge_idempotent(self):
        hooks = _fill_hook_command(_HOOKS_CONFIG, "/usr/bin/python3")
        merged1 = _merge_hooks({}, hooks)
        merged2 = _merge_hooks(merged1, hooks)
        # Same structure after merging twice
        assert json.dumps(merged1, sort_keys=True) == json.dumps(merged2, sort_keys=True)


class TestFillHookCommand:
    def test_fills_all_events(self):
        filled = _fill_hook_command(_HOOKS_CONFIG, "/usr/bin/python3")
        for event_name, matchers in filled.items():
            for matcher_block in matchers:
                for hook in matcher_block["hooks"]:
                    assert "coordinationhub" in hook["command"]
                    assert "/usr/bin/python3" in hook["command"]

    def test_uses_absolute_python_path(self):
        filled = _fill_hook_command(_HOOKS_CONFIG, "/opt/special/python3.12")
        cmd = filled["SessionStart"][0]["hooks"][0]["command"]
        assert cmd == "/opt/special/python3.12 -m coordinationhub.hooks.claude_code"
