#!/usr/bin/env python3
"""CoordinationHub hook for Claude Code.

Provides automatic file locking, change notifications, subagent tracking,
and Stele/Trammel bridging for Claude Code sessions.

Reads hook event JSON from stdin, outputs decision JSON to stdout.
Fails gracefully (exit 0) if coordinationhub is not importable.

Events handled:
  SessionStart     → register session root agent
  PreToolUse       → acquire file lock before Write/Edit
  PostToolUse      → notify change after Write/Edit; Stele/Trammel bridge
  SubagentStart    → register child agent for spawned subagent
  SubagentStop     → deregister child agent
  SessionEnd       → release all locks, deregister agent
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Engine helpers
# ---------------------------------------------------------------------------

def _get_engine(cwd: str):
    """Create a CoordinationEngine rooted at the project directory."""
    from coordinationhub.core import CoordinationEngine

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", cwd)
    engine = CoordinationEngine(project_root=Path(project_dir))
    engine.start()
    return engine


def _session_agent_id(session_id: str) -> str:
    """Deterministic root agent ID from the Claude Code session."""
    short = session_id[:12] if session_id else "unknown"
    return f"hub.cc.{short}"


def _subagent_id(parent_id: str, event: dict, engine=None) -> str:
    """Deterministic child agent ID for a spawned subagent.

    Uses tool_use_id when provided by Claude Code.  Falls back to a
    sequence number derived from existing children of *parent_id* so
    that concurrent SubagentStart events still produce unique IDs.
    """
    tool_input = event.get("tool_input", {})
    agent_type = tool_input.get("subagent_type", "agent")
    tool_use_id = event.get("tool_use_id", "")
    if tool_use_id:
        return f"{parent_id}.{agent_type}.{tool_use_id[:6]}"

    # Fallback: derive next sequence number from existing children
    seq = 0
    if engine is not None:
        try:
            agents = engine.list_agents(active_only=False)
            prefix = f"{parent_id}.{agent_type}."
            existing = [
                a["agent_id"] for a in agents.get("agents", [])
                if a["agent_id"].startswith(prefix)
            ]
            seq = len(existing)
        except Exception:
            pass
    return f"{parent_id}.{agent_type}.{seq}"


def _resolve_agent_id(event: dict) -> str:
    """Return the most specific agent ID available in the event.

    Prefers ``subagent_id`` (if Claude Code populates it for subagent
    tool calls) over the session root ID.
    """
    subagent_id = event.get("subagent_id") or event.get("agent_id")
    if subagent_id:
        return subagent_id
    return _session_agent_id(event.get("session_id", ""))


def _ensure_registered(engine, agent_id: str, parent_id: str | None = None) -> None:
    """Register agent if not already active."""
    agents = engine.list_agents(active_only=True)
    if any(a["agent_id"] == agent_id for a in agents.get("agents", [])):
        engine.heartbeat(agent_id)
        return
    engine.register_agent(agent_id, parent_id=parent_id)


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------

def handle_session_start(event: dict) -> dict | None:
    """Register the session's root agent."""
    engine = _get_engine(event.get("cwd", "."))
    try:
        agent_id = _session_agent_id(event.get("session_id", ""))
        _ensure_registered(engine, agent_id)
    finally:
        engine.close()
    return None


def handle_pre_write(event: dict) -> dict | None:
    """Acquire lock on the file before Write/Edit."""
    tool_input = event.get("tool_input", {})
    file_path = tool_input.get("file_path")
    if not file_path:
        return None

    engine = _get_engine(event.get("cwd", "."))
    try:
        agent_id = _resolve_agent_id(event)
        _ensure_registered(engine, agent_id)

        result = engine.acquire_lock(file_path, agent_id, ttl=600.0)
        if result.get("acquired"):
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "additionalContext": f"[CoordinationHub] Lock acquired: {file_path}",
                }
            }

        holder = result.get("locked_by", "unknown")
        if holder == agent_id:
            return None  # already hold it, proceed

        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"[CoordinationHub] File locked by {holder}. "
                    f"Use 'coordinationhub release-lock {file_path} {holder}' to force."
                ),
            }
        }
    finally:
        engine.close()


def handle_post_write(event: dict) -> dict | None:
    """Fire change notification after Write/Edit completes."""
    tool_input = event.get("tool_input", {})
    file_path = tool_input.get("file_path")
    if not file_path:
        return None

    engine = _get_engine(event.get("cwd", "."))
    try:
        agent_id = _resolve_agent_id(event)
        engine.notify_change(file_path, "modified", agent_id)
    finally:
        engine.close()
    return None


def handle_post_stele_index(event: dict) -> dict | None:
    """Bridge: Stele index → CoordinationHub notify_change.

    Stele's ``index`` tool accepts ``paths`` (plural, array).  Also
    handles the singular ``document_path`` / ``path`` forms for
    forward-compatibility.
    """
    tool_input = event.get("tool_input", {})
    doc_path = tool_input.get("document_path") or tool_input.get("path")
    paths = tool_input.get("paths", [])

    if not doc_path and not paths:
        return None

    engine = _get_engine(event.get("cwd", "."))
    try:
        agent_id = _resolve_agent_id(event)
        if doc_path:
            engine.notify_change(str(doc_path), "indexed", agent_id)
        for p in paths:
            engine.notify_change(str(p), "indexed", agent_id)
    finally:
        engine.close()
    return None


def handle_post_trammel_claim(event: dict) -> dict | None:
    """Bridge: Trammel claim_step → CoordinationHub update_agent_status."""
    tool_input = event.get("tool_input", {})
    step_id = tool_input.get("step_id", "")
    plan_id = tool_input.get("plan_id", "")

    engine = _get_engine(event.get("cwd", "."))
    try:
        agent_id = _resolve_agent_id(event)
        _ensure_registered(engine, agent_id)
        task = f"trammel:{plan_id}/{step_id}" if plan_id else str(step_id)
        engine.update_agent_status(agent_id, current_task=task)
    finally:
        engine.close()
    return None


def handle_subagent_start(event: dict) -> dict | None:
    """Register child agent when Claude Code spawns a subagent."""
    engine = _get_engine(event.get("cwd", "."))
    try:
        parent_id = _session_agent_id(event.get("session_id", ""))
        _ensure_registered(engine, parent_id)
        child_id = _subagent_id(parent_id, event, engine=engine)
        _ensure_registered(engine, child_id, parent_id=parent_id)

        tool_input = event.get("tool_input", {})
        desc = tool_input.get("description", "")
        if desc:
            engine.update_agent_status(child_id, current_task=desc)
    finally:
        engine.close()
    return None


def handle_subagent_stop(event: dict) -> dict | None:
    """Deregister child agent when subagent finishes."""
    engine = _get_engine(event.get("cwd", "."))
    try:
        parent_id = _session_agent_id(event.get("session_id", ""))
        child_id = _subagent_id(parent_id, event, engine=engine)
        try:
            engine.deregister_agent(child_id)
        except Exception:
            pass
    finally:
        engine.close()
    return None


def handle_session_end(event: dict) -> dict | None:
    """Deregister session agent and release all held locks."""
    engine = _get_engine(event.get("cwd", "."))
    try:
        agent_id = _session_agent_id(event.get("session_id", ""))
        try:
            engine.release_agent_locks(agent_id)
            engine.deregister_agent(agent_id)
        except Exception:
            pass
    finally:
        engine.close()
    return None


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def main() -> None:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        event = json.loads(raw)
    except (json.JSONDecodeError, OSError):
        return  # graceful no-op

    hook_event = event.get("hook_event_name", "")
    tool_name = event.get("tool_name", "")

    try:
        result = None

        if hook_event == "SessionStart":
            result = handle_session_start(event)

        elif hook_event == "PreToolUse" and tool_name in ("Write", "Edit"):
            result = handle_pre_write(event)

        elif hook_event == "PostToolUse":
            if tool_name in ("Write", "Edit"):
                result = handle_post_write(event)
            elif "stele" in tool_name and "index" in tool_name:
                result = handle_post_stele_index(event)
            elif "trammel" in tool_name and "claim_step" in tool_name:
                result = handle_post_trammel_claim(event)

        elif hook_event == "SubagentStart":
            result = handle_subagent_start(event)

        elif hook_event == "SubagentStop":
            result = handle_subagent_stop(event)

        elif hook_event == "SessionEnd":
            result = handle_session_end(event)

        if result:
            json.dump(result, sys.stdout)

    except ImportError:
        pass  # coordinationhub not installed — silent no-op
    except Exception:
        pass  # never crash the hook — fail open


if __name__ == "__main__":
    main()
