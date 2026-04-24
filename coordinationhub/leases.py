"""Zero-deps lease primitives for HA coordinator leadership.

Receives connect: ConnectFn from the caller — no internal pool dependency.
"""

from __future__ import annotations

import sqlite3
import time
from typing import NamedTuple


class LeaseHolder(NamedTuple):
    """Holder information returned by get_lease_holder."""
    lease_name: str
    holder_id: str
    acquired_at: float
    ttl: float
    expires_at: float


def acquire_lease(
    conn: sqlite3.Connection,
    lease_name: str,
    holder_id: str,
    ttl: float,
) -> float | None:
    """Attempt to acquire a named lease.

    Uses a two-phase approach:
    1. Try a lightweight INSERT first (no explicit transaction needed).
       If it succeeds the lease is ours.
    2. If INSERT fails (row exists), use BEGIN IMMEDIATE to serialize
       the check-and-update, preventing races from implicit transactions.

    Returns the new expires_at on success, or None if another live holder
    currently owns the lease. Truthy-on-success for callers that only
    care about the boolean outcome; carries the timestamp so wrappers
    (T6.30) don't need a follow-up get_lease_holder round trip.
    """
    # T1.5: sample acquired_at *after* any potential BEGIN IMMEDIATE wait.
    # If BEGIN IMMEDIATE has to wait on another writer (up to busy_timeout =
    # 30 s), a timestamp captured here would be stale by that amount and the
    # stored expires_at would be earlier than the caller's effective window.
    # Fast path: try direct insert (works when no row exists).
    acquired_at = time.time()
    expires_at = acquired_at + ttl
    try:
        conn.execute(
            "INSERT INTO coordinator_leases (lease_name, holder_id, acquired_at, ttl, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (lease_name, holder_id, acquired_at, ttl, expires_at),
        )
        conn.commit()
        return expires_at
    except sqlite3.IntegrityError:
        # Python's sqlite3 implicit-transaction machinery auto-begins on DML,
        # and IntegrityError leaves the connection with in_transaction=True.
        # Without this rollback, the subsequent BEGIN IMMEDIATE raises
        # "cannot start a transaction within a transaction" and this whole
        # function returns False forever on the thread-local connection.
        conn.rollback()

    # Row exists — use an immediate transaction to safely read + update
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError:
        # Another writer is active; bail out
        return None

    # T1.5: re-sample after BEGIN IMMEDIATE so expires_at reflects the time
    # we actually entered the critical section, not the time we started
    # waiting for it.
    acquired_at = time.time()
    expires_at = acquired_at + ttl

    try:
        cursor = conn.execute(
            "SELECT holder_id, expires_at FROM coordinator_leases WHERE lease_name = ?",
            (lease_name,),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        existing_holder = row["holder_id"]
        existing_expires = row["expires_at"]

        # Reject if another valid holder has the lease
        if existing_holder != holder_id and existing_expires > acquired_at:
            return None

        # Overwrite: either unheld (expired) or ours
        conn.execute(
            "UPDATE coordinator_leases SET holder_id = ?, acquired_at = ?, ttl = ?, expires_at = ? "
            "WHERE lease_name = ?",
            (holder_id, acquired_at, ttl, expires_at, lease_name),
        )
        return expires_at
    finally:
        conn.commit()

def refresh_lease(
    conn: sqlite3.Connection,
    lease_name: str,
    holder_id: str,
) -> float | None:
    """Refresh a lease's TTL, extending its expiry time.

    Only the current holder can refresh. Returns the new expires_at on
    success, or None if the caller isn't the current holder (T6.30: the
    timestamp is returned inline so wrappers skip a follow-up read).
    """
    now = time.time()

    # Use an immediate transaction to atomically check-and-update
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError:
        return None

    try:
        cursor = conn.execute(
            "SELECT holder_id, ttl FROM coordinator_leases WHERE lease_name = ?",
            (lease_name,),
        )
        row = cursor.fetchone()
        if row is None or row["holder_id"] != holder_id:
            return None

        ttl = row["ttl"]
        expires_at = now + ttl

        conn.execute(
            "UPDATE coordinator_leases SET acquired_at = ?, expires_at = ? WHERE lease_name = ?",
            (now, expires_at, lease_name),
        )
        return expires_at
    finally:
        conn.commit()


def release_lease(
    conn: sqlite3.Connection,
    lease_name: str,
    holder_id: str,
) -> bool:
    """Release a lease held by the given holder.

    Returns True if the lease was released, False if it wasn't held by this holder.
    """
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError:
        return False

    try:
        cursor = conn.execute(
            "SELECT holder_id FROM coordinator_leases WHERE lease_name = ?",
            (lease_name,),
        )
        row = cursor.fetchone()
        if row is None or row["holder_id"] != holder_id:
            return False

        conn.execute(
            "DELETE FROM coordinator_leases WHERE lease_name = ? AND holder_id = ?",
            (lease_name, holder_id),
        )
        return True
    finally:
        conn.commit()


def get_lease_holder(
    conn: sqlite3.Connection,
    lease_name: str,
) -> LeaseHolder | None:
    """Get the current holder of a lease, or None if unheld/expired.

    Returns None if the lease does not exist or has expired.
    """
    cursor = conn.execute(
        "SELECT lease_name, holder_id, acquired_at, ttl, expires_at "
        "FROM coordinator_leases WHERE lease_name = ?",
        (lease_name,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return LeaseHolder(
        lease_name=row["lease_name"],
        holder_id=row["holder_id"],
        acquired_at=row["acquired_at"],
        ttl=row["ttl"],
        expires_at=row["expires_at"],
    )


def is_lease_expired(conn: sqlite3.Connection, lease_name: str) -> bool:
    """Return True if the named lease is expired or does not exist."""
    cursor = conn.execute(
        "SELECT expires_at FROM coordinator_leases WHERE lease_name = ?",
        (lease_name,),
    )
    row = cursor.fetchone()
    if row is None:
        return True
    return row["expires_at"] <= time.time()


def claim_leadership(
    conn: sqlite3.Connection,
    lease_name: str,
    agent_id: str,
    ttl: float,
) -> float | None:
    """Claim leadership of a lease whose current holder has failed.

    Uses BEGIN IMMEDIATE to serialize races. If the current lease is
    expired (or unheld), this agent takes it. If the current holder is
    still valid, the claim is rejected — the lease is not stolen from
    a live holder.

    This is safe for HA failover: the old leader's lease expires naturally
    via TTL; the new leader's acquired_at > old leader's expires_at makes
    the old lease stale.

    Returns the new expires_at on success, or None if leadership is held
    by a live agent (T6.30).
    """
    # T1.5: BEGIN IMMEDIATE may wait up to busy_timeout for another writer
    # to release. Sample acquired_at *after* the wait so expires_at
    # represents the actual effective window.
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError:
        return None

    acquired_at = time.time()
    expires_at = acquired_at + ttl

    try:
        cursor = conn.execute(
            "SELECT holder_id, expires_at FROM coordinator_leases WHERE lease_name = ?",
            (lease_name,),
        )
        row = cursor.fetchone()

        if row is not None:
            existing_holder = row["holder_id"]
            existing_expires = row["expires_at"]

            # Reject if a live holder has the lease
            if existing_holder != agent_id and existing_expires > acquired_at:
                return None

        # Claim: insert if absent, or update if expired/unheld
        conn.execute(
            "INSERT OR REPLACE INTO coordinator_leases "
            "(lease_name, holder_id, acquired_at, ttl, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (lease_name, agent_id, acquired_at, ttl, expires_at),
        )
        return expires_at
    finally:
        conn.commit()
