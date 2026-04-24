"""BroadcastMixin — broadcast, handoff dispatch, and cross-agent waits.

Historically extracted from ``core_locking`` to stay under the 500-LOC
budget. Post-T6.22 LockingMixin is itself extracted to
:mod:`locking_subsystem` as :class:`Locking`; this mixin still calls
:py:meth:`get_lock_status` on ``self`` and the engine's facade method
delegates to ``self._locking`` so the MRO lookup keeps working. When
Broadcast is itself extracted later in the series it will take an
explicit ``locking`` dep instead of relying on MRO resolution.
"""

from __future__ import annotations

import time
from typing import Any

from . import agent_registry as _ar
from . import broadcasts as _bc
from . import handoffs as _handoffs
from . import messages as _msg
from .paths import normalize_path


class BroadcastMixin:
    """Mixin providing broadcast, handoff, and multi-lock wait primitives.

    Expects the host class to provide:
    - ``_connect() -> sqlite3.Connection``
    - ``_storage.project_root``
    - ``_publish_event(topic, payload)``
    - ``_hybrid_wait(topics, filter_fn, timeout)``
    - ``get_lock_status(document_path)``  (facade on the engine — resolves
      to ``self._locking.get_lock_status`` via the :class:`Locking`
      subsystem post-T6.22)
    """

    def broadcast(
        self, agent_id: str, document_path: str | None = None, ttl: float = 30.0,
        handoff_targets: list[str] | None = None,
        require_ack: bool = False, message: str | None = None,
    ) -> dict[str, Any]:
        """Announce an intention to siblings, or perform a formal multi-recipient handoff.

        When handoff_targets is provided, acts as a formal handoff: records to the
        handoffs table and sends handoff messages to each target agent.

        When require_ack is True, creates a trackable broadcast record and sends
        acknowledgment request messages to each live sibling. Recipients must call
        acknowledge_broadcast to confirm receipt.
        """
        if handoff_targets:
            return self._handoff(agent_id, handoff_targets, document_path)

        siblings = _ar.get_siblings(self._connect, agent_id)
        now = time.time()
        live_siblings = [s for s in siblings if now - s.get("last_heartbeat", 0) <= ttl]

        if require_ack and live_siblings:
            sibling_ids = [s["agent_id"] for s in live_siblings]
            # T1.11: snapshot the target list at broadcast time so
            # pending_acks is computable and late-joiners are excluded.
            result = _bc.record_broadcast(
                self._connect, agent_id, document_path, message, ttl,
                len(live_siblings), targets=sibling_ids,
            )
            broadcast_id = result["broadcast_id"]
            for sib_id in sibling_ids:
                _msg.send_message(
                    self._connect, agent_id, sib_id, "broadcast_ack_request",
                    {"broadcast_id": broadcast_id, "document_path": document_path, "message": message},
                )
            self._publish_event(
                "broadcast.created",
                {
                    "broadcast_id": broadcast_id,
                    "agent_id": agent_id,
                    "document_path": document_path,
                    "pending_acks": sibling_ids,
                },
            )
            return {
                "broadcast_id": broadcast_id,
                "acknowledged_by": [],
                "pending_acks": sibling_ids,
                "conflicts": [],
            }

        acknowledged_by: list[str] = []
        conflicts: list[dict[str, Any]] = []
        sibling_ids = [s["agent_id"] for s in live_siblings]
        if document_path and sibling_ids:
            norm_path = normalize_path(document_path, self._storage.project_root)
            placeholders = ",".join("?" * len(sibling_ids))
            with self._connect() as conn:
                lock_rows = conn.execute(
                    f"SELECT locked_by FROM document_locks WHERE document_path = ? "
                    f"AND locked_by IN ({placeholders})",
                    [norm_path] + sibling_ids,
                ).fetchall()
                for row in lock_rows:
                    if row["locked_by"] != agent_id:
                        conflicts.append({"document_path": document_path, "locked_by": row["locked_by"]})
        return {"acknowledged_by": acknowledged_by, "conflicts": conflicts}

    def acknowledge_broadcast(
        self, broadcast_id: int, agent_id: str,
    ) -> dict[str, Any]:
        """Acknowledge receipt of a broadcast."""
        result = _bc.acknowledge_broadcast(self._connect, broadcast_id, agent_id)
        if result.get("acknowledged"):
            self._publish_event(
                "broadcast.ack",
                {"broadcast_id": broadcast_id, "agent_id": agent_id},
            )
        return result

    def get_broadcast_status(
        self, broadcast_id: int,
    ) -> dict[str, Any]:
        """Get the current acknowledgment status for a broadcast."""
        return _bc.get_broadcast_status(self._connect, broadcast_id)

    def wait_for_broadcast_acks(
        self, broadcast_id: int, timeout_s: float = 30.0,
    ) -> dict[str, Any]:
        """Wait until all expected acknowledgments are received or timeout expires.

        Uses the event bus for low-latency notification and falls back to the
        SQLite event journal for cross-process synchronization.
        Returns the final broadcast status, including acknowledged_by and pending_acks.
        """
        start = time.time()
        status = self.get_broadcast_status(broadcast_id)
        if not status.get("found"):
            return {"timed_out": True, "reason": "not_found"}

        if status.get("expires_at", 0) < time.time():
            return {
                "timed_out": True,
                "reason": "expired",
                "acknowledged_by": status.get("acknowledged_by", []),
            }

        expected = status.get("expected_count", 0)
        if expected <= 0:
            return {"timed_out": False, "acknowledged_by": status.get("acknowledged_by", [])}

        acked = set(status.get("acknowledged_by", []))
        while len(acked) < expected:
            elapsed = time.time() - start
            if elapsed >= timeout_s:
                break
            event = self._hybrid_wait(
                ["broadcast.ack"],
                filter_fn=lambda e: e.get("broadcast_id") == broadcast_id,
                timeout=timeout_s - elapsed,
            )
            if event is None:
                break
            acked.add(event.get("agent_id"))

        # T1.11: re-read status so pending_acks reflects the snapshot.
        final = self.get_broadcast_status(broadcast_id)
        return {
            "timed_out": len(acked) < expected,
            "acknowledged_by": list(acked),
            "pending_acks": final.get("pending_acks", []),
        }

    def _handoff(
        self, agent_id: str, to_agents: list[str],
        document_path: str | None = None, handoff_type: str = "scope_transfer",
    ) -> dict[str, Any]:
        """Formal multi-recipient handoff."""
        result = _handoffs.record_handoff(
            self._connect, agent_id, to_agents, document_path, handoff_type,
        )
        handoff_id = result["handoff_id"]
        for target in to_agents:
            _msg.send_message(
                self._connect, agent_id, target, "handoff",
                {"handoff_id": handoff_id, "document_path": document_path,
                 "handoff_type": handoff_type},
            )
        self._publish_event(
            "handoff.created",
            {"handoff_id": handoff_id, "from_agent_id": agent_id,
             "to_agents": to_agents, "document_path": document_path,
             "handoff_type": handoff_type},
        )
        return {
            "handoff_id": handoff_id, "to_agents": to_agents,
            "document_path": document_path, "handoff_type": handoff_type,
        }

    def wait_for_locks(
        self, document_paths: list[str], agent_id: str, timeout_s: float = 60.0,
    ) -> dict[str, Any]:
        start = time.time()
        paths_set = {normalize_path(p, self._storage.project_root) for p in document_paths}
        released: list[str] = []

        for path in list(paths_set):
            status = self.get_lock_status(path)
            if not status.get("locked", False) or status.get("locked_by") == agent_id:
                released.append(path)
                paths_set.remove(path)

        while paths_set:
            elapsed = time.time() - start
            if elapsed >= timeout_s:
                break
            event = self._hybrid_wait(
                ["lock.released"],
                filter_fn=lambda e: e.get("document_path") in paths_set,
                timeout=timeout_s - elapsed,
            )
            if event is None:
                break
            released.append(event["document_path"])
            paths_set.remove(event["document_path"])

        return {"released": released, "timed_out": list(paths_set)}
