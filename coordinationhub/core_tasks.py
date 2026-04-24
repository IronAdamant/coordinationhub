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
        result = _tasks.create_task(
            self._connect, task_id, parent_agent_id, description, depends_on, priority,
        )
        self._publish_event(
            "task.created",
            {
                "task_id": task_id,
                "parent_agent_id": parent_agent_id,
                "description": description,
            },
        )
        return result

    def assign_task(self, task_id: str, assigned_agent_id: str) -> dict[str, Any]:
        """Assign a task to an agent."""
        result = _tasks.assign_task(self._connect, task_id, assigned_agent_id)
        self._publish_event(
            "task.assigned",
            {"task_id": task_id, "assigned_agent_id": assigned_agent_id},
        )
        return result

    def update_task_status(
        self,
        task_id: str,
        status: str,
        summary: str | None = None,
        blocked_by: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        """Update task status. Auto-satisfies dependencies when a task completes.

        T6.39: ``error`` is forwarded to the primitive on every transition,
        not only when ``status=='failed'``. A ``blocked`` / ``in_progress``
        transition carrying a diagnostic message is now preserved on the
        task row.

        T6.38: dependency-satisfy and DLQ-record side effects are now
        folded into the primitive's transaction. The mixin's job is
        only to fan events out after the write commits — this layer
        can no longer crash between status write and side effect.

        T6.40: events fire only when the status actually changed.
        """
        result = _tasks.update_task_status(
            self._connect, task_id, status, summary, blocked_by, error,
        )
        prior_status = result.get("prior_status")
        changed = result.get("updated") and prior_status != status
        if status == "completed" and changed:
            self._publish_event(
                "task.completed", {"task_id": task_id, "status": "completed"}
            )
        if status == "failed" and changed:
            self._publish_event(
                "task.failed", {"task_id": task_id, "status": "failed"}
            )
        return result

    def query_tasks(
        self,
        query_type: str,
        task_id: str | None = None,
        parent_agent_id: str | None = None,
        assigned_agent_id: str | None = None,
        parent_task_id: str | None = None,
        root_task_id: str | None = None,
    ) -> dict[str, Any]:
        """Unified task query: task | child | by_agent | all | subtasks | tree."""
        if query_type == "task":
            if not task_id:
                return {"error": "task_id is required for query_type='task'"}
            item = _tasks.get_task(self._connect, task_id)
            return {"task": item} if item else {"error": f"Task {task_id!r} not found"}
        if query_type == "child":
            if not parent_agent_id:
                return {"error": "parent_agent_id is required for query_type='child'"}
            tasks = _tasks.get_child_tasks(self._connect, parent_agent_id)
            return {"tasks": tasks, "count": len(tasks)}
        if query_type == "by_agent":
            if not assigned_agent_id:
                return {"error": "assigned_agent_id is required for query_type='by_agent'"}
            tasks = _tasks.get_tasks_by_agent(self._connect, assigned_agent_id)
            return {"tasks": tasks, "count": len(tasks)}
        if query_type == "all":
            tasks = _tasks.get_all_tasks(self._connect)
            return {"tasks": tasks, "count": len(tasks)}
        if query_type == "subtasks":
            if not parent_task_id:
                return {"error": "parent_task_id is required for query_type='subtasks'"}
            subtasks = _tasks.get_subtasks(self._connect, parent_task_id)
            return {"subtasks": subtasks, "count": len(subtasks)}
        if query_type == "tree":
            if not root_task_id:
                return {"error": "root_task_id is required for query_type='tree'"}
            tree = _tasks.get_task_tree(self._connect, root_task_id)
            return tree if tree else {"error": f"Task {root_task_id!r} not found"}
        return {"error": f"Unknown query_type: {query_type!r}"}

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

    # ------------------------------------------------------------------ #
    # Dead Letter Queue
    # ------------------------------------------------------------------ #

    def task_failures(
        self,
        action: str,
        task_id: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Unified dead-letter queue operations: retry | list_dead_letter | history."""
        if action == "retry":
            if not task_id:
                return {"error": "task_id is required for retry"}
            return _tf.retry_from_dead_letter(self._connect, task_id)
        if action == "list_dead_letter":
            tasks = _tf.get_dead_letter_tasks(self._connect, limit)
            return {"dead_letter_tasks": tasks, "count": len(tasks)}
        if action == "history":
            if not task_id:
                return {"error": "task_id is required for history"}
            history = _tf.get_task_failure_history(self._connect, task_id)
            return {"task_id": task_id, "history": history, "count": len(history)}
        return {"error": f"Unknown action: {action!r}"}

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
        """Wait until a task reaches a terminal state (completed/failed) or timeout expires.

        Uses the event bus for low-latency notification.
        """
        import time as _time
        start = _time.time()
        task = _tasks.get_task(self._connect, task_id)
        if task and task.get("status") in ("completed", "failed"):
            return {
                "task_id": task_id,
                "status": task["status"],
                "timed_out": False,
                "waited_s": _time.time() - start,
            }

        event = self._hybrid_wait(
            ["task.completed", "task.failed"],
            filter_fn=lambda e: e.get("task_id") == task_id,
            timeout=timeout_s,
        )
        if event:
            return {
                "task_id": task_id,
                "status": event.get("status", "unknown"),
                "timed_out": False,
                "waited_s": _time.time() - start,
            }
        return {
            "task_id": task_id,
            "status": "timeout",
            "timed_out": True,
            "timeout_s": timeout_s,
        }

    def get_available_tasks(self, agent_id: str | None = None) -> dict[str, Any]:
        """Return tasks whose dependencies are satisfied and that are not claimed."""
        tasks = _tasks.get_available_tasks(self._connect, agent_id)
        return {"tasks": tasks, "count": len(tasks)}
