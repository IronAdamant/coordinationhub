"""Diagnostic checks for ``coordinationhub doctor``.

Extracted from :mod:`cli_setup` so both modules stay under the 500-LOC
budget. Each ``_check_*`` returns ``(ok, message)``; :func:`run_doctor`
aggregates them and :func:`cmd_doctor` prints the result.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .cli_utils import print_json as _print_json


_CLAUDE_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"


def _check_import() -> tuple[bool, str]:
    """Check that coordinationhub is importable."""
    try:
        import coordinationhub  # noqa: F401
        return True, "coordinationhub importable"
    except ImportError as e:
        return False, f"cannot import coordinationhub: {e}"


def _check_hooks_config() -> tuple[bool, str]:
    """Check that IDE hook config exists."""
    # Vendor-neutral path
    neutral_path = Path.home() / ".coordinationhub" / "hooks.json"
    for path in (neutral_path, _CLAUDE_SETTINGS_PATH):
        if path.exists():
            try:
                settings = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                continue
            hooks = settings.get("hooks", {})
            if not hooks:
                continue
            required = {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
                        "SubagentStart", "SubagentStop", "SessionEnd"}
            present = set(hooks.keys()) & required
            missing = required - present
            if missing:
                continue
            for event_matchers in hooks.values():
                for matcher_block in event_matchers:
                    for hook in matcher_block.get("hooks", []):
                        if "coordinationhub" in hook.get("command", ""):
                            return True, f"hooks configured correctly at {path}"
    return True, "no IDE hook config found (run 'coordinationhub init' if using an IDE with hooks)"



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
