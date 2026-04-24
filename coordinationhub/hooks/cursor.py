#!/usr/bin/env python3
"""CoordinationHub hook adapter for Cursor.

Reads generic hook event JSON from stdin and delegates to BaseHook.

Expected event shape:
  {
    "hook_event_name": "SessionStart|PreToolUse|PostToolUse|SubagentStart|SubagentStop|SessionEnd",
    "session_id": "...",
    "cwd": "...",
    "tool_name": "Write|Edit|Agent|...",
    "tool_input": {"file_path": "...", "description": "..."},
    "agent_id": "...",
    "agent_type": "Explore",
    "prompt": "..."
  }

Cursor does not currently provide a native hook system like the stdio adapter.
This adapter is designed to be called by:
  - A wrapper script around Cursor tool invocations
  - A sidecar file watcher on a Cursor event log
  - Manual invocation during custom integrations
"""

from __future__ import annotations

import json
import sys

from coordinationhub.hooks.base import BaseHook


def _to_generic_response(response):
    """Map Claude-Code hookSpecificOutput to a flat decision/reason dict.

    T3.13: Cursor and Kimi CLI don't honour Claude's
    ``hookSpecificOutput`` / ``permissionDecision`` keys, so relaying the
    raw Claude shape through is a no-op for their wrappers. Flatten to
    a vendor-neutral response the wrapper can branch on directly.
    """
    if not response:
        return None
    hsp = response.get("hookSpecificOutput", response)
    if not isinstance(hsp, dict):
        return None
    decision = hsp.get("permissionDecision")
    out = {}
    if decision is not None:
        out["decision"] = decision
    reason = hsp.get("permissionDecisionReason")
    if reason:
        out["reason"] = reason
    additional = hsp.get("additionalContext")
    if additional:
        out["message"] = additional
    event_name = hsp.get("hookEventName")
    if event_name:
        out["event"] = event_name
    return out or None


class CursorHook(BaseHook):
    """Cursor adapter over BaseHook."""

    IDE_PREFIX = "cursor"

    @classmethod
    def from_cwd(cls, cwd: str) -> "CursorHook":
        return cls(project_root=cwd)

    @staticmethod
    def _raw_agent_id(event: dict) -> str | None:
        return event.get("agent_id")

    @staticmethod
    def _agent_type(event: dict) -> str:
        return event.get("agent_type") or "agent"

    def translate_output(self, response):
        """T3.13: reshape BaseHook's Claude-specific response for Cursor.

        Cursor doesn't have a native hook protocol yet, so this adapter
        targets wrapper scripts. The emitted shape is a flat, easy-to-parse
        dict::

            {"decision": "allow"|"deny", "reason": "...", "message": "..."}

        Pass-through returns None when BaseHook produced nothing.
        """
        return _to_generic_response(response)


def main() -> None:
    # T3.1: mirror stdio_adapter.main's fail-open contract. Every stage
    # (read, parse, construct hook, dispatch) is wrapped so an exception
    # is logged and the IDE sees a silent success — never a traceback.
    from coordinationhub.hooks.base import _log_hook_error

    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        event = json.loads(raw)
    except (json.JSONDecodeError, OSError):
        return

    # T3.1: construct the hook inside a guarded block. If the engine
    # can't start (DB stuck, schema migration failure), log and exit
    # silently — do NOT let the cascade hit the IDE.
    hook = None
    try:
        hook = CursorHook.from_cwd(event.get("cwd", "."))
    except Exception as exc:
        _log_hook_error("cursor.hook_init", exc)
        return

    try:
        hook_event = event.get("hook_event_name", "")
        tool_name = event.get("tool_name", "")
        session_id = event.get("session_id", "")
        result = None

        if hook_event == "SessionStart":
            hook.on_session_start(session_id)

        elif hook_event == "UserPromptSubmit":
            hook.on_user_prompt(session_id, event.get("prompt", ""))

        elif hook_event == "PreToolUse":
            tool_input = event.get("tool_input", {})
            if tool_name in ("Write", "Edit"):
                result = hook.on_pre_write(
                    session_id, tool_input.get("file_path", ""), hook._raw_agent_id(event)
                )
            elif tool_name == "Agent":
                hook.stash_subagent_description(
                    session_id=session_id,
                    tool_use_id=event.get("tool_use_id", ""),
                    subagent_type=tool_input.get("subagent_type", ""),
                    description=tool_input.get("description", "") or "",
                    prompt=tool_input.get("prompt", "") or "",
                )

        elif hook_event == "PostToolUse":
            tool_input = event.get("tool_input", {})
            if tool_name in ("Write", "Edit"):
                hook.on_post_write(
                    session_id, tool_input.get("file_path", ""), hook._raw_agent_id(event)
                )
            elif "index" in tool_name.lower():
                paths = tool_input.get("paths", [])
                doc_path = tool_input.get("document_path") or tool_input.get("path")
                if doc_path:
                    paths.insert(0, str(doc_path))
                if paths:
                    hook.on_post_index(session_id, [str(p) for p in paths], hook._raw_agent_id(event))

        elif hook_event == "SubagentStart":
            hook.on_subagent_start(
                session_id,
                hook._raw_agent_id(event),
                hook._agent_type(event),
                event.get("tool_input", {}).get("description") or "",
            )

        elif hook_event == "SubagentStop":
            hook.on_subagent_stop(session_id, hook._raw_agent_id(event), hook._agent_type(event))

        elif hook_event == "SessionEnd":
            result = hook.on_session_end(session_id)

        # T3.13: reshape Claude's hookSpecificOutput into Cursor's flat
        # response shape so wrapper scripts don't have to parse nested
        # Claude-specific keys.
        translated = hook.translate_output(result)
        if translated:
            json.dump(translated, sys.stdout)

    except ImportError:
        pass
    except Exception as exc:
        # T3.1: any other failure is logged and swallowed so the IDE
        # sees no traceback.
        _log_hook_error(
            f"cursor.{event.get('hook_event_name', 'unknown')}", exc,
        )
    finally:
        # T3.1: guard against UnboundLocalError when construction failed;
        # hook.close() is itself fail-safe.
        if hook is not None:
            hook.close()


if __name__ == "__main__":
    main()
