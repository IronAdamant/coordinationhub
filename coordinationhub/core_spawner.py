"""SpawnerMixin — HA coordinator sub-agent spawn management.

Expects the host class to provide:
    self._connect() — callable returning a sqlite3 connection

Delegates to: spawner (spawner.py) for spawn primitives.
"""

from __future__ import annotations

import time
from typing import Any

from . import spawner as _spawner


class SpawnerMixin:
    """Sub-agent spawn management for HA coordinator."""

    DEFAULT_SPAWN_TIMEOUT = 300.0  # 5 minutes

    # ------------------------------------------------------------------ #
    # Spawn Management
    # ------------------------------------------------------------------ #

    def spawn_subagent(
        self,
        parent_agent_id: str,
        subagent_type: str,
        description: str | None = None,
        prompt: str | None = None,
        source: str = "external",
    ) -> dict[str, Any]:
        """Register intent to spawn a sub-agent and return its spawn ID.

        Adds an entry to the spawn queue (pending_tasks table). The parent
        agent calls this before the external system (Kimi CLI,
        etc.) spawns the sub-agent. This creates a pending spawn record that
        the spawning system will consume when the agent is actually spawned,
        correlating via ``parent_agent_id``.

        Returns the spawn ID and pending spawn record.
        """
        # T1.9: generate+stash in one BEGIN IMMEDIATE so two concurrent
        # spawns from the same (parent, subagent_type) can't produce the
        # same seq. spawn_id is returned in the result dict.
        result = _spawner.stash_pending_spawn(
            connect=self._connect,
            parent_agent_id=parent_agent_id,
            subagent_type=subagent_type,
            description=description,
            prompt=prompt,
            source=source,
        )
        return result

    def get_pending_spawns(
        self,
        parent_agent_id: str,
        include_consumed: bool = False,
    ) -> list[dict[str, Any]]:
        """Return pending (or all) spawn records for this parent agent."""
        return _spawner.get_pending_spawns(
            connect=self._connect,
            parent_agent_id=parent_agent_id,
            include_consumed=include_consumed,
        )

    def report_subagent_spawned(
        self,
        parent_agent_id: str,
        subagent_type: str | None,
        child_agent_id: str,
        source: str = "external",
        caller_agent_id: str | None = None,
    ) -> dict[str, Any]:
        """Report that a sub-agent has been spawned by an external system.

        Any IDE/CLI (Kimi CLI, Cursor, etc.) calls this after
        spawning a sub-agent via its native mechanism. This consumes the
        pending spawn record and links it to the actual child agent ID.

        T2.4: ``caller_agent_id`` (optional) — when supplied, must
        equal ``parent_agent_id``. Prevents a sibling agent from
        claiming another parent's child, which could hijack a
        ``spawner.registered`` event and lure ``await_subagent_registration``
        on the rightful parent. Omitted = pre-T2.4 permissive behaviour.
        """
        if caller_agent_id is not None and caller_agent_id != parent_agent_id:
            return {
                "reported": False,
                "error": "caller_agent_id does not match parent_agent_id",
                "reason": "caller_mismatch",
            }
        result = _spawner.report_subagent_spawned(
            self._connect, parent_agent_id, subagent_type, child_agent_id, source,
        )
        if result.get("spawn_id"):
            self._publish_event(
                "spawner.registered",
                {
                    "parent_agent_id": parent_agent_id,
                    "child_agent_id": child_agent_id,
                    "subagent_type": subagent_type,
                    "spawn_id": result["spawn_id"],
                },
            )
        return result

    def await_subagent_registration(
        self,
        parent_agent_id: str,
        subagent_type: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Wait until a pending spawn is consumed (sub-agent registered) or timeout.

        Uses the event bus for low-latency notification.
        Returns the consumed spawn record on success.
        Returns ``{"timed_out": True, ...}`` if the sub-agent did not register
        within the timeout.
        """
        timeout = timeout if timeout is not None else self.DEFAULT_SPAWN_TIMEOUT
        start = time.time()

        # Fast-path: check if already registered
        spawns = _spawner.get_pending_spawns(
            connect=self._connect,
            parent_agent_id=parent_agent_id,
            include_consumed=True,
        )
        for spawn in spawns:
            if subagent_type is None or spawn.get("subagent_type") == subagent_type:
                if spawn["status"] == "registered":
                    return {"registered": True, "spawn": spawn}

        event = self._hybrid_wait(
            ["spawner.registered"],
            filter_fn=lambda e: (
                e.get("parent_agent_id") == parent_agent_id
                and (subagent_type is None or e.get("subagent_type") == subagent_type)
            ),
            timeout=timeout,
        )
        if event is None:
            return {
                "timed_out": True,
                "timeout": timeout,
                "parent_agent_id": parent_agent_id,
                "subagent_type": subagent_type,
            }

        # Re-query to get the full spawn record
        spawns = _spawner.get_pending_spawns(
            connect=self._connect,
            parent_agent_id=parent_agent_id,
            include_consumed=True,
        )
        for spawn in spawns:
            if subagent_type is None or spawn.get("subagent_type") == subagent_type:
                if spawn["status"] == "registered":
                    return {"registered": True, "spawn": spawn}

        return {
            "timed_out": True,
            "timeout": timeout,
            "parent_agent_id": parent_agent_id,
            "subagent_type": subagent_type,
        }

    # ------------------------------------------------------------------ #
    # Health Polling + Deregistration Requests
    # ------------------------------------------------------------------ #

    def cancel_spawn(
        self, spawn_id: str, caller_agent_id: str | None = None,
    ) -> dict[str, Any]:
        """Cancel a pending spawn.

        T3.19: routes through the engine instead of reaching into
        ``engine._connect`` + ``_spawner.cancel_spawn`` directly from
        the CLI.

        T2.4: ``caller_agent_id`` (optional) — when supplied, the
        primitive verifies it matches the pending spawn's parent
        ``scope_id``. Prevents cross-parent cancellations. Omitted =
        pre-T2.4 permissive behaviour.
        """
        return _spawner.cancel_spawn(
            self._connect, spawn_id, caller_agent_id=caller_agent_id,
        )

    def request_subagent_deregistration(
        self,
        parent_agent_id: str,
        child_agent_id: str,
    ) -> dict[str, Any]:
        """Request graceful deregistration of a child agent.

        Sets ``stop_requested_at`` on the child agent. The child is expected
        to poll ``is_stop_requested`` and call ``deregister_agent`` if it sees
        the flag set. After a timeout, the caller should escalate to
        ``deregister_agent`` directly.

        Returns ``requested`` if the stop flag was set.
        Returns ``not_found`` if the child agent does not exist or is not active.
        """
        return _spawner.request_deregistration(
            connect=self._connect,
            child_agent_id=child_agent_id,
            requested_by=parent_agent_id,
        )

    def is_subagent_stop_requested(
        self,
        agent_id: str,
    ) -> dict[str, Any]:
        """Check if a stop has been requested for this agent.

        The agent should call this periodically and deregister if the stop
        flag is set.
        """
        return _spawner.is_stop_requested(
            connect=self._connect,
            agent_id=agent_id,
        )

    def await_subagent_stopped(
        self,
        child_agent_id: str,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Wait until a child agent is stopped or the timeout is reached.

        Uses the event bus for low-latency notification.
        Returns ``stopped: True`` if the child called ``deregister_agent`` within
        the timeout. Returns ``timed_out: True`` with ``escalate: True`` if the
        child did not stop in time — the caller should then call
        ``deregister_agent`` directly to force cleanup.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM agents WHERE agent_id = ?", (child_agent_id,)
            ).fetchone()
            if row is None or row["status"] == "stopped":
                return {"stopped": True, "child_agent_id": child_agent_id}

        event = self._hybrid_wait(
            ["agent.deregistered"],
            filter_fn=lambda e: e.get("agent_id") == child_agent_id,
            timeout=timeout,
        )
        if event:
            return {"stopped": True, "child_agent_id": child_agent_id}
        return {
            "timed_out": True,
            "child_agent_id": child_agent_id,
            "timeout": timeout,
            "escalate": True,
        }
