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

from pathlib import Path
from typing import Any

from ._storage import CoordinationStorage
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
from . import graphs as _g


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
        self._graph = None  # set in start()

    def start(self) -> None:
        """Start storage and load coordination graph."""
        self._storage.start()
        self._graph = _g.load_coordination_spec_from_disk(
            self._connect, self._storage.project_root,
        )

    def close(self) -> None:
        """Close storage, checkpoint WAL."""
        self._storage.close()

    def _connect(self):
        """Return a connection from the thread-local pool."""
        return self._storage._connect()

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