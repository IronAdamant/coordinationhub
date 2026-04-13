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
        return _deps.declare_dependency(
            self._connect, dependent_agent_id, depends_on_agent_id,
            depends_on_task_id, condition,
        )

    def check_dependencies(self, agent_id: str) -> dict[str, Any]:
        """Check unsatisfied dependencies for an agent."""
        unsatisfied = _deps.check_dependencies(self._connect, agent_id)
        return {
            "agent_id": agent_id,
            "blocked": len(unsatisfied) > 0,
            "unsatisfied": unsatisfied,
        }

    def satisfy_dependency(self, dep_id: int) -> dict[str, Any]:
        """Mark a dependency as satisfied."""
        return _deps.satisfy_dependency(self._connect, dep_id)

    def get_blockers(self, agent_id: str) -> dict[str, Any]:
        """Alias for check_dependencies."""
        return self.check_dependencies(agent_id)

    def assert_can_start(self, agent_id: str) -> dict[str, Any]:
        """Structured check before starting work. Returns can_start bool."""
        result = self.check_dependencies(agent_id)
        if result["blocked"]:
            return {"can_start": False, "blockers": result["unsatisfied"]}
        return {"can_start": True}

    def get_all_dependencies(
        self, dependent_agent_id: str | None = None,
    ) -> dict[str, Any]:
        """Get all declared dependencies."""
        deps = _deps.get_all_dependencies(self._connect, dependent_agent_id)
        return {"dependencies": deps, "count": len(deps)}