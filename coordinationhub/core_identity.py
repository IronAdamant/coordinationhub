"""IdentityMixin — agent lifecycle and lineage management.

Expects the host class to provide:
    self._connect()     — callable returning a sqlite3 connection
    self._storage        — CoordinationStorage instance (provides project_root)
    self._graph          — loaded graph (or None)
    self._build_context_bundle(agent_id, parent_id) — host method

Delegates to: agent_registry (agent_registry.py)
"""

from __future__ import annotations

import os
import time
from typing import Any

from . import agent_registry as _ar
from . import graphs as _g
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
        """Build the context bundle returned on agent registration."""
        return build_context_bundle(
            connect_fn=self._connect,
            agent_id=agent_id,
            parent_id=parent_id,
            project_root=str(self._storage.project_root) if self._storage.project_root else os.getcwd(),
            graph_getter=_g.get_graph,
            list_agents_fn=_ar.list_agents,
            default_port=self.DEFAULT_PORT,
            descendants_fn=lambda: _ar.get_descendants_status(self._connect, agent_id),
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
        claude_agent_id: str | None = None,
    ) -> dict[str, Any]:
        """Register a new agent and return its context bundle."""
        worktree = worktree_root or (
            str(self._storage.project_root) if self._storage.project_root else os.getcwd()
        )
        _ar.register_agent(self._connect, agent_id, worktree, parent_id, claude_agent_id=claude_agent_id)
        if parent_id is not None:
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO lineage (parent_id, child_id, spawned_at) VALUES (?, ?, ?)",
                    (parent_id, agent_id, time.time()),
                )
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
        """Send an agent heartbeat. Updates last_heartbeat timestamp."""
        updated = _ar.heartbeat(self._connect, agent_id)
        return {"updated": updated.get("updated", False), "next_heartbeat_in": self.HEARTBEAT_INTERVAL}

    def deregister_agent(self, agent_id: str) -> dict[str, Any]:
        """Deregister an agent, release its locks, and orphan its children."""
        result = _ar.deregister_agent(self._connect, agent_id)
        # Cross-mixin call via MRO: LockingMixin.release_agent_locks is on the host
        lock_result = self.release_agent_locks(agent_id)
        result["locks_released"] = lock_result.get("released", 0)
        return result

    def list_agents(
        self, active_only: bool = True, stale_timeout: float = 600.0,
    ) -> dict[str, Any]:
        """List registered agents, optionally filtered to active only."""
        agents = _ar.list_agents(self._connect, active_only, stale_timeout)
        return {"agents": agents}

    def get_lineage(self, agent_id: str) -> dict[str, Any]:
        """Get ancestors and descendants of an agent."""
        return _ar.get_lineage(self._connect, agent_id)

    def get_siblings(self, agent_id: str) -> dict[str, Any]:
        """Get agents that share the same parent."""
        siblings = _ar.get_siblings(self._connect, agent_id)
        return {"siblings": siblings}

    def find_agent_by_claude_id(self, claude_agent_id: str) -> str | None:
        """Look up a hub.cc.* agent_id by the raw Claude Code hex ID."""
        return _ar.find_agent_by_claude_id(self._connect, claude_agent_id)