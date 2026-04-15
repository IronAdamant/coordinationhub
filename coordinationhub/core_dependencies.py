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
        agent_id: str | None = None,
        dependent_agent_id: str | None = None,
        depends_on_agent_id: str | None = None,
        depends_on_task_id: str | None = None,
        condition: str = "task_completed",
        dep_id: int | None = None,
        timeout_s: float = 60.0,
        poll_interval_s: float = 2.0,
    ) -> dict[str, Any]:
        """Unified dependency management: declare | check | blockers | assert | satisfy | list | wait."""
        if mode == "declare":
            if not dependent_agent_id or not depends_on_agent_id:
                return {"error": "dependent_agent_id and depends_on_agent_id are required for declare"}
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
        if mode in ("check", "blockers"):
            if not agent_id:
                return {"error": "agent_id is required for check/blockers"}
            unsatisfied = _deps.check_dependencies(self._connect, agent_id)
            return {
                "agent_id": agent_id,
                "blocked": len(unsatisfied) > 0,
                "unsatisfied": unsatisfied,
            }
        if mode == "assert":
            if not agent_id:
                return {"error": "agent_id is required for assert"}
            unsatisfied = _deps.check_dependencies(self._connect, agent_id)
            if unsatisfied:
                return {"can_start": False, "blockers": unsatisfied}
            return {"can_start": True}
        if mode == "satisfy":
            if dep_id is None:
                return {"error": "dep_id is required for satisfy"}
            result = _deps.satisfy_dependency(self._connect, dep_id)
            self._publish_event("dependency.satisfied", {"dep_id": dep_id})
            return result
        if mode == "list":
            deps = _deps.get_all_dependencies(self._connect, agent_id)
            return {"dependencies": deps, "count": len(deps)}
        if mode == "wait":
            if dep_id is None:
                return {"error": "dep_id is required for wait"}
            return _deps.wait_for_dependency(
                self._connect, dep_id, timeout_s, poll_interval_s,
            )
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
