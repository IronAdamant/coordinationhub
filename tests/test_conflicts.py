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
