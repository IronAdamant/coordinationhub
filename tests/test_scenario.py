"""Multi-agent scenario integration tests.

End-to-end tests that simulate realistic multi-agent workflows:
parent spawns children, they coordinate via locks, one dies,
orphaning cascades, survivors continue.

``TestHookLevelMultiAgentScenario`` exercises the real Claude Code hook
handlers (not the engine API directly) in a concurrent setting,
reproducing the kind of workflow Review Thirteen surfaced — the
validation gap between unit tests and real multi-agent load.
"""

from __future__ import annotations

import threading
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


class TestHookLevelMultiAgentScenario:
    """End-to-end tests using the real Claude Code hook handlers.

    These tests drive the hooks the way Claude Code would — calling
    ``handle_session_start``, ``handle_subagent_start``, ``handle_pre_write``,
    ``handle_post_write``, ``handle_subagent_stop`` with realistic event
    dicts. They exercise the complete v0.4.0 feature set together: smart
    reap with grace period, TTL refresh on PostToolUse, first-write-wins
    file ownership, SubagentStop status transitions, and cross-agent
    contention resolution.

    This closes the gap between unit tests (which test individual
    primitives) and RecipeLab production reviews (which surface real
    multi-agent bugs). The scenarios are modeled on Review Thirteen.
    """

    @staticmethod
    def _git_dir(tmp_path):
        """Create a project-root-looking directory."""
        (tmp_path / ".git").mkdir(exist_ok=True)
        return str(tmp_path)

    @staticmethod
    def _session_event(cwd, session_id):
        return {"hook_event_name": "SessionStart",
                "session_id": session_id, "cwd": cwd}

    @staticmethod
    def _subagent_start_event(cwd, session_id, raw_id, desc="worker"):
        return {
            "hook_event_name": "SubagentStart",
            "session_id": session_id, "cwd": cwd,
            "subagent_id": raw_id,
            "tool_input": {"subagent_type": "agent", "description": desc},
        }

    @staticmethod
    def _subagent_stop_event(cwd, session_id, raw_id):
        return {
            "hook_event_name": "SubagentStop",
            "session_id": session_id, "cwd": cwd,
            "subagent_id": raw_id,
        }

    @staticmethod
    def _write_event(cwd, session_id, raw_id, file_path, pre=True):
        return {
            "hook_event_name": "PreToolUse" if pre else "PostToolUse",
            "tool_name": "Write", "session_id": session_id, "cwd": cwd,
            "subagent_id": raw_id,
            "tool_input": {"file_path": file_path},
        }

    def test_wave_of_subagents_full_lifecycle(self, tmp_path):
        """11 sub-agents registered, each writes a unique file, all deregister cleanly.

        Mirrors Review Thirteen's batch 2: 4 enrichers + 5 feature builders
        + 2 misc, each writing to a separate file in the same directory.
        Verifies that all register, write successfully, get attributed
        notifications, claim file ownership, and transition to 'stopped'.
        """
        from coordinationhub.hooks.claude_code import (
            handle_session_start, handle_subagent_start, handle_subagent_stop,
            handle_pre_write, handle_post_write, _get_engine,
        )

        cwd = self._git_dir(tmp_path)
        session_id = "sess-wave-1234abcd"
        handle_session_start(self._session_event(cwd, session_id))

        n = 11
        raw_ids = [f"{'a' if i < 10 else 'b'}{i:016x}" for i in range(n)]

        # Register all sub-agents
        for i, raw_id in enumerate(raw_ids):
            handle_subagent_start(self._subagent_start_event(
                cwd, session_id, raw_id, desc=f"builder_{i}"))

        # Each sub-agent writes its own file
        for i, raw_id in enumerate(raw_ids):
            file_path = str(tmp_path / f"src/feature_{i}.py")
            handle_pre_write(self._write_event(cwd, session_id, raw_id, file_path))
            handle_post_write(self._write_event(
                cwd, session_id, raw_id, file_path, pre=False))

        # Stop all sub-agents
        for raw_id in raw_ids:
            handle_subagent_stop(self._subagent_stop_event(cwd, session_id, raw_id))

        # Verify end state
        engine = _get_engine(cwd)
        try:
            # 1. All sub-agents registered with parent hierarchy
            agents = engine.list_agents(active_only=False)
            hub_ids = [a for a in agents["agents"]
                       if a.get("claude_agent_id") in raw_ids]
            assert len(hub_ids) == n, f"Expected {n} sub-agents, got {len(hub_ids)}"

            # 2. All sub-agents transitioned to 'stopped' via SubagentStop
            stopped = [a for a in hub_ids if a["status"] == "stopped"]
            assert len(stopped) == n, (
                f"Expected all {n} stopped, got {len(stopped)}: "
                f"{[(a['agent_id'], a['status']) for a in hub_ids]}"
            )

            # 3. Each sub-agent's write was attributed via change notification
            notifs = engine.get_notifications(limit=50)
            write_notifs = [n for n in notifs["notifications"]
                            if n["change_type"] == "modified"]
            assert len(write_notifs) == n, (
                f"Expected {n} write notifications, got {len(write_notifs)}"
            )

            # 4. File ownership was claimed for every file
            with engine._connect() as conn:
                rows = conn.execute(
                    "SELECT assigned_agent_id FROM file_ownership "
                    "WHERE document_path LIKE '%feature_%'"
                ).fetchall()
            assert len(rows) == n, f"Expected {n} ownership rows, got {len(rows)}"
            # Each owner should be a hub.cc.* ID (not a raw hex)
            for row in rows:
                assert row["assigned_agent_id"].startswith("hub.cc."), (
                    f"Ownership should use hub.cc.* ID, got {row['assigned_agent_id']}"
                )
        finally:
            engine.close()

    def test_concurrent_contention_on_same_file(self, tmp_path):
        """Two sub-agents race to lock the same file — exactly one wins.

        This is the test Review Thirteen flagged as missing: actual
        concurrent contention on a shared file, verified via the hook
        handlers (not the engine API directly).
        """
        from coordinationhub.hooks.claude_code import (
            handle_session_start, handle_subagent_start, handle_pre_write,
            _get_engine,
        )

        cwd = self._git_dir(tmp_path)
        session_id = "sess-race-5678efgh"
        handle_session_start(self._session_event(cwd, session_id))

        raw_a = "a" * 17
        raw_b = "b" * 17
        handle_subagent_start(self._subagent_start_event(cwd, session_id, raw_a, "agent_A"))
        handle_subagent_start(self._subagent_start_event(cwd, session_id, raw_b, "agent_B"))

        shared_file = str(tmp_path / "shared_state.py")
        results = {}
        errors = []

        def attempt_lock(raw_id, key):
            try:
                result = handle_pre_write(self._write_event(
                    cwd, session_id, raw_id, shared_file))
                results[key] = result
            except Exception as exc:
                errors.append(exc)

        t_a = threading.Thread(target=attempt_lock, args=(raw_a, "a"))
        t_b = threading.Thread(target=attempt_lock, args=(raw_b, "b"))
        t_a.start()
        t_b.start()
        t_a.join(timeout=5)
        t_b.join(timeout=5)

        assert not errors, f"Errors during concurrent lock attempt: {errors}"

        # Exactly one got 'allow', the other got 'deny'
        decisions = [results[k]["hookSpecificOutput"]["permissionDecision"]
                     for k in ("a", "b")]
        assert sorted(decisions) == ["allow", "deny"], (
            f"Expected one allow + one deny, got {decisions}"
        )

    def test_smart_reap_survives_long_model_call(self, tmp_path):
        """Simulate a slow model call: lock acquired, then expires, but agent is
        still active — smart reap should refresh it, not delete it.

        This is the Review Thirteen scenario: a lock with short TTL
        expires between PreToolUse and PostToolUse, but the owning agent
        has a recent heartbeat so the lock should survive.
        """
        from coordinationhub.hooks.claude_code import (
            handle_session_start, handle_pre_write, _get_engine,
        )

        cwd = self._git_dir(tmp_path)
        session_id = "sess-slow-9abc0def"
        handle_session_start(self._session_event(cwd, session_id))

        # Acquire with normal hook TTL (300s), then artificially expire
        file_path = str(tmp_path / "slow_file.py")
        handle_pre_write({
            "hook_event_name": "PreToolUse", "tool_name": "Write",
            "session_id": session_id, "cwd": cwd,
            "tool_input": {"file_path": file_path},
        })

        engine = _get_engine(cwd)
        try:
            # Force the lock to be 'expired' by rewriting locked_at into the past
            with engine._connect() as conn:
                conn.execute(
                    "UPDATE document_locks SET locked_at = ?, lock_ttl = ?",
                    (time.time() - 1000, 1.0),
                )
            # Refresh the owning agent's heartbeat
            from coordinationhub.hooks.claude_code import _session_agent_id
            root_id = _session_agent_id(session_id)
            engine.heartbeat(root_id)

            # Smart reap (as the hook calls it) should spare and refresh
            reaped = engine.reap_expired_locks(agent_grace_seconds=120.0)
            assert reaped["reaped"] == 0, "Active-agent lock should be spared"

            # Lock is now effectively live again (locked_at refreshed to now)
            status = engine.get_lock_status(file_path)
            assert status["locked"] is True
        finally:
            engine.close()

    def test_crashed_agent_locks_reaped(self, tmp_path):
        """Crashed agents (stale heartbeats) get their expired locks reaped,
        even with agent_grace_seconds set."""
        from coordinationhub.hooks.claude_code import (
            handle_session_start, handle_pre_write, _get_engine,
            _session_agent_id,
        )

        cwd = self._git_dir(tmp_path)
        session_id = "sess-crash-1122aabb"
        handle_session_start(self._session_event(cwd, session_id))
        handle_pre_write({
            "hook_event_name": "PreToolUse", "tool_name": "Write",
            "session_id": session_id, "cwd": cwd,
            "tool_input": {"file_path": str(tmp_path / "crashed.py")},
        })

        engine = _get_engine(cwd)
        try:
            # Simulate crash: lock expired AND agent heartbeat stale
            root_id = _session_agent_id(session_id)
            with engine._connect() as conn:
                conn.execute(
                    "UPDATE document_locks SET locked_at = ?, lock_ttl = ?",
                    (time.time() - 1000, 1.0),
                )
                conn.execute(
                    "UPDATE agents SET last_heartbeat = 0 WHERE agent_id = ?",
                    (root_id,),
                )

            reaped = engine.reap_expired_locks(agent_grace_seconds=120.0)
            assert reaped["reaped"] == 1, (
                "Crashed-agent locks should be reaped even with grace period"
            )
        finally:
            engine.close()

    def test_coordination_graph_and_assessment_pipeline(self, tmp_path):
        """End-to-end: load a coordination graph during a hook-driven
        multi-agent session, then run assessment on a trace built from
        that session's events.

        Closes Review Thirteen gaps 5 (assessment scoring) and 6
        (coordination graphs): both features existed with unit coverage
        but had never been exercised together through the real hook
        entry points. This test proves the full pipeline — spec load,
        role assignment, hook-driven registrations/writes, trace
        construction, and assessment scoring — works in one flow.
        """
        import json as _json

        from coordinationhub.hooks.claude_code import (
            handle_session_start, handle_subagent_start, handle_post_write,
            handle_subagent_stop, _get_engine, _session_agent_id,
        )

        cwd = self._git_dir(tmp_path)
        session_id = "sess-pipeline-cafe0001"

        # 1. Drop a coordination spec inside the project root so that
        #    load_coordination_spec() can auto-discover it.
        spec_path = tmp_path / "coordination_spec.json"
        spec_path.write_text(_json.dumps({
            "agents": [
                {"id": "planner", "role": "plan",
                 "responsibilities": ["decompose", "plan"]},
                {"id": "builder", "role": "implement",
                 "responsibilities": ["write code", "modify files", "build"]},
            ],
            "handoffs": [
                {"from": "planner", "to": "builder",
                 "condition": "plan approved"},
            ],
            "assessment": {"metrics": [
                "role_stability", "handoff_latency",
                "outcome_verifiability", "protocol_adherence",
                "spawn_propagation",
            ]},
        }))

        # 2. SessionStart registers the root agent; then load the graph.
        handle_session_start(self._session_event(cwd, session_id))
        engine = _get_engine(cwd)
        try:
            load_result = engine.load_coordination_spec(str(spec_path))
            assert load_result["loaded"] is True
            assert "planner" in load_result["agents"]
            assert "builder" in load_result["agents"]

            # 3. Spawn two sub-agents via SubagentStart and have each
            #    write a file through handle_post_write (drives the hook
            #    path end-to-end).
            raw_a = "a" * 17
            raw_b = "b" * 17
            handle_subagent_start(self._subagent_start_event(
                cwd, session_id, raw_a, desc="planner agent"))
            handle_subagent_start(self._subagent_start_event(
                cwd, session_id, raw_b, desc="builder agent"))

            file_a = str(tmp_path / "plan.md")
            file_b = str(tmp_path / "impl.py")
            handle_post_write(self._write_event(
                cwd, session_id, raw_a, file_a, pre=False))
            handle_post_write(self._write_event(
                cwd, session_id, raw_b, file_b, pre=False))

            # 4. Resolve the hub.cc.* IDs while the agents are still
            #    active (find_agent_by_claude_id filters by status).
            hub_a = engine.find_agent_by_claude_id(raw_a)
            hub_b = engine.find_agent_by_claude_id(raw_b)
            assert hub_a is not None
            assert hub_b is not None

            # Deregister both via SubagentStop — must transition to stopped.
            handle_subagent_stop(self._subagent_stop_event(cwd, session_id, raw_a))
            handle_subagent_stop(self._subagent_stop_event(cwd, session_id, raw_b))

            # 5. Build a trace from hook-observed state. Each notify_change
            #    becomes a 'modified' event; each file write adds a matching
            #    'lock' to make outcome_verifiability non-vacuous. Events
            #    mirror what assessment_scorers expect.
            trace = {
                "trace_id": "pipeline-e2e",
                "events": [
                    {"type": "register", "agent_id": hub_a, "graph_id": "planner"},
                    {"type": "register", "agent_id": hub_b, "graph_id": "builder"},
                    {"type": "lock", "path": file_a, "agent_id": hub_a},
                    {"type": "modified", "path": file_a, "agent_id": hub_a},
                    {"type": "handoff", "from": "planner", "to": "builder",
                     "condition": "plan approved"},
                    {"type": "lock", "path": file_b, "agent_id": hub_b},
                    {"type": "modified", "path": file_b, "agent_id": hub_b},
                ],
            }
            suite_path = tmp_path / "pipeline_suite.json"
            suite_path.write_text(_json.dumps({
                "name": "pipeline_e2e",
                "traces": [trace],
            }))

            # 6. Run the assessment. This exercises the full runner:
            #    loading the suite, scoring against the loaded graph,
            #    and persisting results to SQLite.
            result = engine.run_assessment(str(suite_path), format="json")
            assert "error" not in result
            assert result["graph_loaded"] is True
            assert result["suite_name"] == "pipeline_e2e"
            # All five metrics should have been scored.
            metric_names = set(result["metrics"].keys())
            expected_metrics = {
                "role_stability", "handoff_latency", "outcome_verifiability",
                "protocol_adherence", "spawn_propagation",
            }
            assert expected_metrics.issubset(metric_names), (
                f"Missing metrics: {expected_metrics - metric_names}"
            )
            # Overall score is a fraction in [0, 1] and the trace should
            # produce at least some non-zero coverage given the events.
            assert 0.0 <= result["overall_score"] <= 1.0
            assert result["overall_score"] > 0.0, (
                f"Expected non-zero score, got {result['overall_score']}"
            )

            # 7. The scored result should have been persisted.
            with engine._connect() as conn:
                rows = conn.execute(
                    "SELECT suite_name, metric FROM assessment_results "
                    "WHERE suite_name = ?",
                    ("pipeline_e2e",),
                ).fetchall()
            assert len(rows) == len(expected_metrics)
        finally:
            engine.close()

    def test_assess_current_session_from_live_db(self, tmp_path):
        """End-to-end: load a coordination graph, run multi-agent hooks,
        then call engine.assess_current_session() with no hand-built suite.

        This is the capability v0.4.4's pipeline test still required a
        manually-constructed trace for. With build_trace_from_db + the
        assess_current_session engine method (added in v0.4.5), a user
        can score a live session in a single call — no JSON suite file,
        no manual event construction.
        """
        import json as _json

        from coordinationhub.hooks.claude_code import (
            handle_session_start, handle_subagent_start, handle_post_write,
            handle_subagent_stop, _get_engine,
        )

        cwd = self._git_dir(tmp_path)
        session_id = "sess-live-feed0002"

        # Write + load the spec
        spec_path = tmp_path / "coordination_spec.json"
        spec_path.write_text(_json.dumps({
            "agents": [
                {"id": "planner", "role": "plan",
                 "responsibilities": ["decompose", "plan"]},
                {"id": "builder", "role": "implement",
                 "responsibilities": ["write code", "modify files", "build"]},
            ],
            "handoffs": [
                {"from": "planner", "to": "builder",
                 "condition": "plan approved"},
            ],
            "assessment": {"metrics": [
                "role_stability", "handoff_latency",
                "outcome_verifiability", "protocol_adherence",
                "spawn_propagation",
            ]},
        }))

        handle_session_start(self._session_event(cwd, session_id))
        engine = _get_engine(cwd)
        try:
            engine.load_coordination_spec(str(spec_path))

            # Drive a multi-agent session
            raw_a = "c" * 17
            raw_b = "d" * 17
            handle_subagent_start(self._subagent_start_event(
                cwd, session_id, raw_a, desc="planner agent"))
            handle_subagent_start(self._subagent_start_event(
                cwd, session_id, raw_b, desc="builder agent"))

            handle_post_write(self._write_event(
                cwd, session_id, raw_a, str(tmp_path / "plan.md"), pre=False))
            handle_post_write(self._write_event(
                cwd, session_id, raw_b, str(tmp_path / "impl.py"), pre=False))
            handle_post_write(self._write_event(
                cwd, session_id, raw_b, str(tmp_path / "impl2.py"), pre=False))

            # Tag the live sub-agents with explicit graph roles so the
            # synthesized trace carries role information into scoring.
            hub_a = engine.find_agent_by_claude_id(raw_a)
            hub_b = engine.find_agent_by_claude_id(raw_b)
            engine.register_agent(hub_a, graph_agent_id="planner")
            engine.register_agent(hub_b, graph_agent_id="builder")

            handle_subagent_stop(self._subagent_stop_event(cwd, session_id, raw_a))
            handle_subagent_stop(self._subagent_stop_event(cwd, session_id, raw_b))

            # Score the live session — no suite file, no manual trace.
            result = engine.assess_current_session(format="json", scope="all")
            assert "error" not in result, f"Got error: {result.get('error')}"
            assert result["graph_loaded"] is True

            # All five metrics computed
            metric_names = set(result["metrics"].keys())
            assert metric_names == {
                "role_stability", "handoff_latency", "outcome_verifiability",
                "protocol_adherence", "spawn_propagation",
            }

            # outcome_verifiability should be meaningful because the
            # converter synthesized lock→modified→unlock triples from
            # the 3 write notifications.
            assert result["metrics"]["outcome_verifiability"] > 0.0

            # Persisted to assessment_results
            with engine._connect() as conn:
                rows = conn.execute(
                    "SELECT metric FROM assessment_results "
                    "WHERE suite_name = 'live_session'"
                ).fetchall()
            assert len(rows) == 5
        finally:
            engine.close()

    def test_assess_current_session_without_graph_returns_error(self, tmp_path):
        """Without a loaded coordination graph, assess_current_session returns
        a clear error rather than producing vacuous scores."""
        from coordinationhub.hooks.claude_code import (
            handle_session_start, _get_engine,
        )
        from coordinationhub import graphs as _graphs

        # Module-level graph state may be set by an earlier test in the suite.
        _graphs.clear_graph()

        cwd = self._git_dir(tmp_path)
        session_id = "sess-no-graph-00000001"
        handle_session_start(self._session_event(cwd, session_id))
        engine = _get_engine(cwd)
        try:
            result = engine.assess_current_session(format="json")
            assert "error" in result
            assert "graph" in result["error"].lower()
        finally:
            engine.close()
