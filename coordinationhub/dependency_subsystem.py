"""Dependency subsystem — cross-agent dependency declarations and checks.

T6.22 fourth step: extracted out of ``core_dependencies.DependencyMixin``
into a standalone class. Coupling audit confirmed DependencyMixin had
zero cross-mixin method calls, zero ``_hybrid_wait`` calls, and four
``_publish_event`` calls (on declare / satisfy). DB access is via
``_connect``. Both are now injected as constructor dependencies — same
two-dep shape as :class:`Lease` (see commit ``b4a3e6b``). See commits
``1ee46c6`` (Spawner) and ``3d1bd48`` (WorkIntent) for the earlier
three-dep extractions in this series. This continues breaking the
god-object inheritance chain on ``CoordinationEngine`` without changing
observable behaviour.

Note on cross-subsystem calls: ``TaskMixin.update_task_status`` calls
``_deps.satisfy_dependencies_for_task(...)`` directly against the
primitive module — that's a primitive-layer call, not a mixin-to-mixin
call, and is unaffected by this refactor.

Delegates to: dependencies (dependencies.py) for dependency DB primitives.
"""

from __future__ import annotations

from typing import Any, Callable

from . import dependencies as _deps


class Dependency:
    """Declarative dependency graph between agents.

    Constructed by :class:`CoordinationEngine` and exposed as
    ``engine._dependency``. The engine keeps facade methods for each
    public operation so the existing tool API is preserved.
    """

    def __init__(
        self,
        connect_fn: Callable[[], Any],
        publish_event_fn: Callable[[str, dict[str, Any]], None],
    ) -> None:
        self._connect = connect_fn
        self._publish_event = publish_event_fn

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
