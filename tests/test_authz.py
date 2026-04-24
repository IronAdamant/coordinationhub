"""Regression tests for T2.4 caller-assertion gaps.

The audit left three impersonation / hijack vectors open:

* ``send_message`` — any caller could set ``from_agent_id`` to any
  string, forging messages from other agents.
* ``report_subagent_spawned`` — any caller could claim to be the
  parent of another spawn, hijacking the ``spawner.registered`` event
  and waking the rightful parent's ``await_subagent_registration``
  with a child the attacker controls.
* ``cancel_spawn`` — any caller could cancel another parent's pending
  spawn, silently aborting sub-agent creation on unrelated trees.

Each tool now accepts an optional ``caller_agent_id`` that must match
the row's owning agent. Omitting it preserves the pre-T2.4 permissive
behaviour for internal callers (schedulers, CLI admin) that already
trust the agent id.
"""

from __future__ import annotations

import pytest


class TestSendMessageCallerCheck:
    def test_matching_caller_allowed(self, engine, two_agents):
        result = engine.send_message(
            from_agent_id=two_agents["child"],
            to_agent_id=two_agents["other"],
            message_type="query",
            caller_agent_id=two_agents["child"],
        )
        assert result.get("sent") is True or result.get("message_id") is not None

    def test_mismatched_caller_rejected(self, engine, two_agents):
        result = engine.send_message(
            from_agent_id=two_agents["child"],
            to_agent_id=two_agents["other"],
            message_type="query",
            caller_agent_id=two_agents["other"],  # wrong caller
        )
        assert result.get("sent") is False
        assert result.get("reason") == "caller_mismatch"

    def test_omitted_caller_preserves_legacy_trust(self, engine, two_agents):
        """Internal callers (schedulers, CLI) omit caller_agent_id."""
        result = engine.send_message(
            from_agent_id=two_agents["child"],
            to_agent_id=two_agents["other"],
            message_type="query",
        )
        assert result.get("sent") is True or result.get("message_id") is not None


class TestManageMessagesCallerCheck:
    def test_get_rejects_inbox_siphon(self, engine, two_agents):
        # Parent tries to read child's inbox — must reject.
        result = engine.manage_messages(
            action="get",
            agent_id=two_agents["child"],
            caller_agent_id=two_agents["other"],
        )
        assert result.get("reason") == "caller_mismatch"

    def test_get_matching_caller_succeeds(self, engine, two_agents):
        result = engine.manage_messages(
            action="get",
            agent_id=two_agents["child"],
            caller_agent_id=two_agents["child"],
        )
        assert "messages" in result
        assert result.get("reason") != "caller_mismatch"

    def test_send_caller_must_equal_from(self, engine, two_agents):
        result = engine.manage_messages(
            action="send",
            agent_id=two_agents["child"],
            from_agent_id=two_agents["child"],
            to_agent_id=two_agents["other"],
            message_type="notice",
            caller_agent_id=two_agents["other"],
        )
        assert result.get("reason") == "caller_mismatch"

    def test_mark_read_rejects_other_agent(self, engine, two_agents):
        result = engine.manage_messages(
            action="mark_read",
            agent_id=two_agents["child"],
            caller_agent_id=two_agents["other"],
        )
        assert result.get("reason") == "caller_mismatch"


class TestReportSubagentSpawnedCallerCheck:
    def test_matching_parent_allowed(self, engine, two_agents):
        parent = two_agents["parent"]
        # Register + link a spawn.
        child_id = engine.generate_agent_id(parent)
        engine.register_agent(child_id, parent_id=parent)
        result = engine.report_subagent_spawned(
            parent_agent_id=parent,
            subagent_type="worker",
            child_agent_id=child_id,
            caller_agent_id=parent,
        )
        assert result.get("reported") is not False

    def test_mismatched_caller_cannot_claim_sibling_child(self, engine, two_agents):
        parent = two_agents["parent"]
        sibling = two_agents["other"]
        child_id = engine.generate_agent_id(parent)
        engine.register_agent(child_id, parent_id=parent)
        result = engine.report_subagent_spawned(
            parent_agent_id=parent,
            subagent_type="worker",
            child_agent_id=child_id,
            caller_agent_id=sibling,  # wrong caller
        )
        assert result.get("reported") is False
        assert result.get("reason") == "caller_mismatch"

    def test_omitted_caller_preserves_legacy_trust(self, engine, two_agents):
        parent = two_agents["parent"]
        child_id = engine.generate_agent_id(parent)
        engine.register_agent(child_id, parent_id=parent)
        result = engine.report_subagent_spawned(
            parent_agent_id=parent,
            subagent_type="worker",
            child_agent_id=child_id,
        )
        assert result.get("reported") is not False


class TestCancelSpawnCallerCheck:
    def test_matching_parent_allowed(self, engine, two_agents):
        parent = two_agents["parent"]
        spawn = engine.spawn_subagent(
            parent_agent_id=parent,
            subagent_type="worker",
            description="ephemeral",
        )
        spawn_id = spawn.get("spawn_id")
        assert spawn_id
        result = engine.cancel_spawn(spawn_id, caller_agent_id=parent)
        assert result.get("cancelled") is True

    def test_sibling_cannot_cancel(self, engine, two_agents):
        parent = two_agents["parent"]
        sibling = two_agents["other"]
        spawn = engine.spawn_subagent(
            parent_agent_id=parent,
            subagent_type="worker",
            description="ephemeral",
        )
        spawn_id = spawn.get("spawn_id")
        assert spawn_id
        result = engine.cancel_spawn(spawn_id, caller_agent_id=sibling)
        assert result.get("cancelled") is False
        assert result.get("reason") == "caller_mismatch"
        # Underlying row must still be pending — not cancelled.
        with engine._connect() as conn:
            row = conn.execute(
                "SELECT status FROM pending_tasks WHERE task_id = ?",
                (spawn_id,),
            ).fetchone()
        assert row["status"] == "pending"

    def test_omitted_caller_preserves_admin_path(self, engine, two_agents):
        parent = two_agents["parent"]
        spawn = engine.spawn_subagent(
            parent_agent_id=parent,
            subagent_type="worker",
            description="ephemeral",
        )
        result = engine.cancel_spawn(spawn["spawn_id"])
        assert result.get("cancelled") is True


class TestDispatchCallerPlumbing:
    """T2.4: dispatch table whitelists caller_agent_id for the three
    tools so MCP callers can send the assertion over the wire."""

    def test_send_message_accepts_caller_agent_id_over_dispatch(
        self, engine, two_agents,
    ):
        from coordinationhub.dispatch import dispatch_tool
        result = dispatch_tool(engine, "send_message", {
            "from_agent_id": two_agents["child"],
            "to_agent_id": two_agents["other"],
            "message_type": "query",
            "caller_agent_id": two_agents["other"],
        })
        assert result.get("reason") == "caller_mismatch"

    def test_manage_messages_caller_agent_id_over_dispatch(
        self, engine, two_agents,
    ):
        from coordinationhub.dispatch import dispatch_tool
        result = dispatch_tool(engine, "manage_messages", {
            "action": "get",
            "agent_id": two_agents["child"],
            "caller_agent_id": two_agents["other"],
        })
        assert result.get("reason") == "caller_mismatch"

    def test_report_subagent_spawned_caller_over_dispatch(
        self, engine, two_agents,
    ):
        from coordinationhub.dispatch import dispatch_tool
        parent = two_agents["parent"]
        child_id = engine.generate_agent_id(parent)
        engine.register_agent(child_id, parent_id=parent)
        result = dispatch_tool(engine, "report_subagent_spawned", {
            "parent_agent_id": parent,
            "subagent_type": "worker",
            "child_agent_id": child_id,
            "caller_agent_id": two_agents["other"],
        })
        assert result.get("reason") == "caller_mismatch"
