"""CoordinationEngine — thin host class that inherits all mixins.

Wires together storage, lifecycle, and all capability mixins.
Each mixin is in its own file under coordinationhub/.

LockingMixin:     core_locking.py     — lock acquisition, release, broadcast, wait
IdentityMixin:    core_identity.py    — agent registration, heartbeat, lineage
MessagingMixin:   core_messaging.py  — inter-agent messages, await
TaskMixin:        core_tasks.py       — task registry with hierarchy
WorkIntentMixin:  core_work_intent.py — cooperative work intent board
HandoffMixin:     core_handoffs.py    — handoff acknowledgment tracking
DependencyMixin:  core_dependencies.py — cross-agent dependency declarations
ChangeMixin:      core_change.py      — change notifications, audit, status
VisibilityMixin:  core_visibility.py  — coordination graph, scan, assessment

Zero third-party dependencies.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ._storage import CoordinationStorage
from .event_bus import EventBus
from .lock_cache import LockCache
from .core_locking import LockingMixin
from .core_identity import IdentityMixin
from .core_messaging import MessagingMixin
from .core_tasks import TaskMixin
from .core_work_intent import WorkIntentMixin
from .core_handoffs import HandoffMixin
from .core_dependencies import DependencyMixin
from .core_change import ChangeMixin
from .core_visibility import VisibilityMixin
from .core_leases import LeaseMixin
from .core_spawner import SpawnerMixin
from .paths import detect_project_root
from .plugins.graph import graphs as _g


class CoordinationEngine(
    LockingMixin,
    IdentityMixin,
    MessagingMixin,
    TaskMixin,
    WorkIntentMixin,
    HandoffMixin,
    DependencyMixin,
    ChangeMixin,
    VisibilityMixin,
    LeaseMixin,
    SpawnerMixin,
):
    """Host class that inherits all capability mixins.

    Provides storage lifecycle and wiring for cross-mixin calls.
    All domain methods are provided by the mixins.
    """

    DEFAULT_PORT = 9877
    HEARTBEAT_INTERVAL = 30
    DEFAULT_TTL = 300.0

    def __init__(
        self,
        storage_dir: Path | None = None,
        project_root: Path | None = None,
        namespace: str = "hub",
    ) -> None:
        self._storage = CoordinationStorage(
            storage_dir=storage_dir,
            project_root=project_root or detect_project_root(),
            namespace=namespace,
        )
        self._event_bus = EventBus()
        self._lock_cache = LockCache()
        self._graph = None  # set in start()

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

    def close(self) -> None:
        """Close storage, checkpoint WAL."""
        self._storage.close()

    def _connect(self):
        """Return a connection from the thread-local pool."""
        return self._storage._connect()

    def _publish_event(self, topic: str, payload: dict[str, Any]) -> None:
        """Publish to in-memory bus and write to SQLite journal for cross-process sync."""
        self._event_bus.publish(topic, payload)
        try:
            with self._connect() as conn:
                import json as _json
                conn.execute(
                    "INSERT INTO coordination_events (topic, payload_json, created_at) VALUES (?, ?, ?)",
                    (topic, _json.dumps(payload, default=str), time.time()),
                )
        except Exception:
            # Best-effort journal write; don't let event bus fail on DB issues
            pass

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
                        topics + (since,),
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
        )
        # Don't call start() — we don't need the pool or graph, just storage
        replica._connect = self._storage.read_only_connection  # type: ignore[method-assign]
        return replica

    # ------------------------------------------------------------------ #
    # Backward-compatibility: _context_bundle was called with (agent_id)
    # in the old monolithic core.py. The new method takes (agent_id, parent_id).
    # ------------------------------------------------------------------ #

    def _context_bundle(self, agent_id: str) -> dict[str, Any]:
        """Deprecated: use _build_context_bundle(agent_id, parent_id=None) instead."""
        return self._build_context_bundle(agent_id, None)