"""Base hook abstraction for CoordinationHub.

Provides IDE-agnostic coordination logic:
  - Engine lifecycle
  - Agent registration and ID resolution
  - File locking and ownership
  - Change notifications
  - Sub-agent pending-task correlation

IDE-specific adapters (Kimi CLI, Cursor, etc.) subclass BaseHook
and map their native event shapes to these methods.
"""

from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from typing import Any


# T2.9: session_id is supplied by the IDE and embedded into a derived
# agent_id that becomes a DB primary key. Accepting arbitrary content
# (non-ASCII, shell specials, path separators) produced hard-to-debug
# collisions and was a latent injection vector. Restrict the 12-char
# prefix to the IDE-safe alphabet; hash anything else to a stable short
# digest so the agent_id remains deterministic across hook invocations.
_SESSION_ID_SAFE = re.compile(r"^[A-Za-z0-9_-]+$")


def _sanitize_session_id(session_id: str | None) -> str:
    """Return a DB-safe 12-char tag derived from ``session_id``.

    Safe session ids pass through truncated to 12 chars. Unsafe inputs
    (empty, None, or containing anything outside ``[A-Za-z0-9_-]``) are
    hashed with SHA-256 and the first 12 hex chars are returned — still
    deterministic per input but guaranteed to be safe.
    """
    if not session_id:
        return "unknown"
    if _SESSION_ID_SAFE.match(session_id):
        return session_id[:12]
    digest = hashlib.sha256(session_id.encode("utf-8", errors="replace")).hexdigest()
    return digest[:12]


# T2.1: prompts are stored into agents.current_task and then surfaced on
# the dashboard (exposed over HTTP to any local process). Unfiltered
# prompt text often contains API keys, tokens, PII, or internal URLs.
# Redact the common credential-shaped strings before storing.
_REDACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Anthropic API keys (sk-ant-...) and OpenAI keys (sk-...) and
    # generic hex/base64 tokens passed as Bearer.
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._\-]+\b", re.IGNORECASE), "Bearer [REDACTED]"),
    # GitHub personal access tokens
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"), "[REDACTED_GH_PAT]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "[REDACTED_GH_PAT]"),
    # AWS-looking keys
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY_ID]"),
    # Email addresses
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]"),
    # Long all-lowercase hex strings (likely credentials or hashes)
    (re.compile(r"\b[a-f0-9]{32,}\b"), "[REDACTED_HEX]"),
)


def _redact_prompt(text: str) -> str:
    """Return *text* with credential-shaped substrings replaced.

    T2.1: applied to prompts before they land in ``current_task`` so the
    dashboard can't accidentally expose secrets that happened to appear
    in a user prompt. Conservative — only matches well-known patterns;
    unknown shapes pass through unchanged.
    """
    redacted = text
    for pattern, placeholder in _REDACT_PATTERNS:
        redacted = pattern.sub(placeholder, redacted)
    return redacted


class BaseHook:
    """IDE-agnostic hook protocol for CoordinationHub."""

    IDE_PREFIX = "ide"

    def __init__(self, project_root: str | None = None) -> None:
        self._engine = self._create_engine(project_root)
        self._engine.start()

    @classmethod
    def _create_engine(cls, project_root: str | None):
        from coordinationhub.core import CoordinationEngine

        return CoordinationEngine(
            project_root=Path(project_root) if project_root else None,
        )

    @property
    def engine(self):
        return self._engine

    def close(self) -> None:
        self._engine.close()

    # ------------------------------------------------------------------ #
    # Agent IDs
    # ------------------------------------------------------------------ #

    def session_agent_id(self, session_id: str) -> str:
        short = _sanitize_session_id(session_id)
        return f"hub.{self.IDE_PREFIX}.{short}"

    def resolve_agent_id(self, session_id: str, raw_ide_id: str | None = None) -> str:
        if raw_ide_id:
            mapped = self._engine.find_agent_by_raw_ide_id(raw_ide_id)
            if mapped:
                return mapped
            return raw_ide_id
        return self.session_agent_id(session_id)

    def _ensure_registered(self, agent_id: str, parent_id: str | None = None) -> None:
        agents = self._engine.list_agents(active_only=True)
        if any(a["agent_id"] == agent_id for a in agents.get("agents", [])):
            self._engine.heartbeat(agent_id)
            return
        self._engine.register_agent(agent_id, parent_id=parent_id)

    def subagent_id(
        self,
        parent_id: str,
        agent_type: str,
        tool_use_id: str = "",
        seq: int | None = None,
    ) -> str:
        if tool_use_id:
            return f"{parent_id}.{agent_type}.{tool_use_id[:6]}"
        if seq is None:
            seq = 0
            try:
                agents = self._engine.list_agents(active_only=False)
                prefix = f"{parent_id}.{agent_type}."
                existing = [
                    a["agent_id"] for a in agents.get("agents", [])
                    if a["agent_id"].startswith(prefix)
                ]
                seq = len(existing)
            except Exception:
                pass
        return f"{parent_id}.{agent_type}.{seq}"

    # ------------------------------------------------------------------ #
    # Session lifecycle
    # ------------------------------------------------------------------ #

    def on_session_start(self, session_id: str) -> None:
        agent_id = self.session_agent_id(session_id)
        self._ensure_registered(agent_id)

    def on_session_end(self, session_id: str) -> dict[str, Any] | None:
        agent_id = self.session_agent_id(session_id)
        summary_parts: list[str] = []
        try:
            status = self._engine.status()
            summary_parts = [
                f"{status.get('registered_agents', 0)} agents tracked",
                f"{status.get('active_locks', 0)} locks held",
                f"{status.get('recent_conflicts', 0)} conflicts",
                f"{status.get('pending_notifications', 0)} notifications",
            ]
        except Exception:
            pass

        try:
            self._engine.release_agent_locks(agent_id)
            self._engine.deregister_agent(agent_id)
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
        return None

    def on_user_prompt(self, session_id: str, prompt: str) -> None:
        prompt = prompt.strip()
        if not prompt:
            return
        # T2.1: redact credential-shaped substrings BEFORE truncating so
        # a secret living past the 120-char cutoff can't sneak through
        # at full length.
        prompt = _redact_prompt(prompt)
        summary = prompt if len(prompt) <= 120 else prompt[:117] + "..."
        summary = " ".join(summary.split())
        agent_id = self.session_agent_id(session_id)
        self._ensure_registered(agent_id)
        self._engine.update_agent_status(agent_id, current_task=summary)

    # ------------------------------------------------------------------ #
    # File locking
    # ------------------------------------------------------------------ #

    def on_pre_write(
        self, session_id: str, file_path: str, raw_ide_id: str | None = None,
    ) -> dict[str, Any] | None:
        agent_id = self.resolve_agent_id(session_id, raw_ide_id)
        self._ensure_registered(agent_id)
        self._engine.reap_expired_locks(agent_grace_seconds=120.0)

        result = self._engine.acquire_lock(file_path, agent_id, ttl=300.0)
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
            return None

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

    def on_post_write(
        self, session_id: str, file_path: str, raw_ide_id: str | None = None,
    ) -> None:
        agent_id = self.resolve_agent_id(session_id, raw_ide_id)
        try:
            self._engine.notify_change(file_path, "modified", agent_id)
        except Exception:
            pass
        try:
            self._engine.claim_file_ownership(file_path, agent_id)
        except Exception:
            pass
        try:
            self._engine.release_lock(file_path, agent_id)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Sub-agent lifecycle
    # ------------------------------------------------------------------ #

    def stash_subagent_description(
        self,
        session_id: str,
        tool_use_id: str,
        subagent_type: str,
        description: str,
        prompt: str = "",
    ) -> None:
        from coordinationhub.pending_tasks import stash_pending_task
        stash_pending_task(
            self._engine._connect,
            tool_use_id=tool_use_id,
            session_id=session_id,
            subagent_type=subagent_type,
            description=description,
            prompt=prompt,
        )

    def on_subagent_start(
        self,
        session_id: str,
        raw_ide_id: str | None,
        agent_type: str,
        description: str | None = None,
    ) -> None:
        from coordinationhub.pending_tasks import consume_pending_task

        parent_id = self.session_agent_id(session_id)
        self._ensure_registered(parent_id)

        pending = consume_pending_task(
            self._engine._connect, session_id, agent_type,
        )
        pending_desc = (pending or {}).get("description") or description or ""

        # Dedup by raw IDE ID
        if raw_ide_id:
            existing = self._engine.find_agent_by_raw_ide_id(raw_ide_id)
            if existing:
                self._engine.heartbeat(existing)
                if pending_desc:
                    self._engine.update_agent_status(existing, current_task=pending_desc)
                try:
                    self._engine.report_subagent_spawned(parent_id, agent_type, existing, source=self.IDE_PREFIX)
                except Exception:
                    pass
                return

        child_id = self.subagent_id(parent_id, agent_type)
        agents = self._engine.list_agents(active_only=True)
        if any(a["agent_id"] == child_id for a in agents.get("agents", [])):
            self._engine.heartbeat(child_id)
        else:
            kwargs: dict[str, Any] = {"parent_id": parent_id}
            if raw_ide_id:
                kwargs["raw_ide_id"] = raw_ide_id
            self._engine.register_agent(child_id, **kwargs)

        if pending_desc:
            self._engine.update_agent_status(child_id, current_task=pending_desc)

        try:
            self._engine.report_subagent_spawned(parent_id, agent_type, child_id, source=self.IDE_PREFIX)
        except Exception:
            pass

    def on_subagent_stop(
        self, session_id: str, raw_ide_id: str | None, agent_type: str,
    ) -> None:
        child_id = self.resolve_agent_id(session_id, raw_ide_id)
        parent_id = self.session_agent_id(session_id)
        if child_id == parent_id:
            child_id = self.subagent_id(parent_id, agent_type)
        try:
            self._engine.deregister_agent(child_id)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Tool bridges (optional)
    # ------------------------------------------------------------------ #

    def on_post_index(
        self, session_id: str, paths: list[str], raw_ide_id: str | None = None,
    ) -> None:
        agent_id = self.resolve_agent_id(session_id, raw_ide_id)
        for p in paths:
            try:
                self._engine.notify_change(p, "indexed", agent_id)
            except Exception:
                pass

    def on_task_claim(
        self, session_id: str, task: str, raw_ide_id: str | None = None,
    ) -> None:
        agent_id = self.resolve_agent_id(session_id, raw_ide_id)
        self._ensure_registered(agent_id)
        try:
            self._engine.update_agent_status(agent_id, current_task=task)
        except Exception:
            pass
