"""LeaseMixin — HA coordinator lease management.

Expects the host class to provide:
    self._connect() — callable returning a sqlite3 connection

Delegates to: leases (leases.py) for lease primitives.
"""

from __future__ import annotations

from typing import Any

from . import leases as _leases


class LeaseMixin:
    """HA coordinator lease management via coordinator_leases table."""

    COORDINATOR_LEASE = "COORDINATOR_LEADER"
    # T6.23: renamed from DEFAULT_TTL so it doesn't collide with
    # LockingMixin.DEFAULT_TTL (300s) when both mixins land on the same
    # engine via multiple inheritance.
    DEFAULT_LEASE_TTL = 10.0  # 10-second lease — must refresh within TTL
    # Back-compat alias (kept for callers that read LeaseMixin.DEFAULT_TTL
    # explicitly). Prefer DEFAULT_LEASE_TTL going forward. NOTE: on a
    # subclass that also inherits LockingMixin, bare ``self.DEFAULT_TTL``
    # resolves to whichever mixin appears first in the MRO — always use
    # the typed attribute (DEFAULT_LEASE_TTL / DEFAULT_LOCK_TTL) from
    # new code.
    DEFAULT_TTL = DEFAULT_LEASE_TTL

    # ------------------------------------------------------------------ #
    # Lease Management
    # ------------------------------------------------------------------ #

    def manage_leases(
        self,
        action: str,
        agent_id: str | None = None,
        ttl: float | None = None,
    ) -> dict[str, Any]:
        """Unified lease management: acquire | refresh | release | get | claim.

        agent_id is required for acquire/refresh/release/claim. For `get` it is
        ignored (the coordinator lease is a singleton).
        """
        if action == "get":
            return {"leader": self.get_leader()}
        if agent_id is None:
            return {"error": f"action={action!r} requires agent_id"}
        if action == "acquire":
            return self.acquire_coordinator_lease(agent_id, ttl)
        if action == "refresh":
            return self.refresh_coordinator_lease(agent_id)
        if action == "release":
            return self.release_coordinator_lease(agent_id)
        if action == "claim":
            return self.claim_leadership(agent_id, ttl)
        return {"error": f"Unknown action: {action!r}"}

    def acquire_coordinator_lease(
        self,
        agent_id: str,
        ttl: float | None = None,
    ) -> dict[str, Any]:
        """Attempt to acquire the coordinator leadership lease.

        Returns {"acquired": True} if this agent now holds the lease.
        Returns {"acquired": False, "holder": <current_holder>} if leadership
        is held by another agent.
        """
        ttl = ttl if ttl is not None else self.DEFAULT_LEASE_TTL
        conn = self._connect()
        ok = _leases.acquire_lease(conn, self.COORDINATOR_LEASE, agent_id, ttl)

        if ok:
            holder = _leases.get_lease_holder(conn, self.COORDINATOR_LEASE)
            result = {
                "acquired": True,
                "lease_name": self.COORDINATOR_LEASE,
                "holder_id": agent_id,
                "ttl": ttl,
                "expires_at": holder.expires_at if holder else None,
            }
            self._publish_event(
                "lease.acquired",
                {"lease_name": self.COORDINATOR_LEASE, "holder_id": agent_id, "ttl": ttl},
            )
            return result
        else:
            holder = _leases.get_lease_holder(conn, self.COORDINATOR_LEASE)
            return {
                "acquired": False,
                "lease_name": self.COORDINATOR_LEASE,
                "holder": holder._asdict() if holder else None,
            }

    def refresh_coordinator_lease(self, agent_id: str) -> dict[str, Any]:
        """Refresh the coordinator lease TTL.

        Returns {"refreshed": True} if the refresh succeeded.
        Returns {"refreshed": False, "error": ...} if not the current holder.
        """
        conn = self._connect()
        ok = _leases.refresh_lease(conn, self.COORDINATOR_LEASE, agent_id)
        if not ok:
            return {"refreshed": False, "error": "Not the current lease holder"}
        holder = _leases.get_lease_holder(conn, self.COORDINATOR_LEASE)
        result = {
            "refreshed": True,
            "lease_name": self.COORDINATOR_LEASE,
            "expires_at": holder.expires_at if holder else None,
        }
        self._publish_event(
            "lease.refreshed",
            {"lease_name": self.COORDINATOR_LEASE, "holder_id": agent_id},
        )
        return result

    def release_coordinator_lease(self, agent_id: str) -> dict[str, Any]:
        """Release the coordinator lease.

        Returns {"released": True} if the lease was released.
        Returns {"released": False, "error": ...} if not the current holder.
        """
        conn = self._connect()
        ok = _leases.release_lease(conn, self.COORDINATOR_LEASE, agent_id)
        if not ok:
            return {"released": False, "error": "Not the current lease holder"}
        self._publish_event(
            "lease.released",
            {"lease_name": self.COORDINATOR_LEASE, "holder_id": agent_id},
        )
        return {"released": True, "lease_name": self.COORDINATOR_LEASE}

    def is_leader(self, agent_id: str) -> bool:
        """Return True if the given agent holds the coordinator lease."""
        conn = self._connect()
        holder = _leases.get_lease_holder(conn, self.COORDINATOR_LEASE)
        if holder is None:
            return False
        return holder.holder_id == agent_id

    def get_leader(self) -> dict[str, Any] | None:
        """Return the current coordinator lease holder, or None if unheld."""
        conn = self._connect()
        holder = _leases.get_lease_holder(conn, self.COORDINATOR_LEASE)
        if holder is None:
            return None
        return holder._asdict()

    def claim_leadership(self, agent_id: str, ttl: float | None = None) -> dict[str, Any]:
        """Attempt to claim coordinator leadership from a failed leader.

        The claim succeeds only if the current lease is expired or unheld.
        It is NOT taken from a live holder — that would be unsafe.

        Use this when a replica detects the leader has failed (missed its
        heartbeat, lease expired). After claiming, rebuild any in-memory
        state from the DB.

        Returns {"claimed": True} on success.
        Returns {"claimed": False, "error": ...} if leadership is held by a live agent.
        """
        ttl = ttl if ttl is not None else self.DEFAULT_LEASE_TTL
        conn = self._connect()
        ok = _leases.claim_leadership(conn, self.COORDINATOR_LEASE, agent_id, ttl)
        if not ok:
            holder = _leases.get_lease_holder(conn, self.COORDINATOR_LEASE)
            return {
                "claimed": False,
                "error": "Leadership is held by a live agent",
                "lease_name": self.COORDINATOR_LEASE,
                "holder": holder._asdict() if holder else None,
            }
        holder = _leases.get_lease_holder(conn, self.COORDINATOR_LEASE)
        result = {
            "claimed": True,
            "lease_name": self.COORDINATOR_LEASE,
            "holder_id": agent_id,
            "ttl": ttl,
            "expires_at": holder.expires_at if holder else None,
        }
        self._publish_event(
            "lease.claimed",
            {"lease_name": self.COORDINATOR_LEASE, "holder_id": agent_id, "ttl": ttl},
        )
        return result
