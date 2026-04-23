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
                    {"type": "command", "command": "python3 -m coordinationhub.hooks.stdio_adapter", "timeout": 10}
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


class TestMergeHooksAntiAccumulation:
    """T2.7: repeated init runs must not accumulate stale coordinationhub
    matcher blocks. Pre-fix a drift in the matcher string (e.g. adding a
    new event, renaming a matcher) left the old block behind.
    """

    def test_stale_hub_matcher_block_is_replaced(self):
        existing = {
            "PreToolUse": [
                {
                    "matcher": "Write",  # old, narrower matcher
                    "hooks": [{
                        "type": "command",
                        "command": "python3 -m coordinationhub.hooks.stdio_adapter",
                        "timeout": 5,
                    }],
                },
            ],
        }
        new = _fill_hook_command(_HOOKS_CONFIG, "/usr/bin/python3")
        merged = _merge_hooks(existing, new)
        pre_tool = merged["PreToolUse"]
        matchers = sorted(m["matcher"] for m in pre_tool)
        # Old "Write" block (containing a coordinationhub command) is gone;
        # only the fresh Agent / Write|Edit blocks remain.
        assert "Write" not in matchers
        assert "Write|Edit" in matchers
        assert "Agent" in matchers

    def test_accumulated_duplicate_blocks_are_collapsed(self):
        """A pre-fix settings file that already had two duplicate
        CoordinationHub matcher blocks in SessionStart (from two
        overlapping init runs) must come out with exactly one.
        """
        existing = {
            "SessionStart": [
                {"matcher": "", "hooks": [{
                    "type": "command",
                    "command": "python3 -m coordinationhub.hooks.stdio_adapter",
                }]},
                {"matcher": "", "hooks": [{
                    "type": "command",
                    "command": "python3 -m coordinationhub.hooks.stdio_adapter",
                }]},
            ],
        }
        new = _fill_hook_command(_HOOKS_CONFIG, "/usr/bin/python3")
        merged = _merge_hooks(existing, new)
        assert len(merged["SessionStart"]) == 1

    def test_preserves_user_matcher_blocks_with_same_matcher_string(self):
        """A user's own hook on the same matcher as ours must not be
        removed — we only drop blocks that reference coordinationhub.
        """
        existing = {
            "SessionStart": [
                {"matcher": "", "hooks": [{
                    "type": "command",
                    "command": "my-thing",
                }]},
            ],
        }
        new = _fill_hook_command(_HOOKS_CONFIG, "/usr/bin/python3")
        merged = _merge_hooks(existing, new)
        # User's block survives; our block is appended separately.
        commands = [
            h["command"]
            for block in merged["SessionStart"]
            for h in block.get("hooks", [])
        ]
        assert "my-thing" in commands
        assert any("coordinationhub" in c for c in commands)


class TestInitSettingsSafety:
    """T2.7: cmd_init backs up settings.json before writing and aborts
    rather than overwriting a file it can't parse.
    """

    def test_corrupt_settings_aborts_without_clobber(self, tmp_path, monkeypatch, capsys):
        from coordinationhub import cli_setup

        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        corrupt = "{this is : NOT json]"
        settings_path.write_text(corrupt)

        monkeypatch.setattr(cli_setup, "_CLAUDE_SETTINGS_PATH", settings_path)
        monkeypatch.setattr(cli_setup, "_NEUTRAL_HOOKS_PATH", tmp_path / ".coordinationhub" / "hooks.json")
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        monkeypatch.setattr(cli_setup, "run_doctor", lambda: [])
        monkeypatch.setattr(cli_setup, "_install_ide_hooks", lambda *a, **k: None)

        from types import SimpleNamespace
        args = SimpleNamespace(
            auto_dashboard=False, monitor_skill=False,
        )
        cli_setup.cmd_init(args)

        # File contents unchanged — we refused to overwrite.
        assert settings_path.read_text() == corrupt
        # A .bak file exists alongside the original.
        backups = list(settings_path.parent.glob("settings.json.bak.*"))
        assert backups, f"expected a backup in {settings_path.parent}"

        captured = capsys.readouterr()
        assert "not valid JSON" in captured.err or "not valid JSON" in captured.out

    def test_backup_created_on_successful_run(self, tmp_path, monkeypatch):
        from coordinationhub import cli_setup

        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text(json.dumps({"hooks": {}}))

        monkeypatch.setattr(cli_setup, "_CLAUDE_SETTINGS_PATH", settings_path)
        monkeypatch.setattr(cli_setup, "_NEUTRAL_HOOKS_PATH", tmp_path / ".coordinationhub" / "hooks.json")
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        monkeypatch.setattr(cli_setup, "run_doctor", lambda: [])
        monkeypatch.setattr(cli_setup, "_install_ide_hooks", lambda *a, **k: None)

        from types import SimpleNamespace
        args = SimpleNamespace(auto_dashboard=False, monitor_skill=False)
        cli_setup.cmd_init(args)

        backups = list(settings_path.parent.glob("settings.json.bak.*"))
        assert backups, "expected a backup file to be created on init"


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
        assert cmd == "/opt/special/python3.12 -m coordinationhub.hooks.stdio_adapter"


class TestAutoStartDashboard:
    """Validate the SessionStart-hook helper that idempotently launches serve-sse."""

    def test_skips_when_port_bound(self):
        """If something is already listening on the target port, return 0 immediately."""
        import socket
        from types import SimpleNamespace
        from coordinationhub.cli_setup import cmd_auto_start_dashboard

        # Bind a real socket so cmd_auto_start_dashboard sees the port as taken.
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            args = SimpleNamespace(host="127.0.0.1", port=port)
            with patch("subprocess.Popen") as popen:
                rc = cmd_auto_start_dashboard(args)
            assert rc == 0
            popen.assert_not_called()
        finally:
            srv.close()

    def test_spawns_serve_sse_when_port_free(self, tmp_path, monkeypatch):
        """When the port is free, cmd_auto_start_dashboard spawns serve-sse detached."""
        from types import SimpleNamespace
        from coordinationhub.cli_setup import cmd_auto_start_dashboard

        # Pin the log directory to a tmp path so the test doesn't touch ~
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        # Use an ephemeral port that we know is unbound.
        # (Bind+release to discover a likely-free port.)
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()

        args = SimpleNamespace(host="127.0.0.1", port=port)
        with patch("subprocess.Popen") as popen:
            rc = cmd_auto_start_dashboard(args)
        assert rc == 0
        popen.assert_called_once()
        # Verify the subprocess invocation uses the right CLI
        argv = popen.call_args[0][0]
        assert "coordinationhub" in argv
        assert "serve-sse" in argv
        assert "--no-browser" in argv
        assert str(port) in argv
        # Verify the log file was opened under the patched home
        assert (tmp_path / ".coordinationhub" / "dashboard.log").exists()


class TestInitOptInFlags:
    """Validate the --auto-dashboard and --monitor-skill flags on `init`."""

    def test_install_auto_dashboard_hook_appends_to_session_start(self, tmp_path, monkeypatch):
        from coordinationhub import cli_setup

        # Pin the settings path under tmp
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text(json.dumps({"hooks": {"SessionStart": [
            {"matcher": "", "hooks": [{"type": "command", "command": "/usr/bin/python3 -m coordinationhub.hooks.stdio_adapter", "timeout": 10}]}
        ]}}, indent=2))
        monkeypatch.setattr(cli_setup, "_CLAUDE_SETTINGS_PATH", settings_path)

        cli_setup._install_auto_dashboard_hook("/usr/bin/python3")

        settings = json.loads(settings_path.read_text())
        ss = settings["hooks"]["SessionStart"]
        # Original hook preserved
        assert any("hooks.stdio_adapter" in h["command"]
                   for block in ss for h in block.get("hooks", []))
        # New hook added
        assert any("auto-start-dashboard" in h["command"]
                   for block in ss for h in block.get("hooks", []))

    def test_install_auto_dashboard_hook_idempotent(self, tmp_path, monkeypatch):
        from coordinationhub import cli_setup

        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text(json.dumps({"hooks": {"SessionStart": []}}))
        monkeypatch.setattr(cli_setup, "_CLAUDE_SETTINGS_PATH", settings_path)

        cli_setup._install_auto_dashboard_hook("/usr/bin/python3")
        cli_setup._install_auto_dashboard_hook("/usr/bin/python3")  # second call

        settings = json.loads(settings_path.read_text())
        ss = settings["hooks"]["SessionStart"]
        auto_hooks = [h for block in ss for h in block.get("hooks", [])
                      if "auto-start-dashboard" in h["command"]]
        assert len(auto_hooks) == 1, f"expected 1 auto-dashboard hook, got {len(auto_hooks)}"

    def test_install_auto_dashboard_hook_updates_python_path(self, tmp_path, monkeypatch):
        from coordinationhub import cli_setup

        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text(json.dumps({"hooks": {"SessionStart": []}}))
        monkeypatch.setattr(cli_setup, "_CLAUDE_SETTINGS_PATH", settings_path)

        cli_setup._install_auto_dashboard_hook("/old/python3")
        cli_setup._install_auto_dashboard_hook("/new/python3")

        settings = json.loads(settings_path.read_text())
        cmd = next(h["command"] for block in settings["hooks"]["SessionStart"]
                   for h in block.get("hooks", []) if "auto-start-dashboard" in h["command"])
        assert cmd.startswith("/new/python3 ")

    def test_install_monitor_skill_writes_skill_file(self, tmp_path, monkeypatch):
        from coordinationhub import cli_setup

        # Patch the install target dir under tmp_path so we don't touch real ~
        monkeypatch.setattr(cli_setup, "_SKILL_DIR", tmp_path / "skills" / "coordinationhub-monitor")

        cli_setup._install_monitor_skill()

        skill_path = tmp_path / "skills" / "coordinationhub-monitor" / "SKILL.md"
        assert skill_path.exists(), f"expected SKILL.md at {skill_path}"

        body = skill_path.read_text()
        # Frontmatter present
        assert body.startswith("---\n")
        assert "name: coordinationhub-monitor" in body
        # Description triggers on relevant phrases
        assert "monitor" in body.lower()
        # Read-only role enforced
        assert "Never write" in body or "read-only" in body.lower()
        # Cadence and source-of-truth URL spelled out
        assert "127.0.0.1:9898" in body
