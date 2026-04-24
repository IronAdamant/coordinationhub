"""Identity subsystem — agent registration, heartbeat, lineage, and ID generation.

T6.22 twelfth and FINAL step: extracted out of ``core_identity.IdentityMixin``
into a standalone class. With this extraction the MRO on
:class:`CoordinationEngine` contains *zero* mixins — the engine is now a
pure composition of twelve subsystems (``_spawner``, ``_work_intent``,
``_lease``, ``_dependency``, ``_messaging``, ``_handoff``, ``_change``,
``_task``, ``_visibility``, ``_locking``, ``_broadcast``, ``_identity``).

Per the coupling audit ``IdentityMixin`` had two ``_publish_event`` calls
(``agent.registered``, ``agent.deregistered``), zero ``_hybrid_wait``
calls, and **one** cross-mixin call: ``self.release_agent_locks(...)``
in ``deregister_agent``. Wiring follows the :class:`Broadcast` pattern
(commit ``fb9e200``) — the single cross-subsystem dep is injected as
the :class:`Locking` instance itself and called directly as
``self._locking.release_agent_locks(...)``, bypassing the engine MRO.

Storage access:

* ``effective_worktree_root_getter`` — a closure over
  ``self._storage.effective_worktree_root`` so a replica produced by
  ``read_only_engine`` picks up its own storage without rebinding. Used
  both by ``register_agent`` (worktree fallback) and the internal
  ``_build_context_bundle`` helper.
* ``read_only_connect_fn`` — the storage's ``read_only_connection``,
  threaded in so ``_build_context_bundle`` reads the bundle from the
  read replica (T7.29 behaviour preserved: registration bundle reads
  don't hold a writer-pool slot).

Constants ``DEFAULT_PORT`` and ``HEARTBEAT_INTERVAL`` are no longer
declared on this subsystem — the canonical home is the engine class
body (``CoordinationEngine.DEFAULT_PORT`` / ``HEARTBEAT_INTERVAL``) so
``engine.DEFAULT_PORT`` access keeps working. Their values are passed
in as constructor args (``default_port``, ``heartbeat_interval``).

The ``_build_context_bundle`` helper — previously shared with the host
class so both the engine and the mixin could call it — is now a private
method on this subsystem (it was only ever called by
``register_agent``, which moved here too).

See commits ``1ee46c6`` (Spawner), ``3d1bd48`` (WorkIntent),
``b4a3e6b`` (Lease), ``d6c8796`` (Dependency), ``d9f84d3`` (Messaging),
``ded641d`` (Handoff), ``e0c21a8`` (Change), ``8182c7a`` (Task),
``64c3ff4`` (Visibility), ``0660785`` (Locking), and ``fb9e200``
(Broadcast) for the eleven prior extractions in this series. This
commit completes T6.22.

Delegates to: agent_registry (agent_registry.py), scan (scan.py),
context (context.py), graphs (plugins/graph/graphs.py),
locking_subsystem.Locking (for ``release_agent_locks`` in
``deregister_agent``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from . import agent_registry as _ar
from . import scan as _scan
from .context import build_context_bundle
from .locking_subsystem import Locking
from .plugins.graph import graphs as _g


class Identity:
    """Agent identity, registration, heartbeat, lineage, and ID generation.

    Constructed by :class:`CoordinationEngine` and exposed as
    ``engine._identity``. The engine keeps facade methods for each
    public operation so the existing tool API is preserved.

    Takes an explicit :class:`Locking` dep so ``deregister_agent`` can
    call ``self._locking.release_agent_locks(...)`` directly instead of
    routing through the engine's MRO / facade. Same pattern as
    :class:`Broadcast` (commit ``fb9e200``).
    """

    def __init__(
        self,
        connect_fn: Callable[[], Any],
        publish_event_fn: Callable[[str, dict[str, Any]], None],
        locking: Locking,
        effective_worktree_root_getter: Callable[[], str],
        read_only_connect_fn: Callable[[], Any],
        generate_agent_id_fn: Callable[[str | None], str],
        default_port: int,
        heartbeat_interval: int,
    ) -> None:
        self._connect = connect_fn
        self._publish_event = publish_event_fn
        self._locking = locking
        self._effective_worktree_root_getter = effective_worktree_root_getter
        self._read_only_connect = read_only_connect_fn
        self._generate_agent_id_fn = generate_agent_id_fn
        self._default_port = default_port
        self._heartbeat_interval = heartbeat_interval

    # ------------------------------------------------------------------ #
    # Context bundle helper (private)
    # ------------------------------------------------------------------ #

    def _build_context_bundle(
        self, agent_id: str, parent_id: str | None = None,
    ) -> dict[str, Any]:
        """Build the context bundle returned on agent registration.

        T7.29: the inline SELECTs in ``build_context_bundle`` open a
        read-only connection (``self._read_only_connect``) instead of
        borrowing the writer pool. Bundle reads are purely
        side-effect-free; pinning a writer slot for them stalled
        concurrent registrations and lock acquires.
        """
        return build_context_bundle(
            connect_fn=self._connect,
            agent_id=agent_id,
            parent_id=parent_id,
            project_root=self._effective_worktree_root_getter(),
            graph_getter=_g.get_graph,
            list_agents_fn=_ar.list_agents,
            default_port=self._default_port,
            descendants_fn=lambda: _ar.get_descendants_status(self._connect, agent_id),
            read_connect_fn=self._read_only_connect,
        )

    # ------------------------------------------------------------------ #
    # Agent ID generation
    # ------------------------------------------------------------------ #

    def generate_agent_id(self, parent_id: str | None = None) -> str:
        """Generate a unique agent ID. Thread-safe via in-memory counters."""
        return self._generate_agent_id_fn(parent_id)

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
        worktree = worktree_root or self._effective_worktree_root_getter()
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
            "next_heartbeat_in": self._heartbeat_interval,
        }
        if not out["updated"] and "reason" in result:
            out["reason"] = result["reason"]
        return out

    def deregister_agent(self, agent_id: str) -> dict[str, Any]:
        """Deregister an agent, release its locks, and orphan its children."""
        result = _ar.deregister_agent(self._connect, agent_id)
        # T6.22: direct cross-subsystem call to :class:`Locking` rather
        # than routing through the engine's MRO — same shape as
        # :class:`Broadcast.wait_for_locks` calling
        # ``self._locking.get_lock_status`` (commit ``fb9e200``).
        lock_result = self._locking.release_agent_locks(agent_id)
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
