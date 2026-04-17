"""CLI commands for setup and diagnostics: ``init``, ``doctor``, ``watch``.

Diagnostic check functions (and ``cmd_doctor``) live in
:mod:`cli_setup_doctor` so both modules stay under 500 LOC.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .cli_setup_doctor import cmd_doctor, run_doctor

__all__ = ["cmd_doctor", "cmd_init", "cmd_auto_start_dashboard", "cmd_watch", "run_doctor"]


# ------------------------------------------------------------------ #
# Shared constants
# ------------------------------------------------------------------ #

_CLAUDE_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

_HOOK_CMD_TEMPLATE = "{python} -m coordinationhub.hooks.claude_code"
_AUTO_DASHBOARD_CMD_TEMPLATE = "{python} -m coordinationhub auto-start-dashboard"

_SKILL_DIR = Path.home() / ".claude" / "skills" / "coordinationhub-monitor"
_SKILL_TEMPLATE_PATH = Path(__file__).parent / "data" / "monitor_skill.md"

_HOOKS_CONFIG = {
    "SessionStart": [{"matcher": "", "hooks": [{"type": "command", "command": "", "timeout": 10, "statusMessage": "Registering with CoordinationHub"}]}],
    "UserPromptSubmit": [{"matcher": "", "hooks": [{"type": "command", "command": "", "timeout": 5, "statusMessage": "Stamping current task"}]}],
    "PreToolUse": [
        {"matcher": "Write|Edit", "hooks": [{"type": "command", "command": "", "timeout": 5, "statusMessage": "Acquiring file lock"}]},
        {"matcher": "Agent", "hooks": [{"type": "command", "command": "", "timeout": 5, "statusMessage": "Stashing sub-agent task"}]},
    ],
    "PostToolUse": [
        {"matcher": "Write|Edit", "hooks": [{"type": "command", "command": "", "timeout": 5}]},
        {"matcher": "mcp__stele-context__index", "hooks": [{"type": "command", "command": "", "timeout": 5, "statusMessage": "Bridging Stele index to CoordinationHub"}]},
        {"matcher": "mcp__trammel__claim_step", "hooks": [{"type": "command", "command": "", "timeout": 5, "statusMessage": "Syncing Trammel step to CoordinationHub"}]},
    ],
    "SubagentStart": [{"matcher": "", "hooks": [{"type": "command", "command": "", "timeout": 5, "statusMessage": "Registering subagent"}]}],
    "SubagentStop": [{"matcher": "", "hooks": [{"type": "command", "command": "", "timeout": 5}]}],
    "SessionEnd": [{"matcher": "", "hooks": [{"type": "command", "command": "", "timeout": 10, "statusMessage": "Releasing CoordinationHub locks"}]}],
}


def _fill_hook_command(config: dict, python_path: str) -> dict:
    """Deep-copy the hooks config template with the correct python path."""
    cmd = _HOOK_CMD_TEMPLATE.format(python=python_path)
    filled = {}
    for event_name, matchers in config.items():
        filled[event_name] = []
        for matcher_block in matchers:
            new_block = {"matcher": matcher_block["matcher"], "hooks": []}
            for hook in matcher_block["hooks"]:
                new_hook = dict(hook)
                new_hook["command"] = cmd
                new_block["hooks"].append(new_hook)
            filled[event_name].append(new_block)
    return filled


def _merge_hooks(existing: dict, new: dict) -> dict:
    """Merge new CoordinationHub hooks into existing hook config.

    For each event type, checks if a CoordinationHub hook already exists
    (by checking if the command references coordinationhub). If so, updates
    the command. If not, appends the new matcher block.
    """
    merged = dict(existing)
    for event_name, new_matchers in new.items():
        if event_name not in merged:
            merged[event_name] = new_matchers
            continue

        existing_matchers = merged[event_name]
        for new_matcher in new_matchers:
            found = False
            for em in existing_matchers:
                if em.get("matcher") == new_matcher["matcher"]:
                    for hook in em.get("hooks", []):
                        if "coordinationhub" in hook.get("command", ""):
                            hook["command"] = new_matcher["hooks"][0]["command"]
                            found = True
                            break
                if found:
                    break
            if not found:
                existing_matchers.append(new_matcher)

    return merged


# ------------------------------------------------------------------ #
# init
# ------------------------------------------------------------------ #

def cmd_init(args):
    python_path = sys.executable

    from .paths import detect_project_root
    project_root = detect_project_root()
    if project_root is not None:
        storage = project_root / ".coordinationhub"
        storage.mkdir(parents=True, exist_ok=True)
        print(f"Storage directory: {storage}")
    else:
        storage = Path.home() / ".coordinationhub"
        storage.mkdir(parents=True, exist_ok=True)
        print(f"Storage directory: {storage} (no git project detected)")

    from .core import CoordinationEngine
    engine = CoordinationEngine(project_root=project_root)
    engine.start()
    engine.close()
    print("Database initialized.")

    _CLAUDE_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)

    if _CLAUDE_SETTINGS_PATH.exists():
        try:
            settings = json.loads(_CLAUDE_SETTINGS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            settings = {}
    else:
        settings = {}

    hooks_config = _fill_hook_command(_HOOKS_CONFIG, python_path)
    existing_hooks = settings.get("hooks", {})
    merged = _merge_hooks(existing_hooks, hooks_config)
    settings["hooks"] = merged

    _CLAUDE_SETTINGS_PATH.write_text(
        json.dumps(settings, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Hooks written to {_CLAUDE_SETTINGS_PATH}")
    print(f"  Python interpreter: {python_path}")

    _install_ide_hooks(project_root or Path.cwd(), python_path)

    print("\nRunning diagnostics...")
    results = run_doctor()
    all_ok = True
    for r in results:
        icon = "OK" if r["ok"] else "FAIL"
        if not r["ok"]:
            all_ok = False
        print(f"  [{icon:4s}] {r['name']}: {r['message']}")

    if getattr(args, "auto_dashboard", False):
        _install_auto_dashboard_hook(python_path)

    if getattr(args, "monitor_skill", False):
        _install_monitor_skill()

    if all_ok:
        print("\nSetup complete. CoordinationHub is ready.")
    else:
        print("\nSetup complete with warnings. Check the failures above.")


def _install_auto_dashboard_hook(python_path: str) -> None:
    """Append a SessionStart hook that auto-starts the SSE dashboard.

    Idempotent — if the hook already references ``auto-start-dashboard``,
    the command string is updated in place rather than duplicated.
    """
    cmd = _AUTO_DASHBOARD_CMD_TEMPLATE.format(python=python_path)
    if not _CLAUDE_SETTINGS_PATH.exists():
        return
    settings = json.loads(_CLAUDE_SETTINGS_PATH.read_text(encoding="utf-8"))
    hooks = settings.setdefault("hooks", {})
    session_start = hooks.setdefault("SessionStart", [])

    for matcher_block in session_start:
        for hook in matcher_block.get("hooks", []):
            if "auto-start-dashboard" in hook.get("command", ""):
                hook["command"] = cmd
                _CLAUDE_SETTINGS_PATH.write_text(
                    json.dumps(settings, indent=2) + "\n", encoding="utf-8",
                )
                print(f"\nAuto-dashboard hook updated: {cmd}")
                return

    session_start.append({
        "matcher": "",
        "hooks": [{
            "type": "command",
            "command": cmd,
            "timeout": 5,
            "statusMessage": "Starting CoordinationHub dashboard",
        }],
    })
    _CLAUDE_SETTINGS_PATH.write_text(
        json.dumps(settings, indent=2) + "\n", encoding="utf-8",
    )
    print("\nAuto-dashboard hook installed.")
    print(f"  Command: {cmd}")
    print("  Every Claude Code session start will idempotently launch the dashboard")
    print("  at http://127.0.0.1:9898 (skipped if the port is already bound).")


def _install_monitor_skill() -> None:
    """Copy the coordinationhub-monitor SKILL.md into ~/.claude/skills/."""
    _SKILL_DIR.mkdir(parents=True, exist_ok=True)
    target = _SKILL_DIR / "SKILL.md"
    target.write_text(_SKILL_TEMPLATE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    print("\nMonitor skill installed.")
    print(f"  Location: {target}")
    print("  Invoke by asking an LLM to 'watch the swarm' or 'monitor the agents'.")
    print("  The skill instructs the LLM to poll http://127.0.0.1:9898/api/dashboard-data")
    print("  every 30 s and surface boundary crossings, blocked tasks, and stale agents.")


def _install_ide_hooks(project_root: Path, python_path: str) -> None:
    """Detect IDE directories and install or print hook integration notes."""
    kimi_dir = Path.home() / ".kimi"
    cursor_dir = Path.home() / ".cursor"

    if kimi_dir.exists():
        print("\nDetected Kimi CLI.")
        print(f"  Hook adapter: {python_path} -m coordinationhub.hooks.kimi_cli")
        print("  Kimi CLI does not have a native hook system. Integrate by wrapping")
        print("  tool calls or using a sidecar that pipes events to the adapter above.")

    if cursor_dir.exists():
        print("\nDetected Cursor.")
        print(f"  Hook adapter: {python_path} -m coordinationhub.hooks.cursor")
        print("  Cursor does not have a native hook system. Integrate by wrapping")
        print("  tool calls or using a sidecar that pipes events to the adapter above.")


# ------------------------------------------------------------------ #
# auto-start-dashboard
# ------------------------------------------------------------------ #

def cmd_auto_start_dashboard(args) -> int:
    """Idempotently start the SSE dashboard server.

    Designed to be invoked from the Claude Code SessionStart hook installed
    by ``coordinationhub init --auto-dashboard``. Exits silently when:

    - The configured host:port is already bound (dashboard is up, or another
      service has the port).
    - ``serve-sse`` cannot be spawned (e.g. coordinationhub not on PATH).

    Returns the exit code (0 in all normal paths so the hook never blocks
    a session start).
    """
    import socket

    host = getattr(args, "host", "127.0.0.1")
    port = getattr(args, "port", 9898)

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.3)
    try:
        s.connect((host, port))
        return 0
    except OSError:
        pass
    finally:
        try:
            s.close()
        except Exception:
            pass

    log_dir = Path.home() / ".coordinationhub"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "dashboard.log"

    try:
        log_handle = open(log_path, "ab")
    except OSError:
        return 0

    try:
        subprocess.Popen(
            [
                sys.executable, "-m", "coordinationhub", "serve-sse",
                "--no-browser", "--host", host, "--port", str(port),
            ],
            stdout=log_handle,
            stderr=log_handle,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return 0
    return 0


# ------------------------------------------------------------------ #
# watch
# ------------------------------------------------------------------ #

def cmd_watch(args):
    interval = getattr(args, "interval", 5)
    agent_id = getattr(args, "agent_id", None)

    from .cli_utils import engine_from_args as _engine_from_args, close as _close

    try:
        while True:
            os.system("clear" if os.name != "nt" else "cls")

            engine = _engine_from_args(args)
            try:
                result = engine.get_agent_tree(agent_id)
                if "error" in result:
                    print(f"Error: {result['error']}")
                else:
                    print(result["text_tree"])

                status = engine.status()
                print(f"\n--- {status.get('active_agents', 0)} active agents | "
                      f"{status.get('active_locks', 0)} locks | "
                      f"{status.get('recent_conflicts', 0)} conflicts | "
                      f"refreshing every {interval}s (Ctrl+C to stop) ---")
            finally:
                _close(engine)

            time.sleep(interval)

    except KeyboardInterrupt:
        print("\nStopped.")
