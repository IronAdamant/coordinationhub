"""TaskMixin — shared task registry with hierarchy support.

Expects the host class to provide:
    self._connect() — callable returning a sqlite3 connection

Delegates to: tasks (tasks.py), dependencies (dependencies.py) for auto-trigger

Auto-trigger: when a task is marked completed, dependencies referencing that
task_id are automatically satisfied.
"""

from __future__ import annotations

from typing import Any

from . import tasks as _tasks
from . import dependencies as _deps


class TaskMixin:
    """Task registry with parent-child hierarchy and dependency tracking."""

    # ------------------------------------------------------------------ #
    # Task Registry
    # ------------------------------------------------------------------ #

    def create_task(
        self,
        task_id: str,
        parent_agent_id: str,
        description: str,
        depends_on: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a new task in the shared registry."""
        return _tasks.create_task(
            self._connect, task_id, parent_agent_id, description, depends_on,
        )

    def assign_task(self, task_id: str, assigned_agent_id: str) -> dict[str, Any]:
        """Assign a task to an agent."""
        return _tasks.assign_task(self._connect, task_id, assigned_agent_id)

    def update_task_status(
        self,
        task_id: str,
        status: str,
        summary: str | None = None,
        blocked_by: str | None = None,
    ) -> dict[str, Any]:
        """Update task status. Auto-satisfies dependencies when a task completes."""
        result = _tasks.update_task_status(
            self._connect, task_id, status, summary, blocked_by,
        )
        # Auto-trigger: when task completes, satisfy any deps that reference it
        if status == "completed":
            _deps.satisfy_dependencies_for_task(self._connect, task_id)
        return result

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        """Get a single task by ID."""
        return _tasks.get_task(self._connect, task_id)

    def get_child_tasks(self, parent_agent_id: str) -> dict[str, Any]:
        """Get all tasks created by a given agent."""
        tasks = _tasks.get_child_tasks(self._connect, parent_agent_id)
        return {"tasks": tasks, "count": len(tasks)}

    def get_tasks_by_agent(self, assigned_agent_id: str) -> dict[str, Any]:
        """Get all tasks assigned to a given agent."""
        tasks = _tasks.get_tasks_by_agent(self._connect, assigned_agent_id)
        return {"tasks": tasks, "count": len(tasks)}

    def get_all_tasks(self) -> dict[str, Any]:
        """Get all tasks in the registry."""
        tasks = _tasks.get_all_tasks(self._connect)
        return {"tasks": tasks, "count": len(tasks)}

    def create_subtask(
        self,
        task_id: str,
        parent_task_id: str,
        parent_agent_id: str,
        description: str,
        depends_on: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a subtask under an existing parent task."""
        return _tasks.create_subtask(
            self._connect, task_id, parent_task_id, parent_agent_id, description, depends_on,
        )

    def get_subtasks(self, parent_task_id: str) -> dict[str, Any]:
        """Get all direct subtasks of a given task."""
        subtasks = _tasks.get_subtasks(self._connect, parent_task_id)
        return {"subtasks": subtasks, "count": len(subtasks)}

    def get_task_tree(self, root_task_id: str) -> dict[str, Any]:
        """Get a task with all subtasks recursively as a nested tree."""
        tree = _tasks.get_task_tree(self._connect, root_task_id)
        return tree if tree else {"error": f"Task {root_task_id!r} not found"}