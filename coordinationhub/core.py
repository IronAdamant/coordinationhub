"""CoordinationEngine — host class that composes mixins and subsystems.

Wires together storage, lifecycle, capability mixins, and extracted
subsystems. Each mixin is in its own file under coordinationhub/.

LockingMixin:     core_locking.py     — lock acquire/release/refresh/list/admin
BroadcastMixin:   core_broadcasts.py  — broadcast, handoff dispatch, wait_for_locks
IdentityMixin:    core_identity.py    — agent registration, heartbeat, lineage
VisibilityMixin:  core_visibility.py  — coordination graph, scan, assessment

Composed subsystems (T6.22 — extracted from the mixin tree):
Spawner:          spawner_subsystem.py     — sub-agent spawn management
WorkIntent:       work_intent_subsystem.py — cooperative work intent board
Lease:            lease_subsystem.py       — HA coordinator leadership leases
Dependency:       dependency_subsystem.py  — cross-agent dependency declarations
Messaging:        messaging_subsystem.py   — inter-agent messages, agent await
Handoff:          handoff_subsystem.py     — handoff acknowledgment tracking
Change:           change_subsystem.py      — change notifications, audit, status
Task:             task_subsystem.py        — task registry, hierarchy, DLQ

Zero third-party dependencies.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

from ._storage import CoordinationStorage
from .event_bus import EventBus
from .housekeeping import (
    HousekeepingScheduler,
    build_default_scheduler,
    is_enabled_by_env,
)
from .lock_cache import LockCache
from .core_locking import LockingMixin
from .core_broadcasts import BroadcastMixin
from .core_identity import IdentityMixin
from .core_visibility import VisibilityMixin
from .spawner_subsystem import Spawner
from .work_intent_subsystem import WorkIntent
from .lease_subsystem import Lease
from .dependency_subsystem import Dependency
from .messaging_subsystem import Messaging
from .handoff_subsystem import Handoff
from .change_subsystem import Change
from .task_subsystem import Task
from .paths import detect_project_root
from .plugins.graph import graphs as _g


class CoordinationEngine(
    LockingMixin,
    BroadcastMixin,
    IdentityMixin,
    VisibilityMixin,
):
    """Host class that inherits capability mixins and holds subsystems.

    Provides storage lifecycle and wiring for cross-mixin calls.
    Most domain methods are provided by the mixins. Subsystems
    extracted from the mixin tree (T6.22) hang off the engine as
    composed attributes — ``self._spawner``, ``self._work_intent``,
    ``self._lease``, ``self._dependency``, ``self._messaging``,
    ``self._handoff``, ``self._change``, and ``self._task`` — with
    facade methods on the engine preserving the public API.
    """

    DEFAULT_PORT = 9877
    HEARTBEAT_INTERVAL = 30
    DEFAULT_TTL = 300.0

    def __init__(
        self,
        storage_dir: Path | None = None,
        project_root: Path | None = None,
        namespace: str = "hub",
        housekeeping: bool | None = None,
    ) -> None:
        self._storage = CoordinationStorage(
            storage_dir=storage_dir,
            project_root=project_root or detect_project_root(),
            namespace=namespace,
        )
        self._event_bus = EventBus()
        self._lock_cache = LockCache()
        self._graph = None  # set in start()
        # Opt-in: housekeeping=True forces on, False forces off; None
        # defers to the COORDINATIONHUB_HOUSEKEEPING env var so long-lived
        # `serve` processes can enable it without a code change while
        # short-lived CLI invocations stay thread-free by default.
        self._housekeeping_enabled = (
            is_enabled_by_env() if housekeeping is None else bool(housekeeping)
        )
        self._housekeeper: HousekeepingScheduler | None = None
        # T6.22: composed subsystem replaces SpawnerMixin. The engine
        # wires the three infra callables (_connect, _publish_event,
        # _hybrid_wait) as deps; facade methods below delegate so the
        # public API on ``engine`` stays identical.
        self._spawner = Spawner(
            connect_fn=self._connect,
            publish_event_fn=self._publish_event,
            hybrid_wait_fn=self._hybrid_wait,
        )
        # T6.22: composed subsystem replaces WorkIntentMixin. Per the
        # coupling audit the mixin only touched ``_connect`` and
        # ``_storage.project_root`` (for path normalization); both are
        # injected here. ``project_root_getter`` is a callable so a
        # replica produced by ``read_only_engine`` picks up its own
        # storage's root without rebinding.
        self._work_intent = WorkIntent(
            connect_fn=self._connect,
            project_root_getter=lambda: self._storage.project_root,
        )
        # T6.22: composed subsystem replaces LeaseMixin. Per the coupling
        # audit LeaseMixin had zero cross-mixin calls and zero
        # ``_hybrid_wait`` calls — it only needed ``_connect`` and the
        # four ``_publish_event`` notifications for lease state changes.
        # Both are injected here; facade methods below delegate so the
        # public API (``engine.acquire_coordinator_lease`` etc.) stays
        # identical. See commits ``1ee46c6`` (Spawner) and ``3d1bd48``
        # (WorkIntent) for the two prior extractions in this series.
        self._lease = Lease(
            connect_fn=self._connect,
            publish_event_fn=self._publish_event,
        )
        # T6.22: composed subsystem replaces DependencyMixin. Per the
        # coupling audit DependencyMixin had zero cross-mixin calls and
        # zero ``_hybrid_wait`` calls — it only needed ``_connect`` and
        # four ``_publish_event`` notifications for declare/satisfy.
        # Same two-dep shape as :class:`Lease` (commit ``b4a3e6b``).
        # Post-T6.38 the Task subsystem's ``update_task_status`` no
        # longer calls ``_deps.satisfy_dependencies_for_task(...)`` at
        # this layer — the dep-satisfy side effect is folded into the
        # tasks primitive's transaction, so no cross-subsystem wiring
        # between ``_task`` and ``_dependency`` is required.
        self._dependency = Dependency(
            connect_fn=self._connect,
            publish_event_fn=self._publish_event,
        )
        # T6.22: composed subsystem replaces MessagingMixin. Per the
        # coupling audit MessagingMixin had zero cross-mixin calls and
        # needed all three infra callables — ``_connect`` for the
        # primitive module, ``_publish_event`` for ``message.received``
        # notifications on send, and ``_hybrid_wait`` for ``await_agent``.
        # Same three-dep shape as :class:`Spawner` (commit ``1ee46c6``).
        # Preserves the T2.4 caller_agent_id security check on
        # ``send_message`` + ``manage_messages`` and the T7.23 dual-path
        # design (``send_message`` and ``manage_messages(action='send')``
        # both remain on the MCP surface by design).
        self._messaging = Messaging(
            connect_fn=self._connect,
            publish_event_fn=self._publish_event,
            hybrid_wait_fn=self._hybrid_wait,
        )
        # T6.22: composed subsystem replaces HandoffMixin. Per the
        # coupling audit HandoffMixin had zero cross-mixin calls and
        # needed all three infra callables — ``_connect`` for the
        # primitive module, ``_publish_event`` for ``handoff.ack`` /
        # ``handoff.completed`` / ``handoff.cancelled`` notifications,
        # and ``_hybrid_wait`` for ``wait_for_handoff(mode='completion')``.
        # Same three-dep shape as :class:`Spawner` (commit ``1ee46c6``)
        # and :class:`Messaging` (commit ``d9f84d3``). Preserves the
        # T1.15 caller-vs-row authz check on ``acknowledge_handoff`` —
        # the primitive rejects acks from agents not listed in the
        # handoff row's ``to_agents`` — and the T1.19 no-phantom-event
        # guarantee on no-op ``complete_handoff`` / ``cancel_handoff``.
        self._handoff = Handoff(
            connect_fn=self._connect,
            publish_event_fn=self._publish_event,
            hybrid_wait_fn=self._hybrid_wait,
        )
        # T6.22: composed subsystem replaces ChangeMixin. Per the
        # coupling audit ChangeMixin had zero cross-mixin calls and
        # needed all three infra callables — ``_connect`` for the
        # primitive module and direct SQL (status,
        # get_contention_hotspots), ``_publish_event`` for the
        # ``notification.created`` event fired on ``notify_change``,
        # and ``_hybrid_wait`` for ``get_notifications(timeout_s>0)``
        # plus ``wait_for_notifications``. Also needs the project root
        # for ``normalize_path`` on ``notify_change`` /
        # ``claim_file_ownership`` / ``get_conflicts``; injected as a
        # closure like :class:`WorkIntent` (commit ``3d1bd48``) so a
        # replica produced by ``read_only_engine`` picks up its own
        # storage's root without a rebind. Four-dep shape: three-dep
        # Spawner/Messaging/Handoff (commits ``1ee46c6``, ``d9f84d3``,
        # ``ded641d``) plus ``project_root_getter``.
        self._change = Change(
            connect_fn=self._connect,
            publish_event_fn=self._publish_event,
            hybrid_wait_fn=self._hybrid_wait,
            project_root_getter=lambda: self._storage.project_root,
        )
        # T6.22: composed subsystem replaces TaskMixin. Per the coupling
        # audit TaskMixin had zero cross-mixin calls and needed all three
        # infra callables — ``_connect`` for the tasks/task_failures
        # primitive modules, ``_publish_event`` for the four
        # ``task.created`` / ``task.assigned`` / ``task.completed`` /
        # ``task.failed`` notifications, and ``_hybrid_wait`` for
        # ``wait_for_task``. Same three-dep shape as :class:`Spawner`
        # (commit ``1ee46c6``), :class:`Messaging` (commit ``d9f84d3``),
        # and :class:`Handoff` (commit ``ded641d``). Largest surface of
        # the series (11+ public methods including the ``query_tasks``
        # and ``task_failures`` dispatch-by-string entry points — T6.37
        # keeps the string dispatch as-is for now). Preserves T1.13
        # status-validation authz (the primitive owns
        # ``_VALID_TASK_STATUSES``) and T6.38 / T6.39 / T6.40 atomic
        # side-effect folding (dep-satisfy + DLQ record happen inside
        # the primitive's transaction; events only fire when the stored
        # status actually changed).
        self._task = Task(
            connect_fn=self._connect,
            publish_event_fn=self._publish_event,
            hybrid_wait_fn=self._hybrid_wait,
        )

    def start(self) -> None:
        """Start storage, warm lock cache, and load coordination graph."""
        self._storage.start()
        now = time.time()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM document_locks WHERE locked_at + lock_ttl >= ?",
                (now,),
            ).fetchall()
            self._lock_cache.warm([dict(r) for r in rows])
        self._graph = _g.load_coordination_spec_from_disk(
            self._connect, self._storage.project_root,
        )
        if self._housekeeping_enabled:
            self._housekeeper = build_default_scheduler(self)
            self._housekeeper.start()

    def close(self) -> None:
        """Close storage, checkpoint WAL.

        Stops the housekeeping scheduler first so it can't race a DB close
        with an in-flight prune. A stopped scheduler shuts down within a
        few seconds; we log and move on past the join timeout to keep
        engine shutdown bounded.
        """
        if self._housekeeper is not None:
            self._housekeeper.stop()
            self._housekeeper = None
        self._storage.close()

    def _connect(self):
        """Return a connection from the thread-local pool."""
        return self._storage._connect()

    def _publish_event(self, topic: str, payload: dict[str, Any]) -> None:
        """Publish to the SQLite journal first, then the in-memory bus.

        T1.10: order reversed. Previously the in-memory bus fired before the
        journal write, so a crash between the two left in-process waiters
        with an event that cross-process waiters (via ``_hybrid_wait``)
        could never observe. Now the DB insert commits before in-memory
        subscribers see the event, so a crash after the bus publish still
        leaves the durable journal consistent, and a failure on the
        journal write is visible (logged at WARNING) instead of silently
        swallowed.
        """
        import json as _json
        journal_ok = False
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO coordination_events (topic, payload_json, created_at) VALUES (?, ?, ?)",
                    (topic, _json.dumps(payload, default=str), time.time()),
                )
                conn.commit()
            journal_ok = True
        except Exception as exc:
            # Journal write failed — log, but still fire the in-memory bus
            # so same-process waiters don't hang indefinitely. Cross-process
            # waiters will miss this event (they poll the journal); caller
            # can check the return-value-free contract and re-publish if
            # durability matters.
            _log.warning(
                "coordination event journal write failed for topic=%r: %s",
                topic, exc,
            )
        # In-memory publish happens after the durable write so there is no
        # window where a same-process waiter sees an event that isn't yet
        # in the journal. On journal failure we still publish so the
        # in-process bus remains usable.
        self._event_bus.publish(topic, payload)
        # Expose the outcome to callers that care (most don't). This
        # attribute is intentionally per-engine, not per-call, because the
        # API is fire-and-forget for backwards compat.
        self._last_event_journaled = journal_ok

    def _hybrid_wait(
        self,
        topics: list[str],
        filter_fn: Any = None,
        timeout: float = 30.0,
    ) -> dict[str, Any] | None:
        """Wait for an event using in-memory bus first, then SQLite journal fallback.

        This ensures wait primitives work both in-process and across processes
        (e.g., when talking to coordinationhub serve).
        """
        import queue as _queue
        import json as _json

        start = time.time()
        # Fast path: in-memory event bus (same process)
        event = self._event_bus.wait_for_event(topics, filter_fn, timeout=0.05)
        if event is not None:
            return event

        # Determine the earliest created_at we care about
        since = start - 1.0  # allow events that arrived just before we started

        while True:
            elapsed = time.time() - start
            if elapsed >= timeout:
                return None
            remaining = timeout - elapsed

            # Poll SQLite journal for events matching topics
            try:
                with self._connect() as conn:
                    # Fetch recent events for any of the topics
                    placeholders = ",".join("?" * len(topics))
                    rows = conn.execute(
                        f"""SELECT topic, payload_json, created_at FROM coordination_events
                            WHERE topic IN ({placeholders}) AND created_at > ?
                            ORDER BY created_at ASC""",
                        tuple(topics) + (since,),
                    ).fetchall()
            except Exception:
                rows = []

            for row in rows:
                try:
                    payload = _json.loads(row["payload_json"])
                except Exception:
                    continue
                evt = {"topic": row["topic"], **payload}
                if filter_fn is None or filter_fn(evt):
                    # Update since so we don't re-process this event
                    since = row["created_at"]
                    return evt

            if rows:
                since = max(row["created_at"] for row in rows)

            # Short sleep before next poll
            sleep_for = min(0.5, remaining)
            if sleep_for <= 0:
                return None
            time.sleep(sleep_for)

    # ------------------------------------------------------------------ #
    # Spawner facade (T6.22)
    # ------------------------------------------------------------------ #
    # These one-liners delegate to ``self._spawner`` (a :class:`Spawner`
    # composed in ``__init__``). They preserve the pre-extraction public
    # API — MCP dispatch, CLI, and tests all continue to call
    # ``engine.spawn_subagent(...)`` etc. verbatim.

    def spawn_subagent(
        self,
        parent_agent_id: str,
        subagent_type: str,
        description: str | None = None,
        prompt: str | None = None,
        source: str = "external",
    ) -> dict[str, Any]:
        return self._spawner.spawn_subagent(
            parent_agent_id=parent_agent_id,
            subagent_type=subagent_type,
            description=description,
            prompt=prompt,
            source=source,
        )

    def get_pending_spawns(
        self,
        parent_agent_id: str,
        include_consumed: bool = False,
    ) -> list[dict[str, Any]]:
        return self._spawner.get_pending_spawns(
            parent_agent_id=parent_agent_id,
            include_consumed=include_consumed,
        )

    def report_subagent_spawned(
        self,
        parent_agent_id: str,
        subagent_type: str | None,
        child_agent_id: str,
        source: str = "external",
        caller_agent_id: str | None = None,
    ) -> dict[str, Any]:
        return self._spawner.report_subagent_spawned(
            parent_agent_id=parent_agent_id,
            subagent_type=subagent_type,
            child_agent_id=child_agent_id,
            source=source,
            caller_agent_id=caller_agent_id,
        )

    def await_subagent_registration(
        self,
        parent_agent_id: str,
        subagent_type: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        return self._spawner.await_subagent_registration(
            parent_agent_id=parent_agent_id,
            subagent_type=subagent_type,
            timeout=timeout,
        )

    def cancel_spawn(
        self, spawn_id: str, caller_agent_id: str | None = None,
    ) -> dict[str, Any]:
        return self._spawner.cancel_spawn(
            spawn_id=spawn_id, caller_agent_id=caller_agent_id,
        )

    def request_subagent_deregistration(
        self,
        parent_agent_id: str,
        child_agent_id: str,
    ) -> dict[str, Any]:
        return self._spawner.request_subagent_deregistration(
            parent_agent_id=parent_agent_id,
            child_agent_id=child_agent_id,
        )

    def is_subagent_stop_requested(self, agent_id: str) -> dict[str, Any]:
        return self._spawner.is_subagent_stop_requested(agent_id=agent_id)

    def await_subagent_stopped(
        self,
        child_agent_id: str,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        return self._spawner.await_subagent_stopped(
            child_agent_id=child_agent_id, timeout=timeout,
        )

    # ------------------------------------------------------------------ #
    # WorkIntent facade (T6.22)
    # ------------------------------------------------------------------ #
    # These one-liners delegate to ``self._work_intent`` (a
    # :class:`WorkIntent` composed in ``__init__``). They preserve the
    # pre-extraction public API — MCP dispatch (``manage_work_intents``),
    # CLI (``cli_intent.py``), housekeeping (``prune_work_intents``),
    # and tests all continue to call ``engine.declare_work_intent(...)``
    # etc. verbatim.

    def manage_work_intents(
        self,
        action: str,
        agent_id: str,
        document_path: str | None = None,
        intent: str | None = None,
        ttl: float = 60.0,
    ) -> dict[str, Any]:
        return self._work_intent.manage_work_intents(
            action=action,
            agent_id=agent_id,
            document_path=document_path,
            intent=intent,
            ttl=ttl,
        )

    def declare_work_intent(
        self,
        agent_id: str,
        document_path: str,
        intent: str,
        ttl: float = 60.0,
    ) -> dict[str, Any]:
        return self._work_intent.declare_work_intent(
            agent_id=agent_id,
            document_path=document_path,
            intent=intent,
            ttl=ttl,
        )

    def get_work_intents(self, agent_id: str | None = None) -> dict[str, Any]:
        return self._work_intent.get_work_intents(agent_id=agent_id)

    def clear_work_intent(
        self, agent_id: str, document_path: str | None = None,
    ) -> dict[str, Any]:
        return self._work_intent.clear_work_intent(
            agent_id=agent_id, document_path=document_path,
        )

    def prune_work_intents(self) -> dict[str, Any]:
        return self._work_intent.prune_work_intents()

    # ------------------------------------------------------------------ #
    # Lease facade (T6.22)
    # ------------------------------------------------------------------ #
    # These one-liners delegate to ``self._lease`` (a :class:`Lease`
    # composed in ``__init__``). They preserve the pre-extraction public
    # API — MCP dispatch (``manage_leases`` / ``acquire_coordinator_lease``),
    # CLI (``cli_leases.py``), housekeeping, and tests all continue to
    # call ``engine.acquire_coordinator_lease(...)`` etc. verbatim.

    def manage_leases(
        self,
        action: str,
        agent_id: str | None = None,
        ttl: float | None = None,
    ) -> dict[str, Any]:
        return self._lease.manage_leases(
            action=action, agent_id=agent_id, ttl=ttl,
        )

    def acquire_coordinator_lease(
        self,
        agent_id: str,
        ttl: float | None = None,
    ) -> dict[str, Any]:
        return self._lease.acquire_coordinator_lease(agent_id=agent_id, ttl=ttl)

    def refresh_coordinator_lease(self, agent_id: str) -> dict[str, Any]:
        return self._lease.refresh_coordinator_lease(agent_id=agent_id)

    def release_coordinator_lease(self, agent_id: str) -> dict[str, Any]:
        return self._lease.release_coordinator_lease(agent_id=agent_id)

    def is_leader(self, agent_id: str) -> bool:
        return self._lease.is_leader(agent_id=agent_id)

    def get_leader(self) -> dict[str, Any] | None:
        return self._lease.get_leader()

    def claim_leadership(
        self, agent_id: str, ttl: float | None = None,
    ) -> dict[str, Any]:
        return self._lease.claim_leadership(agent_id=agent_id, ttl=ttl)

    # ------------------------------------------------------------------ #
    # Dependency facade (T6.22)
    # ------------------------------------------------------------------ #
    # These one-liners delegate to ``self._dependency`` (a
    # :class:`Dependency` composed in ``__init__``). They preserve the
    # pre-extraction public API — MCP dispatch (``manage_dependencies``),
    # CLI (``cli_deps.py``), and tests all continue to call
    # ``engine.declare_dependency(...)`` etc. verbatim.

    def declare_dependency(
        self,
        dependent_agent_id: str,
        depends_on_agent_id: str,
        depends_on_task_id: str | None = None,
        condition: str = "task_completed",
    ) -> dict[str, Any]:
        return self._dependency.declare_dependency(
            dependent_agent_id=dependent_agent_id,
            depends_on_agent_id=depends_on_agent_id,
            depends_on_task_id=depends_on_task_id,
            condition=condition,
        )

    def manage_dependencies(
        self,
        mode: str,
        agent_id: str | None = None,
        dependent_agent_id: str | None = None,
        depends_on_agent_id: str | None = None,
        depends_on_task_id: str | None = None,
        condition: str = "task_completed",
        dep_id: int | None = None,
        timeout_s: float = 60.0,
        poll_interval_s: float = 2.0,
    ) -> dict[str, Any]:
        return self._dependency.manage_dependencies(
            mode=mode,
            agent_id=agent_id,
            dependent_agent_id=dependent_agent_id,
            depends_on_agent_id=depends_on_agent_id,
            depends_on_task_id=depends_on_task_id,
            condition=condition,
            dep_id=dep_id,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
        )

    def satisfy_dependency(self, dep_id: int) -> dict[str, Any]:
        return self._dependency.satisfy_dependency(dep_id=dep_id)

    def get_all_dependencies(
        self, dependent_agent_id: str | None = None,
    ) -> dict[str, Any]:
        return self._dependency.get_all_dependencies(
            dependent_agent_id=dependent_agent_id,
        )

    def wait_for_dependency(
        self,
        dep_id: int,
        timeout_s: float = 60.0,
        poll_interval_s: float = 2.0,
    ) -> dict[str, Any]:
        return self._dependency.wait_for_dependency(
            dep_id=dep_id,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
        )

    # ------------------------------------------------------------------ #
    # Messaging facade (T6.22)
    # ------------------------------------------------------------------ #
    # These one-liners delegate to ``self._messaging`` (a
    # :class:`Messaging` composed in ``__init__``). They preserve the
    # pre-extraction public API — MCP dispatch (``send_message``,
    # ``manage_messages``, ``await_agent``), CLI (``cli_locks.py``), and
    # tests all continue to call ``engine.send_message(...)`` etc.
    # verbatim, including the T2.4 ``caller_agent_id`` kwarg used by
    # ``tests/test_authz.py`` and the T7.23 dual-path ``send_message``
    # and ``manage_messages(action='send')`` coexistence.

    def manage_messages(
        self,
        action: str,
        agent_id: str,
        from_agent_id: str | None = None,
        to_agent_id: str | None = None,
        message_type: str | None = None,
        payload: dict[str, Any] | None = None,
        unread_only: bool = False,
        limit: int = 50,
        message_ids: list[int] | None = None,
        since_id: int | None = None,
        caller_agent_id: str | None = None,
    ) -> dict[str, Any]:
        return self._messaging.manage_messages(
            action=action,
            agent_id=agent_id,
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            message_type=message_type,
            payload=payload,
            unread_only=unread_only,
            limit=limit,
            message_ids=message_ids,
            since_id=since_id,
            caller_agent_id=caller_agent_id,
        )

    def send_message(
        self,
        from_agent_id: str,
        to_agent_id: str,
        message_type: str,
        payload: dict[str, Any] | None = None,
        caller_agent_id: str | None = None,
    ) -> dict[str, Any]:
        return self._messaging.send_message(
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            message_type=message_type,
            payload=payload,
            caller_agent_id=caller_agent_id,
        )

    def get_messages(
        self, agent_id: str, unread_only: bool = False, limit: int = 50,
        since_id: int | None = None,
    ) -> dict[str, Any]:
        return self._messaging.get_messages(
            agent_id=agent_id,
            unread_only=unread_only,
            limit=limit,
            since_id=since_id,
        )

    def mark_messages_read(
        self, agent_id: str, message_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        return self._messaging.mark_messages_read(
            agent_id=agent_id, message_ids=message_ids,
        )

    def await_agent(self, agent_id: str, timeout_s: float = 60.0) -> dict[str, Any]:
        return self._messaging.await_agent(
            agent_id=agent_id, timeout_s=timeout_s,
        )

    # ------------------------------------------------------------------ #
    # Handoff facade (T6.22)
    # ------------------------------------------------------------------ #
    # These one-liners delegate to ``self._handoff`` (a :class:`Handoff`
    # composed in ``__init__``). They preserve the pre-extraction public
    # API — MCP dispatch (``acknowledge_handoff``, ``complete_handoff``,
    # ``cancel_handoff``, ``get_handoffs``, ``wait_for_handoff``), CLI
    # (``cli_locks.py``), and tests all continue to call
    # ``engine.acknowledge_handoff(...)`` etc. verbatim. Preserves the
    # T1.15 caller-vs-row authz check (the primitive rejects acks from
    # agents not listed in the handoff row's ``to_agents``).

    def acknowledge_handoff(self, handoff_id: int, agent_id: str) -> dict[str, Any]:
        return self._handoff.acknowledge_handoff(
            handoff_id=handoff_id, agent_id=agent_id,
        )

    def complete_handoff(self, handoff_id: int) -> dict[str, Any]:
        return self._handoff.complete_handoff(handoff_id=handoff_id)

    def cancel_handoff(self, handoff_id: int) -> dict[str, Any]:
        return self._handoff.cancel_handoff(handoff_id=handoff_id)

    def get_handoffs(
        self,
        status: str | None = None,
        from_agent_id: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        return self._handoff.get_handoffs(
            status=status, from_agent_id=from_agent_id, limit=limit,
        )

    def wait_for_handoff(
        self,
        handoff_id: int,
        timeout_s: float = 30.0,
        agent_id: str | None = None,
        mode: str = "completion",
    ) -> dict[str, Any]:
        return self._handoff.wait_for_handoff(
            handoff_id=handoff_id,
            timeout_s=timeout_s,
            agent_id=agent_id,
            mode=mode,
        )

    # ------------------------------------------------------------------ #
    # Change facade (T6.22)
    # ------------------------------------------------------------------ #
    # These one-liners delegate to ``self._change`` (a :class:`Change`
    # composed in ``__init__``). They preserve the pre-extraction public
    # API — MCP dispatch (``notify_change``, ``get_notifications``,
    # ``get_conflicts``, ``get_contention_hotspots``), CLI
    # (``cli_vis.py``), hooks (``hooks/base.py`` calls
    # ``notify_change`` / ``claim_file_ownership``), housekeeping
    # (``build_default_scheduler`` calls ``engine.prune_notifications``),
    # and tests all continue to call ``engine.notify_change(...)`` etc.
    # verbatim.

    def notify_change(
        self,
        document_path: str,
        change_type: str,
        agent_id: str,
    ) -> dict[str, Any]:
        return self._change.notify_change(
            document_path=document_path,
            change_type=change_type,
            agent_id=agent_id,
        )

    def claim_file_ownership(self, document_path: str, agent_id: str) -> None:
        return self._change.claim_file_ownership(
            document_path=document_path, agent_id=agent_id,
        )

    def get_notifications(
        self,
        since: float | None = None,
        exclude_agent: str | None = None,
        limit: int = 100,
        agent_id: str | None = None,
        timeout_s: float = 0.0,
        poll_interval_s: float = 2.0,
        prune_max_age_seconds: float | None = None,
        prune_max_entries: int | None = None,
    ) -> dict[str, Any]:
        return self._change.get_notifications(
            since=since,
            exclude_agent=exclude_agent,
            limit=limit,
            agent_id=agent_id,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
            prune_max_age_seconds=prune_max_age_seconds,
            prune_max_entries=prune_max_entries,
        )

    def prune_notifications(
        self,
        max_age_seconds: float | None = None,
        max_entries: int | None = None,
    ) -> dict[str, Any]:
        return self._change.prune_notifications(
            max_age_seconds=max_age_seconds, max_entries=max_entries,
        )

    def wait_for_notifications(
        self,
        agent_id: str,
        timeout_s: float = 30.0,
        poll_interval_s: float = 2.0,
        exclude_agent: str | None = None,
    ) -> dict[str, Any]:
        return self._change.wait_for_notifications(
            agent_id=agent_id,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
            exclude_agent=exclude_agent,
        )

    def get_conflicts(
        self,
        document_path: str | None = None,
        agent_id: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        return self._change.get_conflicts(
            document_path=document_path, agent_id=agent_id, limit=limit,
        )

    def get_contention_hotspots(self, limit: int = 10) -> dict[str, Any]:
        return self._change.get_contention_hotspots(limit=limit)

    def status(self) -> dict[str, Any]:
        return self._change.status()

    # ------------------------------------------------------------------ #
    # Task facade (T6.22)
    # ------------------------------------------------------------------ #
    # These one-liners delegate to ``self._task`` (a :class:`Task`
    # composed in ``__init__``). They preserve the pre-extraction public
    # API — MCP dispatch (``create_task``, ``update_task_status``,
    # ``assign_task``, ``query_tasks``, ``create_subtask``,
    # ``wait_for_task``, ``get_available_tasks``, ``task_failures``,
    # ``get_dead_letter_tasks``, ``retry_from_dead_letter``,
    # ``get_task_failure_history``), CLI (``cli_tasks.py``), hooks, and
    # tests all continue to call ``engine.create_task(...)`` etc.
    # verbatim. ``query_tasks`` keeps its dispatch-by-string shape
    # (T6.37 still deferred). ``update_task_status`` preserves T1.13
    # status-validation authz via the primitive's ``_VALID_TASK_STATUSES``
    # rejection path and T6.38 / T6.39 / T6.40 atomic-side-effect
    # behaviour (dep-satisfy + DLQ record happen inside the primitive
    # tx; ``error`` forwarded on every transition; events only fire
    # when the stored status actually changed).

    def create_task(
        self,
        task_id: str,
        parent_agent_id: str,
        description: str,
        depends_on: list[str] | None = None,
        priority: int = 0,
    ) -> dict[str, Any]:
        return self._task.create_task(
            task_id=task_id,
            parent_agent_id=parent_agent_id,
            description=description,
            depends_on=depends_on,
            priority=priority,
        )

    def assign_task(self, task_id: str, assigned_agent_id: str) -> dict[str, Any]:
        return self._task.assign_task(
            task_id=task_id, assigned_agent_id=assigned_agent_id,
        )

    def update_task_status(
        self,
        task_id: str,
        status: str,
        summary: str | None = None,
        blocked_by: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        return self._task.update_task_status(
            task_id=task_id,
            status=status,
            summary=summary,
            blocked_by=blocked_by,
            error=error,
        )

    def query_tasks(
        self,
        query_type: str,
        task_id: str | None = None,
        parent_agent_id: str | None = None,
        assigned_agent_id: str | None = None,
        parent_task_id: str | None = None,
        root_task_id: str | None = None,
    ) -> dict[str, Any]:
        return self._task.query_tasks(
            query_type=query_type,
            task_id=task_id,
            parent_agent_id=parent_agent_id,
            assigned_agent_id=assigned_agent_id,
            parent_task_id=parent_task_id,
            root_task_id=root_task_id,
        )

    def create_subtask(
        self,
        task_id: str,
        parent_task_id: str,
        parent_agent_id: str,
        description: str,
        depends_on: list[str] | None = None,
        priority: int = 0,
    ) -> dict[str, Any]:
        return self._task.create_subtask(
            task_id=task_id,
            parent_task_id=parent_task_id,
            parent_agent_id=parent_agent_id,
            description=description,
            depends_on=depends_on,
            priority=priority,
        )

    def task_failures(
        self,
        action: str,
        task_id: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        return self._task.task_failures(
            action=action, task_id=task_id, limit=limit,
        )

    def retry_task(self, task_id: str) -> dict[str, Any]:
        return self._task.retry_task(task_id=task_id)

    def get_dead_letter_tasks(self, limit: int = 50) -> dict[str, Any]:
        return self._task.get_dead_letter_tasks(limit=limit)

    def get_task_failure_history(self, task_id: str) -> dict[str, Any]:
        return self._task.get_task_failure_history(task_id=task_id)

    def wait_for_task(
        self,
        task_id: str,
        timeout_s: float = 60.0,
        poll_interval_s: float = 2.0,
    ) -> dict[str, Any]:
        return self._task.wait_for_task(
            task_id=task_id,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
        )

    def get_available_tasks(self, agent_id: str | None = None) -> dict[str, Any]:
        return self._task.get_available_tasks(agent_id=agent_id)

    def read_only_engine(self) -> "CoordinationEngine":
        """Return a read-only view of this engine using direct WAL reads.

        The returned engine bypasses the writer pool and opens SQLite in
        read-only URI mode. All read-only operations (list_agents,
        get_lock_status, get_notifications, etc.) can use this to avoid
        round-tripping through the current leaseholder.
        """
        replica = CoordinationEngine(
            storage_dir=self._storage._storage_dir,
            project_root=self._storage.project_root,
            namespace=self._storage._namespace,
            housekeeping=False,
        )
        # Don't call start() — we don't need the pool or graph, just storage
        replica._connect = self._storage.read_only_connection  # type: ignore[method-assign]
        # T6.22: the Spawner subsystem captured the writer-pool connect in
        # its __init__; rebind to the read-only connection so replica
        # spawner calls don't punch through to the pool.
        replica._spawner._connect = replica._connect
        # Same rebind for the WorkIntent subsystem. Its ``project_root_getter``
        # is a closure over ``self._storage`` so it already picks up the
        # replica's storage without further rebinding.
        replica._work_intent._connect = replica._connect
        # T6.22: and the Lease subsystem — same pattern. No
        # ``_publish_event`` rebind is needed because the replica's
        # ``_publish_event`` was captured by its own ``_lease.__init__``
        # against the replica's (unused) writer pool; lease mutations
        # through a read-only replica are not a supported flow.
        replica._lease._connect = replica._connect
        # T6.22: and the Dependency subsystem — same pattern. Same
        # ``_publish_event`` rationale as Lease: the replica captured
        # its own ``_publish_event`` in ``_dependency.__init__`` and
        # dependency mutations through a read-only replica are not a
        # supported flow.
        replica._dependency._connect = replica._connect
        # T6.22: and the Messaging subsystem — same three-dep pattern as
        # Spawner. ``_publish_event`` and ``_hybrid_wait`` were captured
        # in ``_messaging.__init__`` against the replica's own callables;
        # messaging mutations (send) through a read-only replica are not
        # a supported flow and ``await_agent`` is read-only, so only the
        # ``_connect`` rebind is needed.
        replica._messaging._connect = replica._connect
        # T6.22: and the Handoff subsystem — same three-dep pattern as
        # Spawner and Messaging. ``_publish_event`` and ``_hybrid_wait``
        # were captured in ``_handoff.__init__`` against the replica's
        # own callables; handoff mutations (ack/complete/cancel) through
        # a read-only replica are not a supported flow and
        # ``wait_for_handoff(mode='completion')`` / ``mode='status'``
        # are read-only, so only the ``_connect`` rebind is needed.
        replica._handoff._connect = replica._connect
        # T6.22: and the Change subsystem — four-dep shape, but the
        # ``project_root_getter`` is a closure over ``self._storage``
        # so it already picks up the replica's storage without a
        # rebind (same pattern as WorkIntent). ``_publish_event`` and
        # ``_hybrid_wait`` were captured in ``_change.__init__`` against
        # the replica's own callables; change mutations
        # (notify_change / claim_file_ownership) through a read-only
        # replica are not a supported flow. Read-only reads
        # (get_notifications, get_conflicts, get_contention_hotspots,
        # status) only need ``_connect`` rebound.
        replica._change._connect = replica._connect
        # T6.22: and the Task subsystem — same three-dep pattern as
        # Spawner, Messaging, and Handoff. ``_publish_event`` and
        # ``_hybrid_wait`` were captured in ``_task.__init__`` against
        # the replica's own callables; task mutations (create / assign /
        # update_task_status / create_subtask / retry) through a
        # read-only replica are not a supported flow, and the read-only
        # task operations (``query_tasks``, ``get_available_tasks``,
        # ``get_dead_letter_tasks``, ``get_task_failure_history``,
        # ``wait_for_task``) only need ``_connect`` rebound.
        replica._task._connect = replica._connect
        return replica

