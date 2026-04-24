"""WorkIntent subsystem — cooperative work intent board.

T6.22 second step: extracted out of ``core_work_intent.WorkIntentMixin``
into a standalone class. Coupling audit confirmed WorkIntentMixin had
zero cross-mixin method calls, zero ``_publish_event`` calls, and zero
``_hybrid_wait`` calls — it only needed ``_connect`` for DB access and
``_storage.project_root`` for path normalization. Both are now injected
as constructor dependencies (see commit ``1ee46c6`` for the Spawner
precedent). This continues breaking the god-object inheritance chain
on ``CoordinationEngine`` without changing observable behaviour.

Delegates to: work_intent (work_intent.py) for intent DB primitives.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from . import work_intent as _wi
from .paths import normalize_path


class WorkIntent:
    """Cooperative work intent declarations before lock acquisition.

    Constructed by :class:`CoordinationEngine` and exposed as
    ``engine._work_intent``. The engine keeps facade methods for each
    public operation so the existing tool API is preserved.
    """

    def __init__(
        self,
        connect_fn: Callable[[], Any],
        project_root_getter: Callable[[], Path | None],
    ) -> None:
        self._connect = connect_fn
        self._project_root_getter = project_root_getter

    def _normalize_intent_path(self, document_path: str) -> str:
        """Route ``document_path`` through the engine's project-root normalizer."""
        return normalize_path(document_path, self._project_root_getter())

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
