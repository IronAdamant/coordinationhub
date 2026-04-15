"""WorkIntentMixin — cooperative work intent board.

Expects the host class to provide:
    self._connect() — callable returning a sqlite3 connection

Delegates to: work_intent (work_intent.py)
"""

from __future__ import annotations

from typing import Any

from . import work_intent as _wi


class WorkIntentMixin:
    """Cooperative work intent declarations before lock acquisition."""

    def manage_work_intents(
        self,
        action: str,
        agent_id: str,
        document_path: str | None = None,
        intent: str | None = None,
        ttl: float = 60.0,
    ) -> dict[str, Any]:
        """Unified work intent management: declare | get | clear."""
        if action == "declare":
            if not document_path or not intent:
                return {"error": "document_path and intent are required for declare"}
            return _wi.upsert_intent(self._connect, agent_id, document_path, intent, ttl)
        if action == "get":
            intents = _wi.get_live_intents(self._connect, agent_id)
            return {"intents": intents, "count": len(intents)}
        if action == "clear":
            return _wi.clear_intent(self._connect, agent_id)
        return {"error": f"Unknown action: {action!r}"}

    def declare_work_intent(
        self,
        agent_id: str,
        document_path: str,
        intent: str,
        ttl: float = 60.0,
    ) -> dict[str, Any]:
        """Declare intent to work on a file before acquiring a lock."""
        return _wi.upsert_intent(self._connect, agent_id, document_path, intent, ttl)

    def get_work_intents(self, agent_id: str | None = None) -> dict[str, Any]:
        """Get all live work intents, optionally filtered by agent."""
        intents = _wi.get_live_intents(self._connect, agent_id)
        return {"intents": intents, "count": len(intents)}

    def clear_work_intent(self, agent_id: str) -> dict[str, Any]:
        """Clear an agent's declared work intent."""
        return _wi.clear_intent(self._connect, agent_id)