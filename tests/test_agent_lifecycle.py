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

    def test_register_agent_tolerates_null_responsibilities_row(
        self, engine, registered_agent
    ):
        """``update_agent_status`` inserts an ``agent_responsibilities`` row
        without setting the nullable ``responsibilities`` column. The next
        ``register_agent`` call on the SAME agent id (or a re-registration
        after restart on a dirty DB) builds a context bundle that queries
        that row — which must not crash on ``json.loads(None)``.

        Regression: before v0.7.3, ``context.py`` used
        ``json.loads(resp.get("responsibilities", "[]"))`` — and
        ``dict.get(key, default)`` returns ``None`` (NOT the default) when
        the key is present but the value is ``None``, so this raised
        ``TypeError: the JSON object must be str, bytes or bytearray, not
        NoneType``.
        """
        # Populate the agent_responsibilities row for the already-registered
        # agent, leaving the ``responsibilities`` column at its NULL default.
        engine.update_agent_status(
            agent_id=registered_agent,
            current_task="some current task with a NULL responsibilities column",
        )
        # Re-register the same agent — register_agent is idempotent on the
        # agents table and always builds a fresh context bundle. The bundle
        # query hits the existing NULL-responsibilities row for this agent.
        result = engine.register_agent(registered_agent)
        assert result["agent_id"] == registered_agent
        # With a responsibilities row present, the bundle also returns the
        # populated fields
        assert result.get("current_task") == (
            "some current task with a NULL responsibilities column"
        )

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
        lineage = engine.get_agent_relations(two_agents["child"], mode="lineage")
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


class TestAgentRelations:
    def test_get_agent_relations_lineage_empty_for_new_agent(self, engine, registered_agent):
        lineage = engine.get_agent_relations(registered_agent, mode="lineage")
        assert lineage["ancestors"] == []
        assert lineage["descendants"] == []

    def test_get_agent_relations_lineage_ancestors(self, engine):
        root = engine.generate_agent_id()
        engine.register_agent(root)
        child = engine.generate_agent_id(root)
        engine.register_agent(child, parent_id=root)
        grandchild = engine.generate_agent_id(child)
        engine.register_agent(grandchild, parent_id=child)

        lineage = engine.get_agent_relations(grandchild, mode="lineage")
        assert len(lineage["ancestors"]) == 2

    def test_get_agent_relations_lineage_descendants(self, engine, two_agents):
        # two_agents has parent, child, other (siblings under same parent)
        lineage = engine.get_agent_relations(two_agents["parent"], mode="lineage")
        assert len(lineage["descendants"]) == 2

    def test_get_agent_relations_lineage_ancestor_parent_ids_preserved(self, engine):
        """Ancestor entries preserve their own parent_id, not set to None."""
        root = engine.generate_agent_id()
        engine.register_agent(root)
        child = engine.generate_agent_id(root)
        engine.register_agent(child, parent_id=root)

        lineage = engine.get_agent_relations(child, mode="lineage")
        # First ancestor should be root, and root's parent_id should be preserved (may be None or a value)
        assert lineage["ancestors"][0]["agent_id"] == root
        assert "parent_id" in lineage["ancestors"][0]

    def test_get_agent_relations_siblings(self, engine, two_agents):
        result = engine.get_agent_relations(two_agents["child"], mode="siblings")
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
        result = engine.admin_locks(action="reap_stale", timeout=600.0)
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
        result = engine.admin_locks(action="reap_stale", timeout=600.0)
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
        engine.admin_locks(action="reap_stale", timeout=600.0)

        # The lineage row (parent, child) should be deleted
        with engine._connect() as conn:
            row = conn.execute(
                "SELECT * FROM lineage WHERE parent_id = ? AND child_id = ?",
                (parent, child),
            ).fetchone()
            assert row is None


class TestDescendantRegistry:
    def test_descendant_registry_records_grandchild(self, engine):
        """C registers → B → A. A should immediately know about C via descendant_registry."""
        root = engine.generate_agent_id()
        engine.register_agent(root)
        child = engine.generate_agent_id(root)
        engine.register_agent(child, parent_id=root)
        grandchild = engine.generate_agent_id(child)
        engine.register_agent(grandchild, parent_id=child)

        from coordinationhub.agent_registry import get_descendants_status
        descendants = get_descendants_status(engine._connect, root)

        desc_ids = {d["agent_id"] for d in descendants}
        assert child in desc_ids
        assert grandchild in desc_ids

    def test_descendant_registry_depth_ordering(self, engine):
        """Descendants are ordered by depth (shallowest first)."""
        root = engine.generate_agent_id()
        engine.register_agent(root)
        child = engine.generate_agent_id(root)
        engine.register_agent(child, parent_id=root)
        grandchild = engine.generate_agent_id(child)
        engine.register_agent(grandchild, parent_id=child)

        from coordinationhub.agent_registry import get_descendants_status
        descendants = get_descendants_status(engine._connect, root)

        assert descendants[0]["depth"] == 1
        assert descendants[0]["agent_id"] == child
        assert descendants[1]["depth"] == 2
        assert descendants[1]["agent_id"] == grandchild

    def test_descendant_registry_includes_stopped(self, engine):
        """Stopped agents appear in descendants_status so callers can detect deaths."""
        root = engine.generate_agent_id()
        engine.register_agent(root)
        child = engine.generate_agent_id(root)
        engine.register_agent(child, parent_id=root)

        from coordinationhub.agent_registry import get_descendants_status
        descendants = get_descendants_status(engine._connect, root)
        assert all(d["status"] == "active" for d in descendants)

        engine.deregister_agent(child)

        descendants = get_descendants_status(engine._connect, root)
        child_desc = next(d for d in descendants if d["agent_id"] == child)
        assert child_desc["status"] == "stopped"

    def test_register_agent_context_bundle_includes_descendants(self, engine):
        """register_agent response bundle contains descendants_status."""
        root = engine.generate_agent_id()
        engine.register_agent(root)
        child = engine.generate_agent_id(root)
        engine.register_agent(child, parent_id=root)

        # root re-registers (heartbeat-style) — descendants should be in response
        result = engine.register_agent(root)
        assert "descendants_status" in result
        desc_ids = {d["agent_id"] for d in result["descendants_status"]}
        assert child in desc_ids

    def test_register_agent_no_descendants_when_none_exist(self, engine):
        """A root agent with no children gets an empty descendants_status list."""
        root = engine.generate_agent_id()
        engine.register_agent(root)

        result = engine.register_agent(root)
        assert "descendants_status" in result
        assert result["descendants_status"] == []
