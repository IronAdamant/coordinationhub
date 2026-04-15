"""DependencyMixin — cross-agent dependency declarations and checks.

Expects the host class to provide:
    self._connect() — callable returning a sqlite3 connection

Delegates to: dependencies (dependencies.py)
"""

from __future__ import annotations

from typing import Any

from . import dependencies as _deps


class DependencyMixin:
    """Declarative dependency graph between agents."""

    def declare_dependency(
        self,
        dependent_agent_id: str,
        depends_on_agent_id: str,
        depends_on_task_id: str | None = None,
        condition: str = "task_completed",
    ) -> dict[str, Any]:
        """Declare that dependent_agent needs depends_on_agent to finish first."""
        result = _deps.declare_dependency(
            self._connect, dependent_agent_id, depends_on_agent_id,
            depends_on_task_id, condition,
        )
        self._publish_event(
            "dependency.declared",
            {
                "dep_id": result.get("dep_id"),
                "dependent_agent_id": dependent_agent_id,
                "depends_on_agent_id": depends_on_agent_id,
                "depends_on_task_id": depends_on_task_id,
                "condition": condition,
            },
        )
        return result

    def manage_dependencies(
        self,
        mode: str,
        agent_id: str,
    ) -> dict[str, Any]:
        """Unified dependency query: check | blockers | assert."""
        if mode == "check":
            unsatisfied = _deps.check_dependencies(self._connect, agent_id)
            return {
                "agent_id": agent_id,
                "blocked": len(unsatisfied) > 0,
                "unsatisfied": unsatisfied,
            }
        if mode == "blockers":
            unsatisfied = _deps.check_dependencies(self._connect, agent_id)
            return {
                "agent_id": agent_id,
                "blocked": len(unsatisfied) > 0,
                "unsatisfied": unsatisfied,
            }
        if mode == "assert":
            unsatisfied = _deps.check_dependencies(self._connect, agent_id)
            if unsatisfied:
                return {"can_start": False, "blockers": unsatisfied}
            return {"can_start": True}
        return {"error": f"Unknown mode: {mode!r}"}

    def satisfy_dependency(self, dep_id: int) -> dict[str, Any]:
        """Mark a dependency as satisfied."""
        result = _deps.satisfy_dependency(self._connect, dep_id)
        self._publish_event(
            "dependency.satisfied",
            {"dep_id": dep_id},
        )
        return result

    def get_all_dependencies(
        self, dependent_agent_id: str | None = None,
    ) -> dict[str, Any]:
        """Get all declared dependencies."""
        deps = _deps.get_all_dependencies(self._connect, dependent_agent_id)
        return {"dependencies": deps, "count": len(deps)}

    def wait_for_dependency(
        self,
        dep_id: int,
        timeout_s: float = 60.0,
        poll_interval_s: float = 2.0,
    ) -> dict[str, Any]:
        """Poll until a dependency is satisfied or timeout expires."""
        return _deps.wait_for_dependency(
            self._connect, dep_id, timeout_s, poll_interval_s,
        )
