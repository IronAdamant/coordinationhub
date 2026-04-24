"""Tests for document locking: acquire, release, refresh, status, list, reap.

Covers file-level locks, region locks, shared/exclusive semantics.
"""

from __future__ import annotations

import pytest
import time


class TestLockAcquisition:
    def test_acquire_new_lock(self, engine, registered_agent):
        result = engine.acquire_lock("/test.txt", registered_agent)
        assert result["acquired"] is True
        assert result["locked_by"] == registered_agent
        assert result["document_path"]

    def test_acquire_same_agent_refreshes(self, engine, registered_agent):
        engine.acquire_lock("/test.txt", registered_agent)
        result = engine.acquire_lock("/test.txt", registered_agent)
        assert result["acquired"] is True

    def test_acquire_contested_lock(self, engine, two_agents):
        engine.acquire_lock("/test.txt", two_agents["child"])
        result = engine.acquire_lock("/test.txt", two_agents["other"])
        assert result["acquired"] is False
        assert result["locked_by"] == two_agents["child"]

    def test_acquire_force_steal(self, engine, two_agents):
        engine.acquire_lock("/test.txt", two_agents["child"])
        result = engine.acquire_lock("/test.txt", two_agents["other"], force=True)
        assert result["acquired"] is True
        assert result["locked_by"] == two_agents["other"]

    def test_force_steal_leaves_exactly_one_lock_row(self, engine, two_agents):
        """Regression test for T1.1: force-steal must be atomic.

        Before the fix, record_conflict used conflict_log's `with connect()`
        wrapper which committed the outer BEGIN IMMEDIATE transaction
        mid-flight. After record_conflict but before DELETE+INSERT, the
        outer tx was closed, which broke atomicity of the force-steal.
        The happy path still 'worked' but DB invariants weren't guaranteed
        under concurrency.

        This test asserts the end-state invariant: exactly one lock row
        on the contested path after a force-steal.
        """
        engine.acquire_lock("/contested.txt", two_agents["child"])
        engine.acquire_lock("/contested.txt", two_agents["other"], force=True)
        with engine._connect() as conn:
            rows = conn.execute(
                "SELECT locked_by FROM document_locks WHERE document_path = ?",
                ("/contested.txt",),
            ).fetchall()
        assert len(rows) == 1
        assert rows[0]["locked_by"] == two_agents["other"]

    def test_force_steal_holds_outer_transaction(self, engine, two_agents):
        """Regression test for T1.1: during force-steal, the outer BEGIN
        IMMEDIATE transaction must remain active between the conflict-log
        write and the DELETE of the stolen lock. Otherwise a concurrent
        force-stealer can squeeze in during that window and produce
        duplicate lock rows.

        Technique: wrap conn.execute to record every statement issued,
        so we can see whether a COMMIT fires between the INSERT into
        lock_conflicts and the DELETE from document_locks.
        """
        engine.acquire_lock("/concurrency.txt", two_agents["child"])

        # Use sqlite3's set_trace_callback to record every statement issued
        # on the thread-local conn during the force-steal.
        conn = engine._connect()
        statements: list[str] = []

        def trace(sql: str) -> None:
            first = sql.strip().split()[0].upper() if sql.strip() else ""
            statements.append(first)

        conn.set_trace_callback(trace)
        try:
            engine.acquire_lock("/concurrency.txt", two_agents["other"], force=True)
        finally:
            conn.set_trace_callback(None)

        # Locate the outer BEGIN IMMEDIATE and the DELETE that removes the
        # stolen lock row. Between them there must be no COMMIT, which
        # would indicate the outer transaction was closed prematurely.
        begin_idx = next(
            (i for i, s in enumerate(statements) if s in ("BEGIN",)),
            -1,
        )
        last_delete_idx = max(
            (i for i, s in enumerate(statements) if s == "DELETE"), default=-1
        )
        assert begin_idx >= 0, f"Expected a BEGIN statement; got: {statements}"
        assert last_delete_idx > begin_idx, (
            f"Expected DELETE after BEGIN; got: {statements}"
        )
        commits_during_tx = [
            i for i, s in enumerate(statements)
            if s == "COMMIT" and begin_idx < i < last_delete_idx
        ]
        assert commits_during_tx == [], (
            f"No COMMIT may fire between BEGIN and DELETE during force-steal; "
            f"statements: {statements}"
        )

    def test_force_steal_scope_violation_rolls_back_conflict_log(self, engine):
        """T1.1: force-steal rejected by scope violation must NOT leave a
        phantom 'lock_stolen' row in lock_conflicts. Before the fix,
        record_conflict committed the outer tx, so when scope check
        rejected the acquire at ROLLBACK time, only the force-steal's
        DELETE+INSERT rolled back — the conflict log entry was already
        committed and stayed behind as a ghost 'force_overwritten' record.
        """
        import json
        import time as _time

        victim = engine.generate_agent_id()
        engine.register_agent(victim)
        scoped = engine.generate_agent_id()
        engine.register_agent(scoped)
        with engine._connect() as conn:
            conn.execute(
                "INSERT INTO agent_responsibilities (agent_id, scope, updated_at) "
                "VALUES (?, ?, ?)",
                (scoped, json.dumps(["src/"]), _time.time()),
            )

        engine.acquire_lock("/tests/test.py", victim)
        result = engine.acquire_lock("/tests/test.py", scoped, force=True)

        assert result["acquired"] is False
        assert result.get("error") == "scope_violation"

        with engine._connect() as conn:
            lock_rows = conn.execute(
                "SELECT locked_by FROM document_locks WHERE document_path = ?",
                ("/tests/test.py",),
            ).fetchall()
            conflict_rows = conn.execute(
                "SELECT * FROM lock_conflicts"
            ).fetchall()
        assert len(lock_rows) == 1
        assert lock_rows[0]["locked_by"] == victim
        assert len(conflict_rows) == 0, (
            f"Scope-violation rollback must also roll back the conflict log "
            f"entry; found phantom rows: {[dict(r) for r in conflict_rows]}"
        )

    def test_concurrent_force_steal_with_delay_produces_one_lock(self, engine):
        """T1.1 strengthened concurrent test: inject a delay inside
        record_conflict so the race window between `record_conflict` and
        `DELETE` is wide enough to manifest reliably.

        Without the fix, thread A's record_conflict commits the outer tx,
        then sleeps — thread B grabs BEGIN IMMEDIATE, sees the victim's
        lock still present, records its own conflict (also commits its
        outer tx), etc. When both threads finish, two lock rows exist on
        the same path.
        """
        import time as _time
        from coordinationhub import lock_ops as _lo
        from coordinationhub import core_locking as _cl
        from .conftest import run_concurrent

        victim = engine.generate_agent_id()
        engine.register_agent(victim)
        stealer_a = engine.generate_agent_id()
        engine.register_agent(stealer_a)
        stealer_b = engine.generate_agent_id()
        engine.register_agent(stealer_b)
        engine.acquire_lock("/race.txt", victim)

        orig = _lo.record_conflict

        def slow_record_conflict(*args, **kwargs):
            result = orig(*args, **kwargs)
            _time.sleep(0.05)  # wide race window
            return result

        _lo.record_conflict = slow_record_conflict
        _cl._lo = _lo
        try:
            results, errors = run_concurrent(
                2, lambda aid: engine.acquire_lock("/race.txt", aid, force=True),
                args_per_worker=[(stealer_a,), (stealer_b,)], timeout=15.0,
            )
        finally:
            _lo.record_conflict = orig
            _cl._lo = _lo

        assert not errors, f"Errors during concurrent force-steal: {errors}"

        with engine._connect() as conn:
            rows = conn.execute(
                "SELECT locked_by FROM document_locks WHERE document_path = ?",
                ("/race.txt",),
            ).fetchall()
        assert len(rows) == 1, (
            f"Expected exactly 1 lock row after concurrent force-steal with "
            f"injected delay, got {len(rows)}: {[dict(r) for r in rows]}. "
            f"This indicates T1.1's outer-transaction-commit bug has returned."
        )

    def test_concurrent_force_steal_produces_one_lock(self, engine):
        """T1.1 concurrent regression: two threads force-steal from a shared
        prior holder at the same time. Invariant: after both complete, there
        is exactly one lock row and the conflict log records both steals.

        Before the fix, record_conflict's `with connect()` closed the outer
        BEGIN IMMEDIATE early. A second force-stealer starting BEGIN IMMEDIATE
        between the first's record_conflict and DELETE could produce two
        lock rows on the same path.
        """
        from .conftest import run_concurrent

        victim = engine.generate_agent_id()
        engine.register_agent(victim)
        stealer_a = engine.generate_agent_id()
        engine.register_agent(stealer_a)
        stealer_b = engine.generate_agent_id()
        engine.register_agent(stealer_b)

        engine.acquire_lock("/shared.txt", victim)

        def steal(aid):
            return engine.acquire_lock("/shared.txt", aid, force=True)

        results, errors = run_concurrent(
            2, steal, args_per_worker=[(stealer_a,), (stealer_b,)]
        )
        assert not errors, f"Unexpected errors: {errors}"

        with engine._connect() as conn:
            rows = conn.execute(
                "SELECT locked_by FROM document_locks WHERE document_path = ?",
                ("/shared.txt",),
            ).fetchall()
        # Invariant: at most one lock row. (With broken transaction boundaries,
        # a race could produce two rows — both stealers "winning".)
        assert len(rows) == 1, (
            f"Expected exactly 1 lock row after concurrent force-steal, got "
            f"{len(rows)}: {[dict(r) for r in rows]}"
        )
        # The winner is whichever ran first; both claim to have acquired.
        assert all(r["acquired"] for r in results)

    def test_acquire_expired_lock(self, engine, registered_agent):
        """An expired lock can be taken over without force."""
        engine.acquire_lock("/test.txt", registered_agent, ttl=0.01)
        time.sleep(0.02)
        other = engine.generate_agent_id()
        engine.register_agent(other)
        result = engine.acquire_lock("/test.txt", other)
        assert result["acquired"] is True


class TestReapCacheCoherence:
    """T1.3 regression: reap_expired must not wipe cache entries added by
    concurrent acquire_lock calls. The fix wraps reap DELETE + SELECT +
    warm() in one BEGIN IMMEDIATE so a concurrent acquirer is serialised."""

    def test_reap_wraps_delete_and_warm_in_one_transaction(self, engine):
        """T1.3 regression: the DELETE and the subsequent SELECT that drives
        warm() must live inside one BEGIN IMMEDIATE so a concurrent
        acquire_lock cannot interleave between them (which would let the
        cache warm with a snapshot that misses the concurrent add_lock).

        Technique: sqlite3 trace_callback records every statement. The
        invariant is that no COMMIT appears between the DELETE and the
        SELECT that feeds warm.
        """
        # Seed an expired lock so reap has work to do
        expired_holder = engine.generate_agent_id()
        engine.register_agent(expired_holder)
        engine.acquire_lock("/expired.txt", expired_holder, ttl=0.01)
        time.sleep(0.03)

        conn = engine._connect()
        statements: list[str] = []

        def trace(sql: str) -> None:
            first = sql.strip().split()[0].upper() if sql.strip() else ""
            statements.append(first)

        conn.set_trace_callback(trace)
        try:
            engine.admin_locks("reap_expired")
        finally:
            conn.set_trace_callback(None)

        # Find the DELETE from document_locks and the SELECT that feeds warm.
        delete_idx = next(
            (i for i, s in enumerate(statements) if s == "DELETE"), -1
        )
        # The SELECT driving warm is the last SELECT after DELETE.
        select_after_delete_idx = max(
            (i for i, s in enumerate(statements)
             if s == "SELECT" and i > delete_idx),
            default=-1,
        )
        assert delete_idx >= 0 and select_after_delete_idx > delete_idx, (
            f"Expected DELETE followed by SELECT in reap_expired; statements: {statements}"
        )
        commits_between = [
            i for i, s in enumerate(statements)
            if s == "COMMIT" and delete_idx < i < select_after_delete_idx
        ]
        assert commits_between == [], (
            f"No COMMIT may fire between reap's DELETE and the SELECT that "
            f"feeds lock_cache.warm — a concurrent acquire_lock could "
            f"interleave and have its cache entry wiped. Statements: {statements}"
        )

    def test_reap_releases_and_reacquires_locks_preserves_cache(self, engine):
        """Sanity test: after reap_expired runs, the cache still matches DB
        for all non-expired locks.
        """
        active_holder = engine.generate_agent_id()
        engine.register_agent(active_holder)
        engine.acquire_lock("/still_active.txt", active_holder, ttl=60.0)

        expired_holder = engine.generate_agent_id()
        engine.register_agent(expired_holder)
        engine.acquire_lock("/expired.txt", expired_holder, ttl=0.01)
        time.sleep(0.03)

        engine.admin_locks("reap_expired")

        # The active lock must still be in the cache
        status = engine.get_lock_status("/still_active.txt")
        assert status["locked"] is True
        # The expired lock must be gone from both DB and cache
        status2 = engine.get_lock_status("/expired.txt")
        assert status2["locked"] is False


class TestLockRelease:
    def test_release_owned_lock(self, engine, registered_agent):
        engine.acquire_lock("/test.txt", registered_agent)
        result = engine.release_lock("/test.txt", registered_agent)
        assert result["released"] is True

    def test_release_not_owner(self, engine, two_agents):
        engine.acquire_lock("/test.txt", two_agents["child"])
        result = engine.release_lock("/test.txt", two_agents["other"])
        assert result["released"] is False
        assert result["reason"] == "not_owner"

    def test_release_not_locked(self, engine, registered_agent):
        result = engine.release_lock("/nonexistent.txt", registered_agent)
        assert result["released"] is False
        assert result["reason"] == "not_locked"


class TestLockRefresh:
    def test_refresh_owned_lock(self, engine, registered_agent):
        engine.acquire_lock("/test.txt", registered_agent)
        result = engine.refresh_lock("/test.txt", registered_agent, ttl=60.0)
        assert result["refreshed"] is True
        assert result["expires_at"] > 0

    def test_refresh_not_owner(self, engine, two_agents):
        engine.acquire_lock("/test.txt", two_agents["child"])
        result = engine.refresh_lock("/test.txt", two_agents["other"])
        assert result["refreshed"] is False
        assert result["reason"] == "not_owner"

    def test_refresh_not_locked(self, engine, registered_agent):
        result = engine.refresh_lock("/nonexistent.txt", registered_agent)
        assert result["refreshed"] is False
        assert result["reason"] == "not_locked"

    def test_refresh_expired_lock_rejected(self, engine, registered_agent):
        """T1.4 regression: refresh_lock must reject a lock whose TTL has
        expired. Previously there was no expiry check — an expired lock
        (which another agent could have legitimately acquired in the
        meantime) could be silently resurrected via refresh.
        """
        engine.acquire_lock("/expiring.txt", registered_agent, ttl=0.01)
        time.sleep(0.03)
        result = engine.refresh_lock("/expiring.txt", registered_agent, ttl=60.0)
        assert result["refreshed"] is False
        assert result.get("reason") == "expired"


class TestDuplicateLockPrevention:
    """T1.4 regression: re-acquiring an expired own-lock must UPDATE the
    existing row, not INSERT a duplicate. Previously find_own_lock filtered
    by expiry so expired rows were invisible to the acquire path, which
    then INSERTed a new row — duplicate rows accumulated until reap ran.
    """

    def test_reacquire_expired_own_lock_updates_not_duplicates(self, engine, registered_agent):
        engine.acquire_lock("/reacquire.txt", registered_agent, ttl=0.01)
        time.sleep(0.03)
        # Re-acquire without running reap — the old expired row is still in
        # the table but invisible to find_own_lock pre-fix.
        engine.acquire_lock("/reacquire.txt", registered_agent, ttl=60.0)

        with engine._connect() as conn:
            rows = conn.execute(
                "SELECT id FROM document_locks WHERE document_path = ? AND locked_by = ?",
                ("/reacquire.txt", registered_agent),
            ).fetchall()
        assert len(rows) == 1, (
            f"Re-acquiring an expired own lock must UPDATE the existing row, "
            f"not INSERT a new one. Got {len(rows)} rows: {[dict(r) for r in rows]}. "
            f"This indicates T1.4's duplicate-row accumulation has returned."
        )

    def test_multiple_reacquires_still_single_row(self, engine, registered_agent):
        """Repeated acquire/expire cycles must still produce at most one row."""
        for _ in range(5):
            engine.acquire_lock("/cyclic.txt", registered_agent, ttl=0.01)
            time.sleep(0.02)

        with engine._connect() as conn:
            rows = conn.execute(
                "SELECT id FROM document_locks WHERE document_path = ? AND locked_by = ?",
                ("/cyclic.txt", registered_agent),
            ).fetchall()
        assert len(rows) == 1, (
            f"After 5 acquire/expire cycles there should be 1 lock row, got {len(rows)}"
        )


class TestLockStatus:
    def test_lock_status_locked(self, engine, registered_agent):
        engine.acquire_lock("/test.txt", registered_agent)
        status = engine.get_lock_status("/test.txt")
        assert status["locked"] is True
        assert status["locked_by"] == registered_agent

    def test_lock_status_expired(self, engine, registered_agent):
        engine.acquire_lock("/test.txt", registered_agent, ttl=0.01)
        time.sleep(0.02)
        status = engine.get_lock_status("/test.txt")
        assert status["locked"] is False

    def test_lock_status_not_locked(self, engine, registered_agent):
        status = engine.get_lock_status("/nonexistent.txt")
        assert status["locked"] is False


class TestListLocks:
    def test_list_locks_empty(self, engine, registered_agent):
        result = engine.list_locks()
        assert result["count"] == 0
        assert result["locks"] == []

    def test_list_locks_shows_active(self, engine, registered_agent):
        engine.acquire_lock("/a.txt", registered_agent)
        engine.acquire_lock("/b.txt", registered_agent)
        result = engine.list_locks()
        assert result["count"] == 2

    def test_list_locks_excludes_expired(self, engine, registered_agent):
        engine.acquire_lock("/expired.txt", registered_agent, ttl=0.01)
        engine.acquire_lock("/active.txt", registered_agent, ttl=300.0)
        time.sleep(0.02)
        result = engine.list_locks()
        assert result["count"] == 1
        assert "active.txt" in result["locks"][0]["document_path"]

    def test_list_locks_filter_by_agent(self, engine, two_agents):
        engine.acquire_lock("/a.txt", two_agents["child"])
        engine.acquire_lock("/b.txt", two_agents["other"])
        result = engine.list_locks(agent_id=two_agents["child"])
        assert result["count"] == 1
        assert result["locks"][0]["locked_by"] == two_agents["child"]

    def test_list_locks_includes_details(self, engine, registered_agent):
        engine.acquire_lock("/test.txt", registered_agent, ttl=120.0)
        result = engine.list_locks()
        lock = result["locks"][0]
        assert "document_path" in lock
        assert "locked_by" in lock
        assert "locked_at" in lock
        assert "expires_at" in lock
        assert "lock_type" in lock
        assert lock["locked_by"] == registered_agent


class TestListLocksForceRefresh:
    """T6.33: ``force_refresh=True`` re-warms the in-memory lock cache
    from SQLite. Covers the recovery path if the cache ever desyncs.
    """

    def test_force_refresh_picks_up_db_row_missing_from_cache(
        self, engine, registered_agent
    ):
        # Simulate a desync: insert a row straight into SQLite, bypassing
        # the cache-maintaining acquire path.
        now = time.time()
        with engine._connect() as conn:
            conn.execute(
                "INSERT INTO document_locks (document_path, locked_by, locked_at, "
                "lock_ttl, lock_type, region_start, region_end, worktree_root) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("/orphan.txt", registered_agent, now, 300.0, "exclusive", None, None,
                 str(engine._storage.project_root)),
            )

        # Default read misses it (cache wasn't told).
        assert engine.list_locks()["count"] == 0

        # force_refresh picks it up.
        refreshed = engine.list_locks(force_refresh=True)
        assert refreshed["count"] == 1
        assert refreshed["locks"][0]["document_path"] == "/orphan.txt"

        # Cache is now in sync; subsequent non-forced read also sees it.
        assert engine.list_locks()["count"] == 1

    def test_force_refresh_drops_stale_cache_entry(self, engine, registered_agent):
        # Acquire populates both cache and DB.
        engine.acquire_lock("/ghost.txt", registered_agent, ttl=300.0)
        assert engine.list_locks()["count"] == 1

        # Out-of-band DELETE from DB — cache still thinks the lock is live.
        with engine._connect() as conn:
            conn.execute("DELETE FROM document_locks WHERE document_path = ?",
                         ("/ghost.txt",))

        # Cache read still shows the ghost.
        assert engine.list_locks()["count"] == 1

        # force_refresh clears it.
        assert engine.list_locks(force_refresh=True)["count"] == 0


class TestSharedLocks:
    """Tests for shared lock semantics — multiple shared locks allowed."""

    def test_shared_locks_no_conflict(self, engine, two_agents):
        """Two shared locks on the same file should both succeed."""
        r1 = engine.acquire_lock("/shared.txt", two_agents["child"], lock_type="shared")
        r2 = engine.acquire_lock("/shared.txt", two_agents["other"], lock_type="shared")
        assert r1["acquired"] is True
        assert r2["acquired"] is True

    def test_exclusive_blocks_shared(self, engine, two_agents):
        """An exclusive lock blocks a subsequent shared lock."""
        engine.acquire_lock("/test.txt", two_agents["child"], lock_type="exclusive")
        result = engine.acquire_lock("/test.txt", two_agents["other"], lock_type="shared")
        assert result["acquired"] is False

    def test_shared_blocks_exclusive(self, engine, two_agents):
        """A shared lock blocks a subsequent exclusive lock."""
        engine.acquire_lock("/test.txt", two_agents["child"], lock_type="shared")
        result = engine.acquire_lock("/test.txt", two_agents["other"], lock_type="exclusive")
        assert result["acquired"] is False

    def test_three_shared_locks(self, engine):
        """Three agents can all hold shared locks on the same file."""
        agents = []
        for i in range(3):
            aid = engine.generate_agent_id()
            engine.register_agent(aid)
            agents.append(aid)
        for aid in agents:
            result = engine.acquire_lock("/multi.txt", aid, lock_type="shared")
            assert result["acquired"] is True
        status = engine.get_lock_status("/multi.txt")
        assert status["locked"] is True
        assert "holders" in status
        assert len(status["holders"]) == 3

    def test_release_one_shared_keeps_others(self, engine, two_agents):
        """Releasing one shared lock doesn't affect the other."""
        engine.acquire_lock("/shared.txt", two_agents["child"], lock_type="shared")
        engine.acquire_lock("/shared.txt", two_agents["other"], lock_type="shared")
        engine.release_lock("/shared.txt", two_agents["child"])
        status = engine.get_lock_status("/shared.txt")
        assert status["locked"] is True
        assert status["locked_by"] == two_agents["other"]


class TestRegionLocks:
    """Tests for region-level locking — concurrent edits to non-overlapping regions."""

    def test_non_overlapping_regions_no_conflict(self, engine, two_agents):
        """Two exclusive locks on non-overlapping regions should both succeed."""
        r1 = engine.acquire_lock("/app.js", two_agents["child"], region_start=1, region_end=50)
        r2 = engine.acquire_lock("/app.js", two_agents["other"], region_start=51, region_end=100)
        assert r1["acquired"] is True
        assert r2["acquired"] is True

    def test_overlapping_regions_conflict(self, engine, two_agents):
        """Two exclusive locks on overlapping regions should conflict."""
        engine.acquire_lock("/app.js", two_agents["child"], region_start=1, region_end=60)
        result = engine.acquire_lock("/app.js", two_agents["other"], region_start=50, region_end=100)
        assert result["acquired"] is False

    def test_whole_file_conflicts_with_region(self, engine, two_agents):
        """A whole-file lock conflicts with any region lock."""
        engine.acquire_lock("/app.js", two_agents["child"])  # whole file
        result = engine.acquire_lock("/app.js", two_agents["other"], region_start=1, region_end=10)
        assert result["acquired"] is False

    def test_region_conflicts_with_whole_file(self, engine, two_agents):
        """A region lock conflicts with a whole-file lock request."""
        engine.acquire_lock("/app.js", two_agents["child"], region_start=1, region_end=10)
        result = engine.acquire_lock("/app.js", two_agents["other"])  # whole file
        assert result["acquired"] is False

    def test_shared_overlapping_regions_no_conflict(self, engine, two_agents):
        """Shared locks on overlapping regions should not conflict."""
        r1 = engine.acquire_lock("/app.js", two_agents["child"], lock_type="shared",
                                 region_start=1, region_end=60)
        r2 = engine.acquire_lock("/app.js", two_agents["other"], lock_type="shared",
                                 region_start=50, region_end=100)
        assert r1["acquired"] is True
        assert r2["acquired"] is True

    def test_release_region_lock(self, engine, two_agents):
        """Release a specific region lock without affecting other regions."""
        engine.acquire_lock("/app.js", two_agents["child"], region_start=1, region_end=50)
        engine.acquire_lock("/app.js", two_agents["other"], region_start=51, region_end=100)
        engine.release_lock("/app.js", two_agents["child"], region_start=1, region_end=50)
        # Other agent's lock should still be held
        locks = engine.list_locks(agent_id=two_agents["other"])
        assert locks["count"] == 1

    def test_refresh_region_lock(self, engine, registered_agent):
        """Refresh a region lock by specifying the region."""
        engine.acquire_lock("/app.js", registered_agent, region_start=1, region_end=50, ttl=60.0)
        result = engine.refresh_lock("/app.js", registered_agent, ttl=120.0,
                                     region_start=1, region_end=50)
        assert result["refreshed"] is True

    def test_list_locks_includes_region(self, engine, registered_agent):
        """list_locks returns region info."""
        engine.acquire_lock("/app.js", registered_agent, region_start=10, region_end=20)
        result = engine.list_locks()
        assert result["count"] == 1
        lock = result["locks"][0]
        assert lock["region_start"] == 10
        assert lock["region_end"] == 20

    def test_same_agent_multiple_regions(self, engine, registered_agent):
        """Same agent can hold locks on multiple non-overlapping regions."""
        r1 = engine.acquire_lock("/app.js", registered_agent, region_start=1, region_end=50)
        r2 = engine.acquire_lock("/app.js", registered_agent, region_start=51, region_end=100)
        assert r1["acquired"] is True
        assert r2["acquired"] is True
        locks = engine.list_locks(agent_id=registered_agent)
        assert locks["count"] == 2

    def test_force_steal_region_lock(self, engine, two_agents):
        """Force-steal a conflicting region lock."""
        engine.acquire_lock("/app.js", two_agents["child"], region_start=1, region_end=50)
        result = engine.acquire_lock("/app.js", two_agents["other"], region_start=1, region_end=50, force=True)
        assert result["acquired"] is True
        assert result["locked_by"] == two_agents["other"]

    def test_acquire_returns_region_info(self, engine, registered_agent):
        """acquire_lock response includes region_start/region_end."""
        result = engine.acquire_lock("/app.js", registered_agent, region_start=5, region_end=15)
        assert result["region_start"] == 5
        assert result["region_end"] == 15


class TestLockReaping:
    def test_reap_expired_locks(self, engine, registered_agent):
        engine.acquire_lock("/test.txt", registered_agent, ttl=0.01)
        time.sleep(0.02)
        result = engine.admin_locks(action="reap_expired")
        assert result["reaped"] >= 1

    def test_reap_spares_active_agent_locks(self, engine, registered_agent):
        """Expired locks held by agents with a recent heartbeat are spared."""
        engine.acquire_lock("/active.txt", registered_agent, ttl=0.01)
        time.sleep(0.02)
        # Lock is expired, but agent has a fresh heartbeat
        engine.heartbeat(registered_agent)
        result = engine.admin_locks(action="reap_expired", grace_seconds=120.0)
        assert result["reaped"] == 0
        # Lock still exists
        status = engine.get_lock_status("/active.txt")
        assert status["locked"] is True

    def test_reap_removes_crashed_agent_locks(self, engine, registered_agent):
        """Expired locks from agents with stale heartbeats are reaped normally."""
        engine.acquire_lock("/stale.txt", registered_agent, ttl=0.01)
        time.sleep(0.02)
        # Simulate stale agent: set heartbeat far in the past
        with engine._connect() as conn:
            conn.execute(
                "UPDATE agents SET last_heartbeat = 0 WHERE agent_id = ?",
                (registered_agent,),
            )
        result = engine.admin_locks(action="reap_expired", grace_seconds=120.0)
        assert result["reaped"] >= 1

    def test_release_agent_locks(self, engine, two_agents):
        engine.acquire_lock("/a.txt", two_agents["child"])
        engine.acquire_lock("/b.txt", two_agents["child"])
        result = engine.admin_locks(action="release_by_agent", agent_id=two_agents["child"])
        assert result["released"] == 2

    def test_release_agent_locks_includes_regions(self, engine, registered_agent):
        """release_agent_locks releases both whole-file and region locks."""
        engine.acquire_lock("/a.txt", registered_agent)
        engine.acquire_lock("/b.txt", registered_agent, region_start=1, region_end=50)
        result = engine.admin_locks(action="release_by_agent", agent_id=registered_agent)
        assert result["released"] == 2


class TestBroadcastAcknowledgment:
    def _heartbeat_all(self, engine, two_agents):
        engine.heartbeat(two_agents["parent"])
        engine.heartbeat(two_agents["child"])
        engine.heartbeat(two_agents["other"])

    def test_broadcast_without_require_ack(self, engine, two_agents):
        self._heartbeat_all(engine, two_agents)
        # broadcast from child so other is a sibling
        result = engine.broadcast(two_agents["child"])
        assert "broadcast_id" not in result
        assert "acknowledged_by" in result

    def test_broadcast_with_require_ack_creates_broadcast(self, engine, two_agents):
        self._heartbeat_all(engine, two_agents)
        child = two_agents["child"]
        other = two_agents["other"]

        result = engine.broadcast(child, require_ack=True, message="hello")
        assert "broadcast_id" in result
        assert result["pending_acks"] == [other]
        assert result["acknowledged_by"] == []

    def test_acknowledge_broadcast(self, engine, two_agents):
        self._heartbeat_all(engine, two_agents)
        child = two_agents["child"]
        other = two_agents["other"]

        broadcast = engine.broadcast(child, require_ack=True)
        bid = broadcast["broadcast_id"]

        result = engine.acknowledge_broadcast(bid, other)
        assert result["acknowledged"] is True

        status = engine.wait_for_broadcast_acks(bid, timeout_s=1.0)
        assert status["timed_out"] is False
        assert other in status["acknowledged_by"]

    def test_acknowledge_expired_broadcast_fails(self, engine, two_agents):
        self._heartbeat_all(engine, two_agents)
        child = two_agents["child"]
        other = two_agents["other"]

        broadcast = engine.broadcast(child, require_ack=True, ttl=0.01)
        bid = broadcast["broadcast_id"]
        time.sleep(0.02)

        result = engine.acknowledge_broadcast(bid, other)
        assert result["acknowledged"] is False
        assert result["reason"] == "expired_or_not_found"

    def test_wait_for_broadcast_acks_not_found(self, engine):
        status = engine.wait_for_broadcast_acks(99999, timeout_s=0.1)
        assert status["timed_out"] is True
        assert status["reason"] == "not_found"

    def test_broadcast_sends_ack_request_messages(self, engine, two_agents):
        self._heartbeat_all(engine, two_agents)
        child = two_agents["child"]
        other = two_agents["other"]

        engine.broadcast(child, require_ack=True, message="test")

        msgs = engine.get_messages(other, unread_only=True)
        assert msgs["count"] == 1
        assert msgs["messages"][0]["message_type"] == "broadcast_ack_request"
        assert msgs["messages"][0]["payload"]["message"] == "test"


class TestGetMessagesNoAutoAck:
    """T6.24: reading a broadcast_ack_request must NOT implicitly ack.
    Previously the auto-ack happened as a side effect of get_messages,
    so a crash after fetch-but-before-process produced ghost acks.
    """

    def _heartbeat(self, engine, *ids):
        for aid in ids:
            engine.heartbeat(aid)

    def test_get_messages_does_not_auto_ack_broadcast(self, engine, two_agents):
        self._heartbeat(engine, two_agents["parent"], two_agents["child"], two_agents["other"])
        child = two_agents["child"]
        other = two_agents["other"]

        broadcast = engine.broadcast(child, require_ack=True, message="hi")
        bid = broadcast["broadcast_id"]

        # Recipient fetches messages — pre-fix this auto-acked.
        msgs = engine.get_messages(other, unread_only=True)
        assert msgs["count"] == 1

        status = engine.get_broadcast_status(bid)
        # Pending must still list `other` — no implicit ack happened.
        assert other in status["pending_acks"]
        assert status["acknowledged_by"] == []

        # Explicit ack works and moves the bookkeeping.
        engine.acknowledge_broadcast(bid, other)
        status = engine.get_broadcast_status(bid)
        assert status["acknowledged_by"] == [other]
        assert status["pending_acks"] == []


class TestScopeBoundaryPrecision:
    """T3.23: scope check must compare path components, not character
    prefixes. Pre-fix ``docs/security`` leaked through a scope of
    ``docs/sec`` because raw startswith treats ``docs/sec`` as a prefix
    of ``docs/security/x``.
    """

    def test_similar_prefix_does_not_satisfy_scope(self, engine):
        import json
        import time as _time

        agent = engine.generate_agent_id()
        engine.register_agent(agent)
        with engine._connect() as conn:
            conn.execute(
                "INSERT INTO agent_responsibilities (agent_id, scope, updated_at) "
                "VALUES (?, ?, ?)",
                (agent, json.dumps(["docs/sec"]), _time.time()),
            )

        # docs/security is NOT inside docs/sec, even though startswith
        # would say otherwise.
        result = engine.acquire_lock("docs/security/plan.md", agent)
        assert result["acquired"] is False
        assert result.get("error") == "scope_violation"

    def test_exact_scope_dir_accepted(self, engine):
        import json
        import time as _time

        agent = engine.generate_agent_id()
        engine.register_agent(agent)
        with engine._connect() as conn:
            conn.execute(
                "INSERT INTO agent_responsibilities (agent_id, scope, updated_at) "
                "VALUES (?, ?, ?)",
                (agent, json.dumps(["src/"]), _time.time()),
            )
        # Files inside the scope dir are accepted.
        result = engine.acquire_lock("src/foo.py", agent)
        assert result["acquired"] is True


class TestBroadcastTargetSnapshot:
    """T1.11: broadcast snapshots target list so pending_acks is computable
    and late-joiners are excluded.
    """

    def _heartbeat(self, engine, *agent_ids):
        for aid in agent_ids:
            engine.heartbeat(aid)

    def test_status_shows_real_pending_acks(self, engine, two_agents):
        """Pre-fix get_broadcast_status always returned pending_acks=[].
        Post-fix it returns the snapshot minus the acks."""
        self._heartbeat(engine, two_agents["parent"], two_agents["child"], two_agents["other"])
        child = two_agents["child"]
        other = two_agents["other"]

        broadcast = engine.broadcast(child, require_ack=True, message="hi")
        bid = broadcast["broadcast_id"]

        status = engine.get_broadcast_status(bid)
        assert status["pending_acks"] == [other]
        assert status["acknowledged_by"] == []

        engine.acknowledge_broadcast(bid, other)
        status = engine.get_broadcast_status(bid)
        assert status["pending_acks"] == []
        assert status["acknowledged_by"] == [other]

    def test_late_joiner_excluded_from_targets(self, engine, two_agents):
        """An agent that registers after the broadcast fires isn't a target
        and doesn't appear in pending_acks.
        """
        self._heartbeat(engine, two_agents["parent"], two_agents["child"], two_agents["other"])
        parent = two_agents["parent"]
        child = two_agents["child"]
        other = two_agents["other"]

        broadcast = engine.broadcast(child, require_ack=True, message="early")
        bid = broadcast["broadcast_id"]

        # Register a new sibling after the broadcast.
        late = engine.generate_agent_id(parent)
        engine.register_agent(late, parent)
        engine.heartbeat(late)

        status = engine.get_broadcast_status(bid)
        assert late not in status["pending_acks"]
        assert status["pending_acks"] == [other]

    def test_targets_persist_in_broadcast_targets_table(self, engine, two_agents):
        """The snapshot is stored in the broadcast_targets table."""
        self._heartbeat(engine, two_agents["parent"], two_agents["child"], two_agents["other"])
        child = two_agents["child"]
        other = two_agents["other"]

        broadcast = engine.broadcast(child, require_ack=True)
        bid = broadcast["broadcast_id"]

        with engine._connect() as conn:
            rows = conn.execute(
                "SELECT agent_id FROM broadcast_targets WHERE broadcast_id = ?",
                (bid,),
            ).fetchall()
            target_ids = {r["agent_id"] for r in rows}

        assert target_ids == {other}

    def test_expected_count_matches_target_count(self, engine, two_agents):
        """When targets are supplied, expected_count is derived from len(targets),
        so callers can't pass a mismatched count.
        """
        self._heartbeat(engine, two_agents["parent"], two_agents["child"], two_agents["other"])
        child = two_agents["child"]

        broadcast = engine.broadcast(child, require_ack=True)
        status = engine.get_broadcast_status(broadcast["broadcast_id"])
        assert status["expected_count"] == len(status["pending_acks"]) + len(status["acknowledged_by"])
