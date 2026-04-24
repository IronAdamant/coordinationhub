#!/usr/bin/env python3
"""CoordinationHub stdio event adapter.

Provides automatic file locking, change notifications, subagent tracking,
and Stele/Trammel bridging for IDE sessions via stdin/stdout.

Reads hook event JSON from stdin, outputs decision JSON to stdout.
Fails gracefully (exit 0) if coordinationhub is not importable.

Events handled:
  SessionStart         → register session root agent
  UserPromptSubmit     → stamp root agent's current_task with the prompt
  PreToolUse Write|Edit→ acquire file lock
  PreToolUse Agent     → stash sub-agent description for the following SubagentStart
  PostToolUse          → notify change after Write/Edit; Stele/Trammel bridge
  SubagentStart        → register child agent, consume pending task for current_task
  SubagentStop         → deregister child agent
  SessionEnd           → release all locks, deregister agent
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

# ---------------------------------------------------------------------------
# Error logging — hooks fail open, but errors are recorded for debugging
# ---------------------------------------------------------------------------

_LOG_MAX_BYTES = 1_048_576  # 1 MB


def _log_error(hook_event: str, exc: Exception) -> None:
    """Append error to ~/.coordinationhub/hook.log and stderr.

    Truncates the log file when it exceeds 1 MB to prevent unbounded growth.
    Never raises — logging failures are silently ignored.
    """
    try:
        log_dir = Path.home() / ".coordinationhub"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "hook.log"

        # Truncate if too large: keep last ~500 lines
        if log_path.exists() and log_path.stat().st_size > _LOG_MAX_BYTES:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            log_path.write_text("\n".join(lines[-500:]) + "\n", encoding="utf-8")

        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
        entry = f"[{ts}] {hook_event}: {exc}\n{''.join(tb)}\n"

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(entry)

        print(f"[CoordinationHub] {hook_event} error: {exc}", file=sys.stderr)
    except Exception:
        pass  # logging itself must never crash the hook


# ---------------------------------------------------------------------------
# Event snapshot capture (for contract test fixtures)
# ---------------------------------------------------------------------------


def _save_event_snapshot(event: dict) -> None:
    """Save raw hook event JSON for contract test fixtures.

    T3.14: filename now includes microseconds + a 4-char monotonic
    counter suffix so two events recorded in the same second don't
    silently overwrite each other. Pre-fix, burst events (common on
    IDE startup: SessionStart + UserPromptSubmit + PreToolUse in <1s)
    collided on the 1-second-resolution filename and only the last
    one landed on disk.
    """
    try:
        snap_dir = Path.home() / ".coordinationhub" / "event_snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        hook_name = event.get("hook_event_name", "unknown")
        tool_name = event.get("tool_name", "")
        tag = f"{hook_name}_{tool_name}" if tool_name else hook_name
        # Microsecond resolution + per-process sequence counter for a
        # collision-proof unique suffix within the same millisecond.
        now = time.time()
        micro = int(now * 1_000_000) % 1_000_000
        ts = time.strftime("%Y%m%d_%H%M%S")
        seq = _snapshot_seq()
        path = snap_dir / f"{tag}_{ts}_{micro:06d}_{seq:04d}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(event, f, indent=2, default=str)
    except Exception:
        pass


_snapshot_counter = 0
_snapshot_counter_lock = None


def _snapshot_seq() -> int:
    """Return a monotonic short counter per-process for snapshot filenames."""
    global _snapshot_counter, _snapshot_counter_lock
    import threading as _t
    if _snapshot_counter_lock is None:
        _snapshot_counter_lock = _t.Lock()
    with _snapshot_counter_lock:
        _snapshot_counter = (_snapshot_counter + 1) % 10_000
        return _snapshot_counter


# ---------------------------------------------------------------------------
# Stdio event adapter
# ---------------------------------------------------------------------------

from coordinationhub.hooks.base import BaseHook


class StdioHook(BaseHook):
    """Stdio event adapter over BaseHook."""

    IDE_PREFIX = "cc"

    @classmethod
    def from_cwd(cls, cwd: str) -> "StdioHook":
        project_dir = os.environ.get("IDE_PROJECT_DIR", cwd)
        return cls(project_root=project_dir)

    @staticmethod
    def _subagent_type(event: dict) -> str:
        return (
            event.get("agent_type")
            or event.get("tool_input", {}).get("subagent_type")
            or "agent"
        )

    @staticmethod
    def _raw_agent_id(event: dict) -> str | None:
        return event.get("agent_id") or event.get("subagent_id")


# ---------------------------------------------------------------------------
# Module-level helpers (used by tests and external integrations)
# ---------------------------------------------------------------------------

def _session_agent_id(session_id: str) -> str:
    # T2.9 + T6.28: single shared formatter guarantees this helper
    # stays in lockstep with BaseHook.session_agent_id.
    from coordinationhub.hooks.base import build_session_agent_id
    return build_session_agent_id(StdioHook.IDE_PREFIX, session_id)


def _subagent_type(event: dict) -> str:
    return StdioHook._subagent_type(event)


def _resolve_agent_id(event: dict, engine=None) -> str:
    session_id = event.get("session_id", "")
    raw_id = event.get("subagent_id") or event.get("agent_id")
    if engine is not None:
        hook = StdioHook(project_root=str(engine._storage.project_root))
        try:
            return hook.resolve_agent_id(session_id, raw_id)
        finally:
            hook.close()
    if raw_id:
        return raw_id
    return _session_agent_id(session_id)


def _subagent_id(parent_id: str, event: dict, engine=None) -> str:
    agent_type = _subagent_type(event)
    tool_use_id = event.get("tool_use_id", "")
    if engine is not None:
        hook = StdioHook(project_root=str(engine._storage.project_root))
        try:
            return hook.subagent_id(parent_id, agent_type, tool_use_id)
        finally:
            hook.close()
    if tool_use_id:
        return f"{parent_id}.{agent_type}.{tool_use_id[:6]}"
    return f"{parent_id}.{agent_type}.0"


def _get_engine(cwd: str):
    hook = StdioHook(project_root=cwd)
    return hook.engine


class _HookRunner:
    """Contextual helper: lazily builds one StdioHook and keeps it alive.

    T3.2: each ``handle_*`` function previously constructed its own
    ``StdioHook`` (which boots a ``CoordinationEngine``, opens SQLite,
    runs migrations, warms the lock cache). When ``main()`` dispatches a
    single event that's fine, but tests and future batch callers that
    fire multiple handlers in one process paid the boot cost per call.
    The runner centralises construction + cleanup so handlers can share
    one engine. Each ``handle_*`` still accepts ``hook=None`` for
    backwards compat — external callers that pre-date T3.2 keep working.
    """

    __slots__ = ("_cwd", "_hook")

    def __init__(self, cwd: str) -> None:
        self._cwd = cwd
        self._hook: StdioHook | None = None

    @property
    def hook(self) -> StdioHook:
        if self._hook is None:
            self._hook = StdioHook.from_cwd(self._cwd)
        return self._hook

    def close(self) -> None:
        if self._hook is not None:
            self._hook.close()
            self._hook = None


def _resolve_hook(event: dict, hook: StdioHook | None) -> tuple[StdioHook, bool]:
    """Return ``(hook, owns_it)``. If ``hook`` is None, construct one and
    mark it as owned so the caller closes it; otherwise return the shared
    hook with owns_it=False.
    """
    if hook is not None:
        return hook, False
    return StdioHook.from_cwd(event.get("cwd", ".")), True


# ---------------------------------------------------------------------------
# Event handlers (thin wrappers around StdioHook)
# ---------------------------------------------------------------------------


def handle_session_start(event: dict, hook: StdioHook | None = None) -> dict | None:
    h, owns = _resolve_hook(event, hook)
    try:
        h.on_session_start(event.get("session_id", ""))
    finally:
        if owns:
            h.close()
    return None


def handle_user_prompt_submit(
    event: dict, hook: StdioHook | None = None,
) -> dict | None:
    h, owns = _resolve_hook(event, hook)
    try:
        h.on_user_prompt(event.get("session_id", ""), event.get("prompt", ""))
    finally:
        if owns:
            h.close()
    return None


def handle_pre_agent(event: dict, hook: StdioHook | None = None) -> dict | None:
    tool_input = event.get("tool_input", {})
    tool_use_id = event.get("tool_use_id", "")
    subagent_type = tool_input.get("subagent_type", "")
    if not tool_use_id or not subagent_type:
        return None

    h, owns = _resolve_hook(event, hook)
    try:
        h.stash_subagent_description(
            session_id=event.get("session_id", ""),
            tool_use_id=tool_use_id,
            subagent_type=subagent_type,
            description=tool_input.get("description", "") or "",
            prompt=tool_input.get("prompt", "") or "",
        )
    finally:
        if owns:
            h.close()
    return None


def handle_pre_write(event: dict, hook: StdioHook | None = None) -> dict | None:
    file_path = event.get("tool_input", {}).get("file_path")
    if not file_path:
        return None

    h, owns = _resolve_hook(event, hook)
    try:
        return h.on_pre_write(
            session_id=event.get("session_id", ""),
            file_path=file_path,
            raw_ide_id=StdioHook._raw_agent_id(event),
        )
    finally:
        if owns:
            h.close()


def handle_post_write(event: dict, hook: StdioHook | None = None) -> dict | None:
    file_path = event.get("tool_input", {}).get("file_path")
    if not file_path:
        return None

    h, owns = _resolve_hook(event, hook)
    try:
        h.on_post_write(
            session_id=event.get("session_id", ""),
            file_path=file_path,
            raw_ide_id=StdioHook._raw_agent_id(event),
        )
    finally:
        if owns:
            h.close()
    return None


def handle_post_stele_index(
    event: dict, hook: StdioHook | None = None,
) -> dict | None:
    tool_input = event.get("tool_input", {})
    doc_path = tool_input.get("document_path") or tool_input.get("path")
    paths = tool_input.get("paths", [])
    if doc_path:
        paths = [doc_path] + paths
    if not paths:
        return None

    h, owns = _resolve_hook(event, hook)
    try:
        h.on_post_index(
            session_id=event.get("session_id", ""),
            paths=[str(p) for p in paths],
            raw_ide_id=StdioHook._raw_agent_id(event),
        )
    finally:
        if owns:
            h.close()
    return None


def handle_post_trammel_claim(
    event: dict, hook: StdioHook | None = None,
) -> dict | None:
    tool_input = event.get("tool_input", {})
    step_id = tool_input.get("step_id", "")
    plan_id = tool_input.get("plan_id", "")
    task = f"trammel:{plan_id}/{step_id}" if plan_id else str(step_id)

    h, owns = _resolve_hook(event, hook)
    try:
        h.on_task_claim(
            session_id=event.get("session_id", ""),
            task=task,
            raw_ide_id=StdioHook._raw_agent_id(event),
        )
    finally:
        if owns:
            h.close()
    return None


def handle_subagent_start(
    event: dict, hook: StdioHook | None = None,
) -> dict | None:
    h, owns = _resolve_hook(event, hook)
    try:
        h.on_subagent_start(
            session_id=event.get("session_id", ""),
            raw_ide_id=StdioHook._raw_agent_id(event),
            agent_type=StdioHook._subagent_type(event),
            description=event.get("tool_input", {}).get("description") or "",
        )
    finally:
        if owns:
            h.close()
    return None


def handle_subagent_stop(
    event: dict, hook: StdioHook | None = None,
) -> dict | None:
    h, owns = _resolve_hook(event, hook)
    try:
        h.on_subagent_stop(
            session_id=event.get("session_id", ""),
            raw_ide_id=StdioHook._raw_agent_id(event),
            agent_type=StdioHook._subagent_type(event),
        )
    finally:
        if owns:
            h.close()
    return None


def handle_session_end(event: dict, hook: StdioHook | None = None) -> dict | None:
    h, owns = _resolve_hook(event, hook)
    try:
        return h.on_session_end(event.get("session_id", ""))
    finally:
        if owns:
            h.close()


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


# T3.15: exact MCP tool-name allow-lists for the PostToolUse routers.
# Add new tool aliases here rather than loosening the match condition.
_STELE_INDEX_TOOLS = frozenset({
    "mcp__stele-context__index",
})
_TRAMMEL_CLAIM_TOOLS = frozenset({
    "mcp__trammel__claim_step",
})


def main() -> None:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        event = json.loads(raw)
    except (json.JSONDecodeError, OSError):
        return  # graceful no-op

    if os.environ.get("COORDINATIONHUB_CAPTURE_EVENTS"):
        _save_event_snapshot(event)

    hook_event = event.get("hook_event_name", "")
    tool_name = event.get("tool_name", "")

    # T3.2: construct a single StdioHook up front and reuse it across
    # every branch below. Pre-fix, each branch's ``handle_*`` built its
    # own hook — fine when only one branch fires per invocation, but
    # boot cost is non-trivial (engine __init__ + start + lock cache
    # warm + graph load). Closing happens in the outer finally so the
    # cleanup path runs even when a handler raises.
    runner = _HookRunner(event.get("cwd", "."))
    try:
        result = None

        if hook_event == "SessionStart":
            result = handle_session_start(event, hook=runner.hook)

        elif hook_event == "UserPromptSubmit":
            result = handle_user_prompt_submit(event, hook=runner.hook)

        elif hook_event == "PreToolUse" and tool_name in ("Write", "Edit"):
            result = handle_pre_write(event, hook=runner.hook)

        elif hook_event == "PreToolUse" and tool_name == "Agent":
            result = handle_pre_agent(event, hook=runner.hook)

        elif hook_event == "PostToolUse":
            if tool_name in ("Write", "Edit"):
                result = handle_post_write(event, hook=runner.hook)
            # T3.15: exact-match against an allow-list instead of loose
            # substring checks. Previously ``"stele" in tool_name and
            # "index" in tool_name`` matched any tool whose name
            # contained both words in any order (e.g.
            # ``unstele_reindexer``).
            elif tool_name in _STELE_INDEX_TOOLS:
                result = handle_post_stele_index(event, hook=runner.hook)
            elif tool_name in _TRAMMEL_CLAIM_TOOLS:
                result = handle_post_trammel_claim(event, hook=runner.hook)

        elif hook_event == "SubagentStart":
            result = handle_subagent_start(event, hook=runner.hook)

        elif hook_event == "SubagentStop":
            result = handle_subagent_stop(event, hook=runner.hook)

        elif hook_event == "SessionEnd":
            result = handle_session_end(event, hook=runner.hook)

        if result:
            json.dump(result, sys.stdout)

    except ImportError:
        pass
    except Exception as exc:
        _log_error(hook_event or "unknown", exc)
    finally:
        runner.close()


if __name__ == "__main__":
    main()
