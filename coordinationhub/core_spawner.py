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
    ) -> dict[str, Any]:
        """Register intent to spawn a sub-agent and return its spawn ID.

        The parent agent calls this before Claude Code spawns the sub-agent.
        This creates a pending spawn record that the hook will consume
        when ``SubagentStart`` fires, correlating via ``parent_agent_id``.

        Returns the spawn ID and pending spawn record.
        """
        spawn_id = _spawner.generate_spawn_id(
            self._connect, parent_agent_id, subagent_type,
        )
        result = _spawner.stash_pending_spawn(
            connect=self._connect,
            spawn_id=spawn_id,
            parent_agent_id=parent_agent_id,
            subagent_type=subagent_type,
            description=description,
            prompt=prompt,
        )
        result["spawn_id"] = spawn_id
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

    def await_subagent_registration(
        self,
        parent_agent_id: str,
        subagent_type: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Poll until a pending spawn is consumed (sub-agent registered) or timeout.

        The parent agent calls this after ``spawn_subagent`` to wait for the
        sub-agent to register itself via the Claude Code hook's
        ``SubagentStart`` handler.

        Returns the consumed spawn record on success.
        Returns ``{"timed_out": True, ...}`` if the sub-agent did not register
        within the timeout.
        """
        timeout = timeout if timeout is not None else self.DEFAULT_SPAWN_TIMEOUT
        deadline = time.time() + timeout
        poll_interval = 0.5  # 500ms between polls

        while time.time() < deadline:
            spawns = _spawner.get_pending_spawns(
                connect=self._connect,
                parent_agent_id=parent_agent_id,
                include_consumed=True,
            )
            for spawn in spawns:
                # Check if the matching type (or any if no type) is now registered
                if subagent_type is None or spawn.get("subagent_type") == subagent_type:
                    if spawn["status"] == "registered":
                        return {"registered": True, "spawn": spawn}
                    elif spawn["status"] == "expired":
                        # Found but expired — keep looking for a newer one
                        pass
            time.sleep(poll_interval)

        return {
            "timed_out": True,
            "timeout": timeout,
            "parent_agent_id": parent_agent_id,
            "subagent_type": subagent_type,
        }

    # ------------------------------------------------------------------ #
    # Health Polling + Deregistration Requests
    # ------------------------------------------------------------------ #

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
        """Poll until a child agent is stopped or the timeout is reached.

        Returns ``stopped: True`` if the child called ``deregister_agent`` within
        the timeout. Returns ``timed_out: True`` with ``escalate: True`` if the
        child did not stop in time — the caller should then call
        ``deregister_agent`` directly to force cleanup.
        """
        return _spawner.await_agent_stopped(
            connect=self._connect,
            child_agent_id=child_agent_id,
            timeout=timeout,
        )
