"""Multi-agent scenario integration tests.

End-to-end tests that simulate realistic multi-agent workflows:
parent spawns children, they coordinate via locks, one dies,
orphaning cascades, survivors continue.
"""

from __future__ import annotations

import time

import pytest


class TestFullLifecycleScenario:
    """Parent → spawn children → lock → work → die → orphan → continue."""

    def test_spawn_lock_die_orphan_continue(self, engine):
        # 1. Parent registers
        parent = engine.generate_agent_id()
        reg = engine.register_agent(parent)
        assert reg["agent_id"] == parent

        # 2. Parent spawns 3 children
        children = []
        for _ in range(3):
            cid = engine.generate_agent_id(parent)
            engine.register_agent(cid, parent_id=parent)
            children.append(cid)

        # Verify lineage
        lineage = engine.get_lineage(parent)
        desc_ids = {d["agent_id"] for d in lineage["descendants"]}
        assert desc_ids == set(children)

        # 3. Children acquire locks on distinct files
        for i, cid in enumerate(children):
            result = engine.acquire_lock(f"/src/module_{i}.py", cid)
            assert result["acquired"]

        # 4. Child 0 tries to lock child 1's file — should fail
        contested = engine.acquire_lock("/src/module_1.py", children[0])
        assert contested["acquired"] is False
        assert contested["locked_by"] == children[1]

        # 5. Force-steal creates a conflict log entry (normal denial does not)
        engine.acquire_lock("/src/module_1.py", children[0], force=True)
        conflicts = engine.get_conflicts()
        conflict_list = conflicts["conflicts"]
        assert len(conflict_list) >= 1
        latest = conflict_list[0]
        assert latest["agent_b"] == children[0]

        # Release back so child 1 still has module_1 for the deregister test
        engine.release_lock("/src/module_1.py", children[0])
        engine.acquire_lock("/src/module_1.py", children[1])

        # 6. Child 1 dies
        dereg = engine.deregister_agent(children[1])
        assert dereg["locks_released"] == 1

        # 7. The file is now unlockable by child 0
        result = engine.acquire_lock("/src/module_1.py", children[0])
        assert result["acquired"]

        # 8. All notifications flow correctly
        engine.notify_change("/src/module_0.py", "modified", children[0])
        engine.notify_change("/src/module_2.py", "modified", children[2])
        notifs = engine.get_notifications(exclude_agent=parent)
        assert len(notifs["notifications"]) == 2

        # 9. Status is coherent
        status = engine.status()
        assert status["active_agents"] == 3  # parent + child0 + child2

    def test_grandchild_orphaning(self, engine):
        """When a middle agent dies, its children re-parent to grandparent."""
        root = engine.generate_agent_id()
        engine.register_agent(root)

        mid = engine.generate_agent_id(root)
        engine.register_agent(mid, parent_id=root)

        leaf = engine.generate_agent_id(mid)
        engine.register_agent(leaf, parent_id=mid)

        # Verify three-level lineage
        lineage = engine.get_lineage(root)
        desc_ids = {d["agent_id"] for d in lineage["descendants"]}
        assert mid in desc_ids
        assert leaf in desc_ids

        # Middle agent dies — leaf should re-parent to root
        engine.deregister_agent(mid)

        # Leaf is still active
        agents = engine.list_agents()
        active_ids = {a["agent_id"] for a in agents["agents"]}
        assert leaf in active_ids
        assert mid not in active_ids

        # Leaf's lineage now shows root as ancestor
        leaf_lineage = engine.get_lineage(leaf)
        ancestor_ids = {a["agent_id"] for a in leaf_lineage["ancestors"]}
        assert root in ancestor_ids

    def test_broadcast_then_lock_workflow(self, engine):
        """Agent broadcasts intent, then acquires lock — siblings see the broadcast."""
        parent = engine.generate_agent_id()
        engine.register_agent(parent)

        writer = engine.generate_agent_id(parent)
        engine.register_agent(writer, parent_id=parent)
        reviewer = engine.generate_agent_id(parent)
        engine.register_agent(reviewer, parent_id=parent)

        # Writer broadcasts intent to work on a file
        broadcast_result = engine.broadcast(writer, document_path="/shared/config.yaml")
        assert broadcast_result["acknowledged_by"] is not None

        # Writer acquires the lock
        lock = engine.acquire_lock("/shared/config.yaml", writer)
        assert lock["acquired"]

        # Writer makes changes and notifies
        engine.notify_change("/shared/config.yaml", "modified", writer)

        # Reviewer polls for changes (excluding own)
        notifs = engine.get_notifications(exclude_agent=reviewer)
        paths = [n["document_path"] for n in notifs["notifications"]]
        assert "/shared/config.yaml" in paths

        # Writer releases lock
        release = engine.release_lock("/shared/config.yaml", writer)
        assert release["released"]

    def test_stale_agent_reaping_workflow(self, engine):
        """Agents go stale, get reaped, locks are released."""
        parent = engine.generate_agent_id()
        engine.register_agent(parent)
        worker = engine.generate_agent_id(parent)
        engine.register_agent(worker, parent_id=parent)

        # Worker acquires a lock with short TTL
        engine.acquire_lock("/important.py", worker, ttl=0.01)
        time.sleep(0.02)

        # Reap expired locks
        reaped = engine.reap_expired_locks()
        assert reaped["reaped"] == 1

        # File is now available
        lock_status = engine.get_lock_status("/important.py")
        assert lock_status["locked"] is False

    def test_wait_for_locks_workflow(self, engine):
        """One agent waits for another to release a lock."""
        parent = engine.generate_agent_id()
        engine.register_agent(parent)
        holder = engine.generate_agent_id(parent)
        engine.register_agent(holder, parent_id=parent)
        waiter = engine.generate_agent_id(parent)
        engine.register_agent(waiter, parent_id=parent)

        # Holder takes a very short-lived lock
        engine.acquire_lock("/shared.py", holder, ttl=0.05)

        # Waiter waits — the lock should expire within timeout
        time.sleep(0.06)
        engine.reap_expired_locks()
        result = engine.wait_for_locks(["/shared.py"], waiter, timeout_s=1.0)
        assert "/shared.py" in result.get("released", [])

    def test_full_notification_lifecycle(self, engine):
        """Create, query, and prune notifications across agents."""
        parent = engine.generate_agent_id()
        engine.register_agent(parent)

        agents = []
        for _ in range(3):
            aid = engine.generate_agent_id(parent)
            engine.register_agent(aid, parent_id=parent)
            agents.append(aid)

        # Each agent modifies files
        for i, aid in enumerate(agents):
            for j in range(3):
                engine.notify_change(f"/src/agent{i}_file{j}.py", "modified", aid)

        # Total: 9 notifications
        all_notifs = engine.get_notifications(limit=50)
        assert len(all_notifs["notifications"]) == 9

        # Exclude agent 0's notifications
        filtered = engine.get_notifications(exclude_agent=agents[0], limit=50)
        assert len(filtered["notifications"]) == 6

        # Prune to keep only 5
        pruned = engine.prune_notifications(max_entries=5)
        assert pruned["pruned"] == 4

        remaining = engine.get_notifications(limit=50)
        assert len(remaining["notifications"]) == 5
