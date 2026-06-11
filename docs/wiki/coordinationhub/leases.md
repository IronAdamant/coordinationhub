# coordinationhub/leases.py

Zero-deps lease primitives for HA coordinator leadership: named TTL leases in the `coordinator_leases` table, with acquire/refresh/release and failover claim semantics.

## Key Functions / Classes
- `LeaseHolder` — NamedTuple (lease_name, holder_id, acquired_at, ttl, expires_at) returned by `get_lease_holder`.
- `acquire_lease(conn, lease_name, holder_id, ttl)` — two-phase acquire: lightweight INSERT fast path, then `BEGIN IMMEDIATE` check-and-update if the row exists; returns the new `expires_at` (truthy) on success, `None` if a live holder owns the lease.
- `refresh_lease(conn, lease_name, holder_id)` — extend expiry; only the current holder may refresh; returns new `expires_at` or `None`.
- `release_lease(conn, lease_name, holder_id)` — delete the row if held by this holder; True/False.
- `get_lease_holder(conn, lease_name)` / `is_lease_expired(conn, lease_name)` — read-side helpers (missing lease counts as expired).
- `claim_leadership(conn, lease_name, agent_id, ttl)` — failover claim: takes an expired/unheld lease via `INSERT OR REPLACE`; never steals from a live holder.

## Design Notes
- Takes a raw `sqlite3.Connection` (not a `connect` callable) — no internal pool dependency; functions manage their own COMMIT/ROLLBACK.
- T1.5: `acquired_at` is sampled *after* `BEGIN IMMEDIATE` returns — the wait can last up to busy_timeout (30 s), and a pre-wait timestamp would store an `expires_at` earlier than the caller's effective window.
- The IntegrityError path in `acquire_lease` must `rollback()` first: sqlite3's implicit-transaction machinery leaves `in_transaction=True` after a failed INSERT, and without the rollback the subsequent `BEGIN IMMEDIATE` raises "cannot start a transaction within a transaction" forever on that thread-local connection.
- Returning the timestamp inline (T6.30) saves wrappers a follow-up `get_lease_holder` round trip.
- HA failover safety: the old leader's lease expires naturally via TTL; new leader's `acquired_at > old expires_at` makes the stale lease inert.
- T7.31: explicit COMMIT on success / ROLLBACK on bail-out so empty check-and-return paths don't waste a COMMIT.

## Relationships
Imports: (none within the package — stdlib only)
Imported by: `lease_subsystem.py`
