"""WorkIntentMixin — cooperative work intent board.

Expects the host class to provide:
    self._connect() — callable returning a sqlite3 connection
    self._storage.project_root — for path normalization

Delegates to: work_intent (work_intent.py)
"""

from __future__ import annotations

from typing import Any

from . import work_intent as _wi
from .paths import normalize_path


class WorkIntentMixin:
    """Cooperative work intent declarations before lock acquisition."""

    def _normalize_intent_path(self, document_path: str) -> str:
        """Route `document_path` through the engine's project-root normalizer."""
        return normalize_path(document_path, self._storage.project_root)

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
            norm_path = self._normalize_intent_path(document_path)
            return _wi.upsert_intent(self._connect, agent_id, norm_path, intent, ttl)
        if action == "get":
            intents = _wi.get_live_intents(self._connect, agent_id)
            return {"intents": intents, "count": len(intents)}
        if action == "clear":
            norm_path = (
                self._normalize_intent_path(document_path) if document_path else None
            )
            return _wi.clear_intent(self._connect, agent_id, document_path=norm_path)
        return {"error": f"Unknown action: {action!r}"}

    def declare_work_intent(
        self,
        agent_id: str,
        document_path: str,
        intent: str,
        ttl: float = 60.0,
    ) -> dict[str, Any]:
        """Declare intent to work on a file before acquiring a lock.

        T1.16: ``document_path`` is normalized via the engine's project
        root so ``./foo.py`` and ``foo.py`` collapse to the same key.
        An agent can now declare intent on multiple files at once —
        calling this with a different ``document_path`` no longer
        erases the prior intent.
        """
        norm_path = self._normalize_intent_path(document_path)
        return _wi.upsert_intent(self._connect, agent_id, norm_path, intent, ttl)

    def get_work_intents(self, agent_id: str | None = None) -> dict[str, Any]:
        """Get all live work intents, optionally filtered by agent."""
        intents = _wi.get_live_intents(self._connect, agent_id)
        return {"intents": intents, "count": len(intents)}

    def clear_work_intent(
        self, agent_id: str, document_path: str | None = None,
    ) -> dict[str, Any]:
        """Clear an agent's declared work intent.

        T1.16: when ``document_path`` is supplied only that specific
        intent is cleared; omitting it clears every live intent for the
        agent.
        """
        norm_path = (
            self._normalize_intent_path(document_path) if document_path else None
        )
        return _wi.clear_intent(self._connect, agent_id, document_path=norm_path)

    def prune_work_intents(self) -> dict[str, Any]:
        """Delete expired intent rows. Callable from the engine so operators
        don't need to dig into the primitive.
        """
        return _wi.prune_expired_intents(self._connect)