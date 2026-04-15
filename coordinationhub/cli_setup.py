"""CLI commands for setup and diagnostics: doctor, init, watch."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .cli_utils import print_json as _print_json


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


# ------------------------------------------------------------------ #
# doctor
# ------------------------------------------------------------------ #

def _check_import() -> tuple[bool, str]:
    """Check that coordinationhub is importable."""
    try:
        import coordinationhub  # noqa: F401
        return True, "coordinationhub importable"
    except ImportError as e:
        return False, f"cannot import coordinationhub: {e}"


def _check_hooks_config() -> tuple[bool, str]:
    """Check that Claude Code hook config exists."""
    if not _CLAUDE_SETTINGS_PATH.exists():
        return False, f"no settings file at {_CLAUDE_SETTINGS_PATH}"
    try:
        settings = json.loads(_CLAUDE_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return False, f"cannot read settings: {e}"

    hooks = settings.get("hooks", {})
    if not hooks:
        return False, "no hooks configured in settings.json"

    required = {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "SubagentStart", "SubagentStop", "SessionEnd"}
    present = set(hooks.keys()) & required
    missing = required - present
    if missing:
        return False, f"missing hook events: {', '.join(sorted(missing))}"

    # Check that at least one hook command references coordinationhub
    for event_matchers in hooks.values():
        for matcher_block in event_matchers:
            for hook in matcher_block.get("hooks", []):
                if "coordinationhub" in hook.get("command", ""):
                    return True, "hooks configured correctly"
    return False, "hooks exist but none reference coordinationhub"


def _check_storage_dir() -> tuple[bool, str]:
    """Check that .coordinationhub/ and DB exist."""
    from .paths import detect_project_root
    project_root = detect_project_root()
    if project_root is None:
        storage = Path.home() / ".coordinationhub"
    else:
        storage = project_root / ".coordinationhub"
    if not storage.exists():
        return False, f"storage directory not found: {storage}"
    db_path = storage / "coordination.db"
    if not db_path.exists():
        return False, f"database not found: {db_path}"
    return True, f"database exists at {db_path}"


def _check_schema_version() -> tuple[bool, str]:
    """Check that DB schema version is current."""
    from .paths import detect_project_root
    from .db import _CURRENT_SCHEMA_VERSION
    import sqlite3

    project_root = detect_project_root()
    if project_root is None:
        db_path = Path.home() / ".coordinationhub" / "coordination.db"
    else:
        db_path = project_root / ".coordinationhub" / "coordination.db"
    if not db_path.exists():
        return False, "database not found (run a session first or use 'init')"
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1").fetchone()
        conn.close()
        if row is None:
            return False, "no schema version recorded"
        version = row["version"]
        if version < _CURRENT_SCHEMA_VERSION:
            return False, f"schema v{version} is outdated (current: v{_CURRENT_SCHEMA_VERSION})"
        return True, f"schema v{version} is current"
    except Exception as e:
        return False, f"schema check failed: {e}"


def _check_hook_python() -> tuple[bool, str]:
    """Check that the python3 on PATH can import coordinationhub."""
    python3 = shutil.which("python3")
    if python3 is None:
        return False, "python3 not found on PATH"

    # If the hooks config specifies a different python, check that one
    hook_python = python3
    if _CLAUDE_SETTINGS_PATH.exists():
        try:
            settings = json.loads(_CLAUDE_SETTINGS_PATH.read_text(encoding="utf-8"))
            hooks = settings.get("hooks", {})
            for event_matchers in hooks.values():
                for matcher_block in event_matchers:
                    for hook in matcher_block.get("hooks", []):
                        cmd = hook.get("command", "")
                        if "coordinationhub" in cmd:
                            # Extract the python path from the command
                            parts = cmd.split(" -m ")
                            if parts:
                                hook_python = parts[0].strip()
                            break
        except Exception:
            pass

    try:
        result = subprocess.run(
            [hook_python, "-c", "import coordinationhub"],
            capture_output=True, timeout=10,
        )
        if result.returncode == 0:
            return True, f"hook python ({hook_python}) can import coordinationhub"
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        return False, f"hook python ({hook_python}) cannot import coordinationhub: {stderr}"
    except FileNotFoundError:
        return False, f"hook python not found: {hook_python}"
    except subprocess.TimeoutExpired:
        return False, f"hook python ({hook_python}) timed out"


def run_doctor() -> list[dict]:
    """Run all diagnostic checks. Returns list of {name, ok, message}."""
    checks = [
        ("import", _check_import),
        ("hooks_config", _check_hooks_config),
        ("storage_dir", _check_storage_dir),
        ("schema_version", _check_schema_version),
        ("hook_python", _check_hook_python),
    ]
    results = []
    for name, fn in checks:
        try:
            ok, msg = fn()
        except Exception as e:
            ok, msg = False, f"check crashed: {e}"
        results.append({"name": name, "ok": ok, "message": msg})
    return results


def cmd_doctor(args):
    results = run_doctor()
    if getattr(args, "json_output", False):
        _print_json({"checks": results, "all_ok": all(r["ok"] for r in results)})
        return

    all_ok = True
    for r in results:
        icon = "OK" if r["ok"] else "FAIL"
        if not r["ok"]:
            all_ok = False
        print(f"  [{icon:4s}] {r['name']}: {r['message']}")

    if all_ok:
        print("\nAll checks passed.")
    else:
        print("\nSome checks failed. Run 'coordinationhub init' to fix setup issues.")


# ------------------------------------------------------------------ #
# init
# ------------------------------------------------------------------ #

def cmd_init(args):
    python_path = sys.executable

    # Step 1: Create .coordinationhub directory
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

    # Step 2: Initialize DB schema
    from .core import CoordinationEngine
    engine = CoordinationEngine(project_root=project_root)
    engine.start()
    engine.close()
    print("Database initialized.")

    # Step 3: Write/merge hooks into ~/.claude/settings.json
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

    # Step 4: Detect other IDEs and print integration notes
    _install_ide_hooks(project_root or Path.cwd(), python_path)

    # Step 5: Run doctor
    print("\nRunning diagnostics...")
    results = run_doctor()
    all_ok = True
    for r in results:
        icon = "OK" if r["ok"] else "FAIL"
        if not r["ok"]:
            all_ok = False
        print(f"  [{icon:4s}] {r['name']}: {r['message']}")

    # Step 6 (opt-in): auto-dashboard SessionStart hook
    if getattr(args, "auto_dashboard", False):
        _install_auto_dashboard_hook(python_path)

    # Step 7 (opt-in): coordinationhub-monitor skill
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
        # The main init step always writes settings.json before this is called
        return
    settings = json.loads(_CLAUDE_SETTINGS_PATH.read_text(encoding="utf-8"))
    hooks = settings.setdefault("hooks", {})
    session_start = hooks.setdefault("SessionStart", [])

    # Update existing entry if present
    for matcher_block in session_start:
        for hook in matcher_block.get("hooks", []):
            if "auto-start-dashboard" in hook.get("command", ""):
                hook["command"] = cmd
                _CLAUDE_SETTINGS_PATH.write_text(
                    json.dumps(settings, indent=2) + "\n", encoding="utf-8",
                )
                print(f"\nAuto-dashboard hook updated: {cmd}")
                return

    # Append a fresh matcher block — doesn't disturb the existing
    # claude_code.py hook that registers the root agent.
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
            # Find if this matcher already exists with a coordinationhub hook
            found = False
            for em in existing_matchers:
                if em.get("matcher") == new_matcher["matcher"]:
                    for hook in em.get("hooks", []):
                        if "coordinationhub" in hook.get("command", ""):
                            # Update existing hook command
                            hook["command"] = new_matcher["hooks"][0]["command"]
                            found = True
                            break
                if found:
                    break
            if not found:
                existing_matchers.append(new_matcher)

    return merged


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

    # Probe — if anything is listening we're done.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.3)
    try:
        s.connect((host, port))
        return 0  # something already serving on this port
    except OSError:
        pass
    finally:
        try:
            s.close()
        except Exception:
            pass

    # Spawn ``serve-sse`` as a detached daemon.
    log_dir = Path.home() / ".coordinationhub"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "dashboard.log"

    try:
        log_handle = open(log_path, "ab")
    except OSError:
        return 0  # filesystem refused — fail silently to keep the hook fast

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
        return 0  # spawn failed — still don't block the session
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
            # Clear terminal
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
