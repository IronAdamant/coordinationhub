#!/usr/bin/env python3
"""CoordinationHub hook for Claude Code.

Provides automatic file locking, change notifications, subagent tracking,
and Stele/Trammel bridging for Claude Code sessions.

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
# Engine helpers
# ---------------------------------------------------------------------------

def _save_event_snapshot(event: dict) -> None:
    """Save raw hook event JSON for contract test fixtures.

    Activated by ``COORDINATIONHUB_CAPTURE_EVENTS=1``.  Writes to
    ``~/.coordinationhub/event_snapshots/<event_type>_<timestamp>.json``.
    """
    try:
        snap_dir = Path.home() / ".coordinationhub" / "event_snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        hook_name = event.get("hook_event_name", "unknown")
        tool_name = event.get("tool_name", "")
        tag = f"{hook_name}_{tool_name}" if tool_name else hook_name
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = snap_dir / f"{tag}_{ts}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(event, f, indent=2, default=str)
    except Exception:
        pass  # capture must never crash the hook


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


def _subagent_type(event: dict) -> str:
    """Return the sub-agent type from a SubagentStart event.

    Real Claude Code events carry ``agent_type`` at the top level
    (e.g. ``"Explore"``, ``"Plan"``, ``"general-purpose"``). Older
    test fixtures used ``tool_input.subagent_type``; we accept either
    so tests stay passable during the transition. Defaults to
    ``"agent"`` only if neither is present.
    """
    return (
        event.get("agent_type")
        or event.get("tool_input", {}).get("subagent_type")
        or "agent"
    )


def _subagent_id(parent_id: str, event: dict, engine=None) -> str:
    """Deterministic child agent ID for a spawned subagent.

    Uses tool_use_id when provided (legacy test fixtures). Real Claude
    Code SubagentStart events don't carry tool_use_id, so the fallback
    derives a sequence number from existing children of *parent_id*
    with the same agent type.
    """
    agent_type = _subagent_type(event)
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


def _resolve_agent_id(event: dict, engine=None) -> str:
    """Return the most specific agent ID available in the event.

    When *engine* is provided, maps raw Claude Code hex IDs (e.g.
    ``ac70a34bf2d2264d4``) back to the ``hub.cc.*`` child ID that
    SubagentStart registered.  Falls back to the raw ID (registering a
    new agent) only when no mapping exists.
    """
    raw_id = event.get("subagent_id") or event.get("agent_id")
    if raw_id:
        if engine is not None:
            mapped = engine.find_agent_by_claude_id(raw_id)
            if mapped:
                return mapped
        return raw_id
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


def handle_user_prompt_submit(event: dict) -> dict | None:
    """Stamp the root session agent's ``current_task`` with the latest prompt.

    Makes the root agent show "what the user just asked me to do" in
    ``get_agent_tree`` and ``coordinationhub watch`` — symmetric with
    sub-agents, whose ``current_task`` is populated from the Agent
    tool's ``description`` during SubagentStart. Without this hook, the
    root agent's task column stays empty even though locks and writes
    are fully tracked.

    The prompt is truncated to keep the agent tree compact.
    """
    prompt = (event.get("prompt") or "").strip()
    if not prompt:
        return None

    summary = prompt if len(prompt) <= 120 else prompt[:117] + "..."
    # Collapse whitespace so multi-line prompts render cleanly in the tree.
    summary = " ".join(summary.split())

    engine = _get_engine(event.get("cwd", "."))
    try:
        agent_id = _session_agent_id(event.get("session_id", ""))
        _ensure_registered(engine, agent_id)
        engine.update_agent_status(agent_id, current_task=summary)
    finally:
        engine.close()
    return None


def handle_pre_agent(event: dict) -> dict | None:
    """Stash the sub-agent's task description for the next SubagentStart.

    Claude Code fires ``PreToolUse`` with ``tool_name == "Agent"``
    right before spawning a sub-agent. The event carries
    ``tool_input.description``, ``tool_input.prompt``, and
    ``tool_input.subagent_type`` — everything needed to populate the
    child's ``current_task`` column. But the ``SubagentStart`` event
    that follows carries *none* of those fields, so we stash them here
    keyed by (``session_id``, ``subagent_type``) and pop on SubagentStart.
    """
    tool_input = event.get("tool_input", {})
    tool_use_id = event.get("tool_use_id", "")
    subagent_type = tool_input.get("subagent_type", "")
    description = tool_input.get("description", "") or ""
    prompt = tool_input.get("prompt", "") or ""
    session_id = event.get("session_id", "")

    if not tool_use_id or not subagent_type:
        return None  # nothing we can correlate on

    engine = _get_engine(event.get("cwd", "."))
    try:
        from coordinationhub.pending_tasks import stash_pending_task
        stash_pending_task(
            engine._connect,
            tool_use_id=tool_use_id,
            session_id=session_id,
            subagent_type=subagent_type,
            description=description,
            prompt=prompt,
        )
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
        agent_id = _resolve_agent_id(event, engine=engine)
        _ensure_registered(engine, agent_id)

        # Reap expired locks before attempting acquire — prevents stale locks
        # from completed agents blocking new work (Review Ten finding #1).
        # Grace period: spare locks held by agents with recent heartbeats,
        # preventing expiry during long model calls (Review Thirteen finding).
        engine.reap_expired_locks(agent_grace_seconds=120.0)

        result = engine.acquire_lock(file_path, agent_id, ttl=300.0)
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
    """Fire change notification and refresh lock after Write/Edit completes.

    The lock refresh extends the TTL after the tool completes, preventing
    expiry when the model takes longer than the TTL between PreToolUse
    and PostToolUse.
    """
    tool_input = event.get("tool_input", {})
    file_path = tool_input.get("file_path")
    if not file_path:
        return None

    engine = _get_engine(event.get("cwd", "."))
    try:
        agent_id = _resolve_agent_id(event, engine=engine)
        engine.notify_change(file_path, "modified", agent_id)
        # First-write-wins file ownership
        try:
            engine.claim_file_ownership(file_path, agent_id)
        except Exception:
            pass  # ownership tracking is best-effort
        try:
            engine.release_lock(file_path, agent_id)
        except Exception:
            pass  # lock may have been released already
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
        agent_id = _resolve_agent_id(event, engine=engine)
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
        agent_id = _resolve_agent_id(event, engine=engine)
        _ensure_registered(engine, agent_id)
        task = f"trammel:{plan_id}/{step_id}" if plan_id else str(step_id)
        engine.update_agent_status(agent_id, current_task=task)
    finally:
        engine.close()
    return None


def handle_subagent_start(event: dict) -> dict | None:
    """Register child agent when Claude Code spawns a subagent.

    Real Claude Code SubagentStart events carry only ``agent_id`` (raw
    hex), ``agent_type`` (e.g. ``"Explore"``), ``session_id``, and
    ``cwd`` — no ``tool_input``, no description, no ``tool_use_id``.
    The task description comes from the *preceding* ``PreToolUse``
    event with ``tool_name == "Agent"``, which ``handle_pre_agent``
    stashed in the ``pending_subagent_tasks`` table.

    Stores the raw Claude Code hex ID (``agent_id`` from the event,
    falling back to ``subagent_id`` for legacy fixtures) as
    ``claude_agent_id`` on the child record so that subsequent
    PreToolUse/PostToolUse hooks can map it back.

    Deduplicates by ``claude_agent_id`` — background agents
    (``run_in_background: true``) fire SubagentStart twice with the
    same hex ID but would otherwise get two different sequence numbers.

    **Stage 6 (HA Spawner)**: After registering the sub-agent, calls
    ``consume_pending_spawn`` to mark any matching pending spawn record
    (from the parent's ``spawn_subagent`` call) as ``registered``. This
    lets the parent track spawn intent → sub-agent registration lifecycle.
    """
    from coordinationhub.pending_tasks import consume_pending_task
    from coordinationhub.spawner import consume_pending_spawn

    engine = _get_engine(event.get("cwd", "."))
    try:
        session_id = event.get("session_id", "")
        parent_id = _session_agent_id(session_id)
        _ensure_registered(engine, parent_id)

        # Real events use ``agent_id``; legacy fixtures use ``subagent_id``.
        raw_claude_id = event.get("agent_id") or event.get("subagent_id")
        subagent_type = _subagent_type(event)

        # Pop the pending task stashed by handle_pre_agent (if any).
        # We consume regardless of dedup so a re-fired SubagentStart for
        # a background agent picks up the same task the first run got.
        pending = consume_pending_task(
            engine._connect, session_id, subagent_type,
        )
        pending_desc = (pending or {}).get("description") or ""

        # Legacy fallback: some unit-test fixtures still put the
        # description in ``tool_input.description`` (fake shape). Use
        # that if no pending task was found.
        if not pending_desc:
            pending_desc = event.get("tool_input", {}).get("description", "") or ""

        # Dedup: if an agent with this claude_agent_id already exists, heartbeat it
        if raw_claude_id:
            existing = engine.find_agent_by_claude_id(raw_claude_id)
            if existing:
                engine.heartbeat(existing)
                if pending_desc:
                    engine.update_agent_status(existing, current_task=pending_desc)
                return None

        child_id = _subagent_id(parent_id, event, engine=engine)

        # Register with parent hierarchy AND the raw Claude Code ID mapping
        agents = engine.list_agents(active_only=True)
        if any(a["agent_id"] == child_id for a in agents.get("agents", [])):
            engine.heartbeat(child_id)
        else:
            engine.register_agent(
                child_id,
                parent_id=parent_id,
                claude_agent_id=raw_claude_id,
            )

        if pending_desc:
            engine.update_agent_status(child_id, current_task=pending_desc)

        # Stage 6: consume pending spawn so parent sees registered status
        try:
            consume_pending_spawn(engine._connect, parent_id, subagent_type)
        except Exception:
            pass  # spawner tracking is best-effort
    finally:
        engine.close()
    return None


def handle_subagent_stop(event: dict) -> dict | None:
    """Deregister child agent when subagent finishes.

    Resolves the agent's ``hub.cc.*`` ID from the raw Claude hex ID
    stored during SubagentStart, then marks it as ``stopped``.
    """
    engine = _get_engine(event.get("cwd", "."))
    try:
        # Look up the hub.cc.* child ID from the raw Claude hex ID
        child_id = _resolve_agent_id(event, engine=engine)

        # If resolve returned the session root (no subagent_id in event),
        # fall back to _subagent_id derivation
        parent_id = _session_agent_id(event.get("session_id", ""))
        if child_id == parent_id:
            child_id = _subagent_id(parent_id, event, engine=engine)

        try:
            engine.deregister_agent(child_id)
        except Exception:
            pass
    finally:
        engine.close()
    return None


def handle_session_end(event: dict) -> dict | None:
    """Deregister session agent, release locks, and return session summary."""
    engine = _get_engine(event.get("cwd", "."))
    try:
        agent_id = _session_agent_id(event.get("session_id", ""))

        # Collect summary counts before teardown
        summary_parts: list[str] = []
        try:
            status = engine.status()
            agents_count = status.get("registered_agents", 0)
            locks_count = status.get("active_locks", 0)
            conflicts_count = status.get("recent_conflicts", 0)
            notifs_count = status.get("pending_notifications", 0)
            summary_parts = [
                f"{agents_count} agents tracked",
                f"{locks_count} locks held",
                f"{conflicts_count} conflicts",
                f"{notifs_count} notifications",
            ]
        except Exception:
            pass

        try:
            engine.release_agent_locks(agent_id)
            engine.deregister_agent(agent_id)
        except Exception:
            pass

        if summary_parts:
            summary = ", ".join(summary_parts)
            return {
                "hookSpecificOutput": {
                    "hookEventName": "SessionEnd",
                    "additionalContext": f"[CoordinationHub] Session summary: {summary}",
                }
            }
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

    # Optional: capture raw events for contract test fixtures
    if os.environ.get("COORDINATIONHUB_CAPTURE_EVENTS"):
        _save_event_snapshot(event)

    hook_event = event.get("hook_event_name", "")
    tool_name = event.get("tool_name", "")

    try:
        result = None

        if hook_event == "SessionStart":
            result = handle_session_start(event)

        elif hook_event == "UserPromptSubmit":
            result = handle_user_prompt_submit(event)

        elif hook_event == "PreToolUse" and tool_name in ("Write", "Edit"):
            result = handle_pre_write(event)

        elif hook_event == "PreToolUse" and tool_name == "Agent":
            result = handle_pre_agent(event)

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
    except Exception as exc:
        _log_error(hook_event or "unknown", exc)


if __name__ == "__main__":
    main()
