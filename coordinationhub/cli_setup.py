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

_NEUTRAL_HOOKS_PATH = Path.home() / ".coordinationhub" / "hooks.json"

_HOOK_CMD_TEMPLATE = "{python} -m coordinationhub.hooks.stdio_adapter"
_AUTO_DASHBOARD_CMD_TEMPLATE = "{python} -m coordinationhub auto-start-dashboard"

_SKILL_DIR = Path.home() / ".coordinationhub" / "skills" / "coordinationhub-monitor"
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


# ------------------------------------------------------------------ #
# init
# ------------------------------------------------------------------ #

# T6.20: cmd_init intentionally skips the shared ``@_command`` decorator.
# The decorator assumes an engine is ready to be started against an
# existing storage dir, but ``init`` is the command that CREATES the
# storage dir + runs the first migration. It also has to write hook
# files that live outside the engine's purview. So engine construction
# happens here, scoped to the project root detected on the spot.
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

    # Write vendor-neutral hooks config
    _NEUTRAL_HOOKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    hooks_config = _fill_hook_command(_HOOKS_CONFIG, python_path)
    _NEUTRAL_HOOKS_PATH.write_text(
        json.dumps({"hooks": hooks_config}, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Hooks written to {_NEUTRAL_HOOKS_PATH}")
    print(f"  Python interpreter: {python_path}")

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
    """Auto-dashboard installation for Claude Code has been removed.

    The --auto-dashboard flag is now a no-op. Users who need a SessionStart
    hook for their IDE should configure it manually via the vendor-neutral
    ~/.coordinationhub/hooks.json or their IDE's native hook mechanism.
    """
    print("\nNote: --auto-dashboard is deprecated (Claude Code integration removed).")
    print("  The dashboard can still be started manually with:")
    print(f"    {python_path} -m coordinationhub serve-sse --host 127.0.0.1 --port 9898")


def _install_monitor_skill() -> None:
    """Copy the coordinationhub-monitor SKILL.md into ~/.coordinationhub/skills/."""
    _SKILL_DIR.mkdir(parents=True, exist_ok=True)
    target = _SKILL_DIR / "SKILL.md"
    target.write_text(_SKILL_TEMPLATE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    print("\nMonitor skill installed.")
    print(f"  Location: {target}")
    print("  Invoke by asking an LLM to 'watch the swarm' or 'monitor the agents'.")
    print("  The skill instructs the LLM to poll http://127.0.0.1:9898/api/dashboard-data")
    print("  every 30 s and surface boundary crossings, blocked tasks, and stale agents.")
    print("  Install path is vendor-neutral; copy into your IDE's skills directory if needed.")


# ------------------------------------------------------------------ #
# auto-start-dashboard
# ------------------------------------------------------------------ #

# T6.20: cmd_auto_start_dashboard intentionally skips ``@_command``. It
# never constructs a CoordinationEngine — it's a pre-flight socket
# probe that spawns ``serve-sse`` as a detached subprocess if the port
# is free. Wrapping this in the engine-lifecycle decorator would add
# DB startup cost to every IDE SessionStart hook for no benefit.
def cmd_auto_start_dashboard(args) -> int:
    """Idempotently start the SSE dashboard server.

    Designed to be invoked from an IDE SessionStart hook installed
    by ``coordinationhub init --auto-dashboard``. Exits silently when:

    - The configured host:port is already bound (dashboard is up, or another
      service has the port).
    - ``serve-sse`` cannot be spawned (e.g. coordinationhub not on PATH).

    Returns the exit code (0 in all normal paths).
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

# T6.20: cmd_watch intentionally skips ``@_command``. The decorator
# does one engine start + close around the handler; ``watch`` needs a
# FRESH engine per tick so stale in-memory caches don't produce stale
# dashboard renders. Looping handles lifecycle explicitly instead.
def cmd_watch(args):
    interval = getattr(args, "interval", 5)
    agent_id = getattr(args, "agent_id", None)

    from .cli_utils import engine_from_args as _engine_from_args, close as _close

    try:
        while True:
            # T3.18: ANSI clear-screen + home-cursor instead of
            # shelling out to ``clear``/``cls`` (subprocess spawn per
            # iteration + platform-specific command). Works in any VT
            # terminal including Windows 10+ with ANSI enabled.
            sys.stdout.write("\x1b[2J\x1b[H")
            sys.stdout.flush()

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
