#!/usr/bin/env python3
"""CoordinationHub hook adapter for Kimi CLI.

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

Kimi CLI does not currently provide a native hook system like the stdio adapter.
This adapter is designed to be called by:
  - A wrapper script around Kimi CLI tool invocations
  - A sidecar file watcher on a Kimi event log
  - Manual invocation during custom integrations
"""

from __future__ import annotations

import json
import sys

from coordinationhub.hooks.base import BaseHook


class KimiCliHook(BaseHook):
    """Kimi CLI adapter over BaseHook."""

    IDE_PREFIX = "kimi"

    @classmethod
    def from_cwd(cls, cwd: str) -> "KimiCliHook":
        return cls(project_root=cwd)

    @staticmethod
    def _raw_agent_id(event: dict) -> str | None:
        return event.get("agent_id")

    @staticmethod
    def _agent_type(event: dict) -> str:
        return event.get("agent_type") or "agent"


def main() -> None:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        event = json.loads(raw)
    except (json.JSONDecodeError, OSError):
        return

    # T3.1: fail-open wrapper — construct the hook guarded, then the
    # dispatch block below is also wrapped so any failure is logged
    # and swallowed rather than bubbling up as a traceback to Kimi.
    from coordinationhub.hooks.base import _log_hook_error

    hook = None
    try:
        hook = KimiCliHook.from_cwd(event.get("cwd", "."))
    except Exception as exc:
        _log_hook_error("kimi_cli.hook_init", exc)
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

        if result:
            json.dump(result, sys.stdout)

    except ImportError:
        pass
    except Exception as exc:
        _log_hook_error(
            f"kimi_cli.{event.get('hook_event_name', 'unknown')}", exc,
        )
    finally:
        if hook is not None:
            hook.close()


if __name__ == "__main__":
    main()
