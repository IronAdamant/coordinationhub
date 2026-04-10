"""Tests for conflict logging and lineage table with composite primary key."""

from __future__ import annotations

import pytest


class TestConflictLogging:
    def test_record_conflict(self, engine, two_agents):
        from coordinationhub.conflict_log import record_conflict
        cid = record_conflict(
            engine._connect,
            "/test.txt",
            two_agents["child"],
            two_agents["other"],
            "lock_stolen",
        )
        assert cid is not None
        with engine._connect() as conn:
            row = conn.execute(
                "SELECT * FROM lock_conflicts WHERE id = ?", (cid,)
            ).fetchone()
            assert row["conflict_type"] == "lock_stolen"

    def test_record_conflict_data_integrity(self, engine, two_agents):
        """Verify record_conflict stores correct number of columns (no shifting)."""
        from coordinationhub.conflict_log import record_conflict
        cid = record_conflict(
            engine._connect,
            "/test.txt",
            two_agents["child"],
            two_agents["other"],
            "lock_stolen",
            resolution="force_overwritten",
        )
        with engine._connect() as conn:
            row = conn.execute(
                "SELECT conflict_type, resolution, details_json FROM lock_conflicts WHERE id = ?",
                (cid,),
            ).fetchone()
            # resolution should be 'force_overwritten', not shifted into details_json
            assert row["resolution"] == "force_overwritten"
            assert row["details_json"] is None

    def test_get_conflicts(self, engine, two_agents):
        from coordinationhub.conflict_log import record_conflict
        record_conflict(
            engine._connect,
            "/test.txt",
            two_agents["child"],
            two_agents["other"],
            "lock_stolen",
        )
        result = engine.get_conflicts()
        assert len(result["conflicts"]) >= 1

    def test_get_conflicts_by_agent(self, engine, two_agents):
        from coordinationhub.conflict_log import record_conflict
        record_conflict(
            engine._connect,
            "/test.txt",
            two_agents["child"],
            two_agents["other"],
            "lock_stolen",
        )
        result = engine.get_conflicts(agent_id=two_agents["other"])
        assert all(
            two_agents["other"] in (c["agent_a"], c["agent_b"])
            for c in result["conflicts"]
        )


class TestBoundaryCrossing:
    """Tests for ownership-aware locking — boundary crossing detection."""

    def test_acquire_lock_no_warning_when_no_ownership(self, engine, registered_agent):
        """No ownership_warning when file_ownership table has no entry for the file."""
        result = engine.acquire_lock("/unowned.txt", registered_agent)
        assert result["acquired"] is True
        assert "ownership_warning" not in result

    def test_acquire_lock_no_warning_when_same_owner(self, engine, registered_agent):
        """No warning when the locking agent owns the file."""
        with engine._connect() as conn:
            import time
            conn.execute(
                "INSERT INTO file_ownership (document_path, assigned_agent_id, assigned_at) VALUES (?, ?, ?)",
                ("unowned.txt", registered_agent, time.time()),
            )
        result = engine.acquire_lock("/unowned.txt", registered_agent)
        assert result["acquired"] is True
        assert "ownership_warning" not in result

    def _insert_ownership(self, engine, rel_path, agent_id):
        """Insert a file_ownership entry using the same normalization as acquire_lock."""
        from coordinationhub.paths import normalize_path
        import time
        norm = normalize_path(rel_path, engine._storage.project_root)
        with engine._connect() as conn:
            conn.execute(
                "INSERT INTO file_ownership (document_path, assigned_agent_id, assigned_at) VALUES (?, ?, ?)",
                (norm, agent_id, time.time()),
            )

    def test_acquire_lock_warns_on_boundary_crossing(self, engine, two_agents):
        """Warning when agent locks a file owned by another agent."""
        self._insert_ownership(engine, "shared.txt", two_agents["child"])
        result = engine.acquire_lock("shared.txt", two_agents["other"])
        assert result["acquired"] is True
        assert "ownership_warning" in result
        assert result["ownership_warning"]["owned_by"] == two_agents["child"]

    def test_boundary_crossing_records_conflict(self, engine, two_agents):
        """Boundary crossing records a conflict in the log."""
        self._insert_ownership(engine, "owned.txt", two_agents["child"])
        engine.acquire_lock("owned.txt", two_agents["other"])
        conflicts = engine.get_conflicts()["conflicts"]
        boundary = [c for c in conflicts if c["conflict_type"] == "boundary_crossing"]
        assert len(boundary) == 1
        assert boundary[0]["agent_a"] == two_agents["child"]
        assert boundary[0]["agent_b"] == two_agents["other"]

    def test_boundary_crossing_fires_notification(self, engine, two_agents):
        """Boundary crossing fires a change notification."""
        self._insert_ownership(engine, "notify.txt", two_agents["child"])
        engine.acquire_lock("notify.txt", two_agents["other"])
        notifs = engine.get_notifications()["notifications"]
        boundary = [n for n in notifs if n["change_type"] == "boundary_crossing"]
        assert len(boundary) == 1
        assert boundary[0]["agent_id"] == two_agents["other"]

    def test_self_lock_refresh_no_boundary_warning(self, engine, two_agents):
        """Re-acquiring own lock (refresh) should not trigger boundary warning."""
        self._insert_ownership(engine, "refresh.txt", two_agents["child"])
        engine.acquire_lock("refresh.txt", two_agents["other"])
        # Second acquire is a refresh — returns early before ownership check
        result = engine.acquire_lock("refresh.txt", two_agents["other"])
        assert result["acquired"] is True
        assert "ownership_warning" not in result


class TestContentionHotspots:
    """Tests for the contention hotspots tool."""

    def test_empty_hotspots(self, engine):
        """No hotspots when no conflicts exist."""
        result = engine.get_contention_hotspots()
        assert result["hotspots"] == []
        assert result["total"] == 0

    def test_hotspots_ranked_by_count(self, engine, two_agents):
        """Files ranked by conflict count descending."""
        from coordinationhub.conflict_log import record_conflict
        # 3 conflicts on file A, 1 on file B
        for _ in range(3):
            record_conflict(engine._connect, "hot.txt",
                            two_agents["child"], two_agents["other"], "lock_stolen")
        record_conflict(engine._connect, "warm.txt",
                        two_agents["child"], two_agents["other"], "lock_stolen")
        result = engine.get_contention_hotspots()
        assert result["total"] == 2
        assert result["hotspots"][0]["document_path"] == "hot.txt"
        assert result["hotspots"][0]["conflict_count"] == 3
        assert result["hotspots"][1]["document_path"] == "warm.txt"
        assert result["hotspots"][1]["conflict_count"] == 1

    def test_hotspots_includes_all_agents(self, engine):
        """Hotspot entry includes all agents involved (both sides of conflicts)."""
        from coordinationhub.conflict_log import record_conflict
        a1 = engine.generate_agent_id()
        engine.register_agent(a1)
        a2 = engine.generate_agent_id()
        engine.register_agent(a2)
        a3 = engine.generate_agent_id()
        engine.register_agent(a3)
        record_conflict(engine._connect, "multi.txt", a1, a2, "lock_stolen")
        record_conflict(engine._connect, "multi.txt", a1, a3, "lock_denied")
        result = engine.get_contention_hotspots()
        agents = result["hotspots"][0]["agents_involved"]
        assert a1 in agents
        assert a2 in agents
        assert a3 in agents

    def test_hotspots_limit(self, engine, two_agents):
        """Limit parameter caps the number of hotspots returned."""
        from coordinationhub.conflict_log import record_conflict
        for i in range(5):
            record_conflict(engine._connect, f"file{i}.txt",
                            two_agents["child"], two_agents["other"], "lock_stolen")
        result = engine.get_contention_hotspots(limit=2)
        assert result["total"] == 2


class TestLineageTable:
    def test_lineage_composite_pk_allows_multiple_children(self, engine):
        """Verify lineage table accepts multiple children for the same parent."""
        parent = engine.generate_agent_id()
        engine.register_agent(parent)

        child1 = engine.generate_agent_id(parent)
        engine.register_agent(child1, parent)
        child2 = engine.generate_agent_id(child1)
        engine.register_agent(child2, child1)
        child3 = engine.generate_agent_id(child1)
        engine.register_agent(child3, child1)

        with engine._connect() as conn:
            rows = conn.execute(
                "SELECT parent_id, child_id FROM lineage"
            ).fetchall()
            # Should have 3 entries (parent->child1, child1->child2, child1->child3)
            assert len(rows) == 3

    def test_lineage_index_on_parent(self, engine):
        """Verify idx_lineage_parent index exists for efficient lineage walks."""
        with engine._connect() as conn:
            indexes = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_lineage_parent'"
            ).fetchone()
            assert indexes is not None
