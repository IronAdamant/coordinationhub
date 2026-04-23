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
_CLAUDE_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

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


def _backup_settings(path: Path) -> Path:
    """Copy *path* to ``{path}.bak.YYYYMMDD-HHMMSS`` and return the backup.

    T2.7: called before any overwrite of ``~/.claude/settings.json`` so
    the user never loses their pre-init state silently.
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = path.with_suffix(path.suffix + f".bak.{stamp}")
    try:
        backup.write_bytes(path.read_bytes())
    except OSError:
        # Best-effort; a failed backup is not fatal, but we can't offer
        # recovery then. Continue so the init can still fix the config.
        pass
    return backup


def _merge_hooks(existing: dict, new: dict) -> dict:
    """Merge new CoordinationHub hooks into existing hook config.

    T2.7: rewritten to be repeatable-run-safe. The pre-fix merge could
    (a) leave behind stale CoordinationHub matcher blocks from prior
    init runs because it only ever updated the first matcher whose
    ``matcher`` string matched, and (b) accumulate duplicate matcher
    blocks with the same matcher string when no existing block happened
    to already contain a ``coordinationhub`` command.

    New semantics, per event:
      1. Drop every existing matcher block whose hooks list contains ANY
         command referencing ``coordinationhub``. Non-CoordinationHub
         matcher blocks (user's own hooks) are preserved untouched.
      2. Append the fresh matcher blocks from ``new``.

    This means running ``coordinationhub init`` repeatedly always leaves
    exactly one CoordinationHub matcher block per (event, matcher_string)
    pair — no accumulation, no drift.
    """
    merged: dict = {}
    for event_name, existing_matchers in existing.items():
        if event_name in new:
            # Remove any existing block that references coordinationhub;
            # keep user-owned blocks.
            preserved = [
                em for em in existing_matchers
                if not any(
                    "coordinationhub" in hook.get("command", "")
                    for hook in em.get("hooks", [])
                )
            ]
            merged[event_name] = preserved
        else:
            merged[event_name] = list(existing_matchers)
    for event_name, new_matchers in new.items():
        bucket = merged.setdefault(event_name, [])
        for matcher_block in new_matchers:
            bucket.append(matcher_block)
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

    # Write vendor-neutral hooks config
    _NEUTRAL_HOOKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    hooks_config = _fill_hook_command(_HOOKS_CONFIG, python_path)
    _NEUTRAL_HOOKS_PATH.write_text(
        json.dumps({"hooks": hooks_config}, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Hooks written to {_NEUTRAL_HOOKS_PATH}")
    print(f"  Python interpreter: {python_path}")

    # Also write to IDE-specific settings if present
    _CLAUDE_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    # T2.7: on JSONDecodeError abort with a clear error and point the
    # user at their file rather than silently overwriting a file we
    # couldn't parse.
    if _CLAUDE_SETTINGS_PATH.exists():
        try:
            raw = _CLAUDE_SETTINGS_PATH.read_text(encoding="utf-8")
            settings = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError as exc:
            backup = _backup_settings(_CLAUDE_SETTINGS_PATH)
            print(
                f"ERROR: {_CLAUDE_SETTINGS_PATH} is not valid JSON ({exc}). "
                f"A backup was saved to {backup}. Refusing to overwrite — "
                f"fix the file by hand and re-run `coordinationhub init`.",
                file=sys.stderr,
            )
            return
        except OSError as exc:
            print(
                f"ERROR: could not read {_CLAUDE_SETTINGS_PATH}: {exc}",
                file=sys.stderr,
            )
            return
    else:
        settings = {}

    # T2.7: back up the existing settings before we rewrite them so a
    # user whose file was silently changed can recover.
    if _CLAUDE_SETTINGS_PATH.exists():
        backup = _backup_settings(_CLAUDE_SETTINGS_PATH)
        print(f"Existing settings backed up to {backup}")

    existing_hooks = settings.get("hooks", {})
    merged = _merge_hooks(existing_hooks, hooks_config)
    settings["hooks"] = merged

    _CLAUDE_SETTINGS_PATH.write_text(
        json.dumps(settings, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Hooks also written to {_CLAUDE_SETTINGS_PATH}")

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

    T2.7: wraps ``json.loads`` in a try/except. Previously a corrupt
    ``~/.claude/settings.json`` would propagate as an uncaught
    ``JSONDecodeError`` out of ``cmd_init`` — now the user sees a clear
    message and the file is left untouched.
    """
    cmd = _AUTO_DASHBOARD_CMD_TEMPLATE.format(python=python_path)
    if not _CLAUDE_SETTINGS_PATH.exists():
        return
    try:
        raw = _CLAUDE_SETTINGS_PATH.read_text(encoding="utf-8")
        settings = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError) as exc:
        print(
            f"WARNING: could not install auto-dashboard hook "
            f"({_CLAUDE_SETTINGS_PATH} is unreadable: {exc}). Skipping.",
            file=sys.stderr,
        )
        return
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
    print("  Every IDE session start will idempotently launch the dashboard")
    print("  at http://127.0.0.1:9898 (skipped if the port is already bound).")


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
    print("  Install path is vendor-neutral; copy to ~/.claude/skills/ if using an IDE that reads from there.")


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
