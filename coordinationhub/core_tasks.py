"""TaskMixin — shared task registry with hierarchy support.

Expects the host class to provide:
    self._connect() — callable returning a sqlite3 connection

Delegates to: tasks (tasks.py), dependencies (dependencies.py) for auto-trigger,
task_failures (task_failures.py) for dead letter queue.

Auto-trigger: when a task is marked completed, dependencies referencing that
task_id are automatically satisfied.
"""

from __future__ import annotations

from typing import Any

from . import tasks as _tasks
from . import dependencies as _deps
from . import task_failures as _tf


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
        priority: int = 0,
    ) -> dict[str, Any]:
        """Create a new task in the shared registry."""
        return _tasks.create_task(
            self._connect, task_id, parent_agent_id, description, depends_on, priority,
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
        error: str | None = None,
    ) -> dict[str, Any]:
        """Update task status. Auto-satisfies dependencies when a task completes."""
        result = _tasks.update_task_status(
            self._connect, task_id, status, summary, blocked_by,
        )
        # Auto-trigger: when task completes, satisfy any deps that reference it
        if status == "completed":
            _deps.satisfy_dependencies_for_task(self._connect, task_id)
        # Auto-record failure: when task fails, record in DLQ
        if status == "failed":
            _tf.record_task_failure(self._connect, task_id, error)
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
        priority: int = 0,
    ) -> dict[str, Any]:
        """Create a subtask under an existing parent task."""
        return _tasks.create_subtask(
            self._connect, task_id, parent_task_id, parent_agent_id, description, depends_on, priority,
        )

    def get_subtasks(self, parent_task_id: str) -> dict[str, Any]:
        """Get all direct subtasks of a given task."""
        subtasks = _tasks.get_subtasks(self._connect, parent_task_id)
        return {"subtasks": subtasks, "count": len(subtasks)}

    def get_task_tree(self, root_task_id: str) -> dict[str, Any]:
        """Get a task with all subtasks recursively as a nested tree."""
        tree = _tasks.get_task_tree(self._connect, root_task_id)
        return tree if tree else {"error": f"Task {root_task_id!r} not found"}

    # ------------------------------------------------------------------ #
    # Dead Letter Queue
    # ------------------------------------------------------------------ #

    def retry_task(self, task_id: str) -> dict[str, Any]:
        """Retry a task from the dead letter queue.

        Resets task to 'pending' status and marks DLQ entry as 'retried'.
        """
        return _tf.retry_from_dead_letter(self._connect, task_id)

    def get_dead_letter_tasks(self, limit: int = 50) -> dict[str, Any]:
        """Return tasks currently in the dead letter queue."""
        tasks = _tf.get_dead_letter_tasks(self._connect, limit)
        return {"dead_letter_tasks": tasks, "count": len(tasks)}

    def get_task_failure_history(self, task_id: str) -> dict[str, Any]:
        """Return failure history for a task."""
        history = _tf.get_task_failure_history(self._connect, task_id)
        return {"task_id": task_id, "history": history, "count": len(history)}

    def wait_for_task(
        self,
        task_id: str,
        timeout_s: float = 60.0,
        poll_interval_s: float = 2.0,
    ) -> dict[str, Any]:
        """Poll until a task reaches a terminal state (completed/failed) or timeout expires.

        Use this to coordinate sequential dependencies between tasks when
        depends_on alone is not sufficient (e.g., waiting for a task
        completed by an external agent).
        """
        return _tasks.wait_for_task(self._connect, task_id, timeout_s, poll_interval_s)

    def get_available_tasks(self, agent_id: str | None = None) -> dict[str, Any]:
        """Return tasks whose depends_on are all satisfied (completed) and not currently claimed.

        A task is \"available\" if:
        - Its status is \"pending\" (not yet claimed)
        - All tasks in its depends_on list have status \"completed\"

        Use this to find work that can be picked up by an idle agent.
        """
        tasks = _tasks.get_available_tasks(self._connect, agent_id)
        return {"tasks": tasks, "count": len(tasks)}