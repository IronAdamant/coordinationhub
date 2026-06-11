# coordinationhub/lease_subsystem.py

HA coordinator leadership via a singleton lease (`COORDINATOR_LEADER`) in the `coordinator_leases` table: acquire, refresh, release, query, and failover claim. Third T6.22 extraction from `core_leases.LeaseMixin`; exposed as `engine._lease`.

## Key Functions / Classes
- `Lease` — subsystem class; two-dep ctor (`connect_fn`, `publish_event_fn`).
- `Lease.manage_leases(action, agent_id, ttl)` — unified dispatch: `acquire | refresh | release | get | claim`; `agent_id` required for all but `get`.
- `Lease.acquire_coordinator_lease(agent_id, ttl)` — `{"acquired": True, expires_at}` or `{"acquired": False, "holder": ...}`; publishes `lease.acquired`.
- `Lease.refresh_coordinator_lease(agent_id)` — holder-only TTL refresh; publishes `lease.refreshed`.
- `Lease.release_coordinator_lease(agent_id)` — holder-only release; publishes `lease.released`.
- `Lease.is_leader(agent_id)` / `Lease.get_leader()` — leadership queries.
- `Lease.claim_leadership(agent_id, ttl)` — failover claim; publishes `lease.claimed`.
- `COORDINATOR_LEASE = "COORDINATOR_LEADER"`, `DEFAULT_LEASE_TTL = 10.0` (with legacy `DEFAULT_TTL` alias).

## Design Notes
- T6.23: the TTL constant was renamed `DEFAULT_LEASE_TTL` to avoid colliding with `LockingMixin.DEFAULT_TTL` (300s) when both landed on the engine via multiple inheritance; `DEFAULT_TTL` is kept as a back-compat alias — prefer `DEFAULT_LEASE_TTL` in new code.
- 10-second lease: the holder must refresh within the TTL or leadership becomes claimable.
- `claim_leadership` succeeds only when the lease is expired or unheld — it is never taken from a live holder (unsafe). After claiming, callers should rebuild in-memory state from the DB.
- T6.30: the acquire/refresh/claim primitives return `expires_at` inline on success (None on failure), eliminating the follow-up `get_lease_holder` round trip on the hot path; the failure path still fetches the holder row for the error payload.
- Holder rows are namedtuples — results use `holder._asdict()`.

## Relationships
Imports: `leases` (lease DB primitives)
Imported by: `core.py` (constructed by `CoordinationEngine`), `identity_subsystem.py` (injected dep for lease release on deregister)
