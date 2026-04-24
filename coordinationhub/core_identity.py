"""IdentityMixin — agent lifecycle and lineage management.

Expects the host class to provide:
    self._connect()     — callable returning a sqlite3 connection
    self._storage        — CoordinationStorage instance (provides project_root)
    self._graph          — loaded graph (or None)
    self._build_context_bundle(agent_id, parent_id) — host method

Delegates to: agent_registry (agent_registry.py)
"""

from __future__ import annotations

import time
from typing import Any

from . import agent_registry as _ar
from .plugins.graph import graphs as _g
from . import scan as _scan
from .context import build_context_bundle


class IdentityMixin:
    """Agent identity, registration, heartbeat, lineage, and ID generation."""

    DEFAULT_PORT = 9877
    HEARTBEAT_INTERVAL = 30

    # ------------------------------------------------------------------ #
    # Lifecycle helpers (host-provided)
    # ------------------------------------------------------------------ #

    def _build_context_bundle(self, agent_id: str, parent_id: str | None = None) -> dict[str, Any]:
        """Build the context bundle returned on agent registration.

        T7.29: the inline SELECTs in ``build_context_bundle`` now open a
        read-only connection (``self._storage.read_only_connection``)
        instead of borrowing the writer pool. Bundle reads are
        purely side-effect-free; pinning a writer slot for them
        stalled concurrent registrations and lock acquires.
        """
        return build_context_bundle(
            connect_fn=self._connect,
            agent_id=agent_id,
            parent_id=parent_id,
            project_root=str(self._storage.effective_worktree_root),
            graph_getter=_g.get_graph,
            list_agents_fn=_ar.list_agents,
            default_port=self.DEFAULT_PORT,
            descendants_fn=lambda: _ar.get_descendants_status(self._connect, agent_id),
            read_connect_fn=self._storage.read_only_connection,
        )

    # ------------------------------------------------------------------ #
    # Agent ID generation
    # ------------------------------------------------------------------ #

    def generate_agent_id(self, parent_id: str | None = None) -> str:
        """Generate a unique agent ID. Thread-safe via in-memory counters."""
        return self._storage.generate_agent_id(parent_id)

    # ------------------------------------------------------------------ #
    # Registration
    # ------------------------------------------------------------------ #

    def register_agent(
        self,
        agent_id: str,
        parent_id: str | None = None,
        graph_agent_id: str | None = None,
        worktree_root: str | None = None,
        raw_ide_id: str | None = None,
        ide_vendor: str | None = None,
    ) -> dict[str, Any]:
        """Register a new agent and return its context bundle.

        Returns an error bundle (``{"registered": False, "reason": "collision", ...}``)
        if an active agent already exists at ``agent_id`` under a different PID
        (T1.2 cross-process collision guard).
        """
        # T6.16: fall back to the storage-level ``effective_worktree_root``
        # which was captured at engine init. Prior code re-read ``os.getcwd()``
        # per call, so a chdir mid-run would give different agents different
        # roots.
        worktree = worktree_root or str(self._storage.effective_worktree_root)
        ar_result = _ar.register_agent(
            self._connect, agent_id, worktree, parent_id,
            raw_ide_id=raw_ide_id, ide_vendor=ide_vendor,
        )
        if not ar_result.get("registered", True):
            # Collision with a live agent in another process — do NOT publish
            # an agent.registered event or write lineage.
            return ar_result

        # T1.20: lineage insert moved into the primitive so it joins the
        # agent-row INSERT in a single transaction. The engine now only
        # publishes the event and handles optional graph/responsibilities.
        self._publish_event("agent.registered", {"agent_id": agent_id, "parent_id": parent_id})
        if graph_agent_id:
            graph = _g.get_graph()
            if graph:
                agent_def = graph.agent(graph_agent_id)
                if agent_def:
                    _scan.store_responsibilities(
                        self._connect,
                        agent_id,
                        graph_agent_id,
                        agent_def.get("role", ""),
                        agent_def.get("model", ""),
                        agent_def.get("responsibilities", []),
                    )
        return self._build_context_bundle(agent_id, parent_id)

    def heartbeat(self, agent_id: str) -> dict[str, Any]:
        """Send an agent heartbeat. Updates last_heartbeat timestamp.

        T1.18: propagates the primitive's ``reason`` field on failure
        (e.g. ``not_registered``, ``agent_stopped``) so the caller can
        distinguish "need to re-register" from "lost race".
        """
        result = _ar.heartbeat(self._connect, agent_id)
        out = {
            "updated": result.get("updated", False),
            "next_heartbeat_in": self.HEARTBEAT_INTERVAL,
        }
        if not out["updated"] and "reason" in result:
            out["reason"] = result["reason"]
        return out

    def deregister_agent(self, agent_id: str) -> dict[str, Any]:
        """Deregister an agent, release its locks, and orphan its children."""
        result = _ar.deregister_agent(self._connect, agent_id)
        # Cross-mixin call via MRO: resolves to the engine's facade for
        # ``release_agent_locks`` which delegates to ``self._locking``
        # (T6.22 — commit extracting :class:`Locking`).
        lock_result = self.release_agent_locks(agent_id)
        result["locks_released"] = lock_result.get("released", 0)
        self._publish_event("agent.deregistered", {"agent_id": agent_id})
        return result

    def prune_stopped_agents(
        self, retention_seconds: float = 7 * 24 * 3600.0,
    ) -> dict[str, Any]:
        """Delete agent rows stopped for longer than ``retention_seconds``.

        T1.17 tail: pairs with ``reap_stale_agents`` — the reaper transitions
        rows to ``status='stopped'``; this hard-deletes rows that have been in
        that state long enough that post-mortem lookup is unlikely. Rows with
        active children are preserved so live agents don't lose their parent.
        """
        return _ar.prune_stopped_agents(self._connect, retention_seconds)

    def list_agents(
        self, active_only: bool = True, stale_timeout: float = 600.0,
        include_stale: bool = False,
    ) -> dict[str, Any]:
        """List registered agents, optionally filtered to active only.

        T1.17: when ``active_only`` is True the result excludes rows whose
        heartbeat is older than ``stale_timeout``. Pass
        ``include_stale=True`` to recover every row with status='active'
        regardless of heartbeat age (useful for dashboards surfacing
        stuck agents).
        """
        agents = _ar.list_agents(
            self._connect, active_only, stale_timeout, include_stale=include_stale,
        )
        return {"agents": agents}

    def get_agent_relations(self, agent_id: str, mode: str = "lineage") -> dict[str, Any]:
        """Get ancestors/descendants (mode='lineage') or siblings (mode='siblings') of an agent."""
        if mode == "siblings":
            siblings = _ar.get_siblings(self._connect, agent_id)
            return {"mode": "siblings", "agent_id": agent_id, "siblings": siblings}
        return _ar.get_lineage(self._connect, agent_id)

    def find_agent_by_raw_ide_id(
        self, raw_ide_id: str, ide_vendor: str | None = None,
    ) -> str | None:
        """Look up a hub agent_id by the raw IDE-specific agent ID.

        T3.12: pass ``ide_vendor`` to namespace the lookup so
        colliding raw ids from different IDEs don't cross-match.
        """
        return _ar.find_agent_by_raw_ide_id(
            self._connect, raw_ide_id, ide_vendor=ide_vendor,
        )