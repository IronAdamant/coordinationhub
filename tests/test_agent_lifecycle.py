"""Tests for agent lifecycle: register, heartbeat, deregister, lineage, siblings."""

from __future__ import annotations

import pytest


class TestAgentRegistration:
    def test_generate_agent_id_root(self, engine):
        aid = engine.generate_agent_id()
        assert aid.startswith("hub.")
        assert "." in aid

    def test_generate_agent_id_child(self, engine, registered_agent):
        child = engine.generate_agent_id(registered_agent)
        assert child.startswith(registered_agent + ".")
        assert child != registered_agent

    def test_register_agent(self, engine, registered_agent):
        result = engine.register_agent(registered_agent)
        assert result["agent_id"] == registered_agent
        assert result["worktree_root"]

    def test_register_agent_with_parent(self, engine, registered_agent):
        child = engine.generate_agent_id(registered_agent)
        result = engine.register_agent(child, parent_id=registered_agent)
        assert result["agent_id"] == child

    def test_register_unknown_parent_raises(self, engine):
        with pytest.raises(ValueError, match="Parent agent not found"):
            engine.generate_agent_id("nonexistent.parent.0")

    def test_heartbeat(self, engine, registered_agent):
        result = engine.heartbeat(registered_agent)
        assert result["updated"] is True

    def test_heartbeat_nonexistent(self, engine):
        result = engine.heartbeat("nonexistent.agent")
        assert result["updated"] is False

    def test_deregister_agent(self, engine, two_agents):
        result = engine.deregister_agent(two_agents["child"])
        assert result["deregistered"] is True

    def test_deregister_orphans_children(self, engine, two_agents):
        """When a parent is deregistered, children are re-parented to grandparent."""
        engine.deregister_agent(two_agents["parent"])
        lineage = engine.get_lineage(two_agents["child"])
        # Child's parent_id should now be None (grandparent doesn't exist)
        assert lineage["ancestors"] == []  # reparented to None

    def test_deregister_deletes_stale_lineage_rows(self, engine):
        """Orphaned children have their lineage row with dead parent deleted."""
        parent = engine.generate_agent_id()
        engine.register_agent(parent)
        child = engine.generate_agent_id(parent)
        engine.register_agent(child, parent_id=parent)

        # Verify lineage row exists
        with engine._connect() as conn:
            row = conn.execute(
                "SELECT * FROM lineage WHERE parent_id = ? AND child_id = ?",
                (parent, child),
            ).fetchone()
            assert row is not None

        engine.deregister_agent(parent)

        # Lineage row for (parent, child) should be gone
        with engine._connect() as conn:
            row = conn.execute(
                "SELECT * FROM lineage WHERE parent_id = ? AND child_id = ?",
                (parent, child),
            ).fetchone()
            assert row is None

    def test_list_agents(self, engine, two_agents):
        result = engine.list_agents()
        assert len(result["agents"]) == 3

    def test_list_agents_active_only(self, engine, two_agents):
        engine.deregister_agent(two_agents["other"])
        result = engine.list_agents(active_only=True)
        assert all(a["status"] == "active" for a in result["agents"])

    def test_list_agents_stale_detection(self, engine, registered_agent):
        result = engine.list_agents(stale_timeout=0.1)
        assert len(result["agents"]) == 1
        assert result["agents"][0]["stale"] is False


class TestAgentLineage:
    def test_get_lineage_empty_for_new_agent(self, engine, registered_agent):
        lineage = engine.get_lineage(registered_agent)
        assert lineage["ancestors"] == []
        assert lineage["descendants"] == []

    def test_get_lineage_ancestors(self, engine):
        root = engine.generate_agent_id()
        engine.register_agent(root)
        child = engine.generate_agent_id(root)
        engine.register_agent(child, parent_id=root)
        grandchild = engine.generate_agent_id(child)
        engine.register_agent(grandchild, parent_id=child)

        lineage = engine.get_lineage(grandchild)
        assert len(lineage["ancestors"]) == 2

    def test_get_lineage_descendants(self, engine, two_agents):
        # two_agents has parent, child, other (siblings under same parent)
        lineage = engine.get_lineage(two_agents["parent"])
        assert len(lineage["descendants"]) == 2

    def test_get_lineage_ancestor_parent_ids_preserved(self, engine):
        """Ancestor entries preserve their own parent_id, not set to None."""
        root = engine.generate_agent_id()
        engine.register_agent(root)
        child = engine.generate_agent_id(root)
        engine.register_agent(child, parent_id=root)

        lineage = engine.get_lineage(child)
        # First ancestor should be root, and root's parent_id should be preserved (may be None or a value)
        assert lineage["ancestors"][0]["agent_id"] == root
        assert "parent_id" in lineage["ancestors"][0]

    def test_get_siblings(self, engine, two_agents):
        result = engine.get_siblings(two_agents["child"])
        siblings = result["siblings"]
        sibling_ids = [s["agent_id"] for s in siblings]
        assert two_agents["other"] in sibling_ids
        assert two_agents["child"] not in sibling_ids


class TestReaping:
    def test_reap_stale_agents(self, engine, registered_agent):
        """Set an old heartbeat then reap."""
        with engine._connect() as conn:
            old_time = 1000.0  # ancient timestamp
            conn.execute(
                "UPDATE agents SET last_heartbeat = ? WHERE agent_id = ?",
                (old_time, registered_agent),
            )
        result = engine.reap_stale_agents(timeout=600.0)
        assert result["reaped"] >= 1

    def test_reap_stale_agents_orphans_children(self, engine):
        """When a parent is reaped, its children are re-parented to the grandparent."""
        parent = engine.generate_agent_id()
        engine.register_agent(parent)
        child = engine.generate_agent_id(parent)
        engine.register_agent(child, parent_id=parent)

        # Make parent ancient so it gets reaped
        with engine._connect() as conn:
            conn.execute(
                "UPDATE agents SET last_heartbeat = 0 WHERE agent_id = ?",
                (parent,),
            )
        result = engine.reap_stale_agents(timeout=600.0)
        assert result["orphaned_children"] == 1

    def test_reap_cleans_lineage_rows(self, engine):
        """Reaping a stale agent removes its lineage entries for orphaned children."""
        parent = engine.generate_agent_id()
        engine.register_agent(parent)
        child = engine.generate_agent_id(parent)
        engine.register_agent(child, parent_id=parent)

        with engine._connect() as conn:
            conn.execute(
                "UPDATE agents SET last_heartbeat = 0 WHERE agent_id = ?",
                (parent,),
            )
        engine.reap_stale_agents(timeout=600.0)

        # The lineage row (parent, child) should be deleted
        with engine._connect() as conn:
            row = conn.execute(
                "SELECT * FROM lineage WHERE parent_id = ? AND child_id = ?",
                (parent, child),
            ).fetchone()
            assert row is None
