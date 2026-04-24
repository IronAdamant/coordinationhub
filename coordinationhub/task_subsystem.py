"""Task subsystem — shared task registry with hierarchy + dead-letter queue.

T6.22 eighth step: extracted out of ``core_tasks.TaskMixin`` into a
standalone class. Coupling audit confirmed TaskMixin had zero
cross-mixin method calls and only relied on three pieces of engine
infrastructure — ``_connect``, ``_publish_event``, ``_hybrid_wait`` —
which are now injected as constructor dependencies. Same three-dep
shape as :class:`Spawner` (commit ``1ee46c6``), :class:`Messaging`
(commit ``d9f84d3``), and :class:`Handoff` (commit ``ded641d``).
See commits ``3d1bd48`` (WorkIntent), ``b4a3e6b`` (Lease),
``d6c8796`` (Dependency), and ``e0c21a8`` (Change) for the other
extractions in this series. This is the largest surface extracted so
far (11+ public methods) and continues breaking the god-object
inheritance chain on ``CoordinationEngine`` without changing
observable behaviour.

Preserves T1.13's status-validation authz — the primitive
``_VALID_TASK_STATUSES`` set in :mod:`tasks` rejects unknown
``update_task_status`` transitions; this layer does not duplicate
that check, it just propagates the primitive's error payload.
Preserves T6.38 / T6.39 / T6.40: dependency-satisfy and DLQ-record
side effects are folded into the primitive's transaction, the
``error`` argument is forwarded on every transition (not only on
``status=='failed'``), and events only fire when the stored status
actually changed (``prior_status != status``).

Preserves T6.37: ``query_tasks`` keeps its dispatch-by-string shape
— splitting it into separate methods is deferred.

Delegates to: tasks (tasks.py) for registry primitives, task_failures
(task_failures.py) for dead-letter queue primitives. Auto-trigger of
dependency satisfaction happens inside the ``_tasks.update_task_status``
primitive (T6.38), so this layer does not call into the Dependency
subsystem directly.
"""

from __future__ import annotations

import time as _time
from typing import Any, Callable

from . import tasks as _tasks
from . import task_failures as _tf


class Task:
    """Task registry with parent-child hierarchy and dependency tracking.

    Constructed by :class:`CoordinationEngine` and exposed as
    ``engine._task``. The engine keeps facade methods for each
    public operation so the existing tool API is preserved.
    """

    def __init__(
        self,
        connect_fn: Callable[[], Any],
        publish_event_fn: Callable[[str, dict[str, Any]], None],
        hybrid_wait_fn: Callable[..., dict[str, Any] | None],
    ) -> None:
        self._connect = connect_fn
        self._publish_event = publish_event_fn
        self._hybrid_wait = hybrid_wait_fn

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
        folded into the primitive's transaction. The subsystem's job is
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
        """Unified dead-letter queue operations: retry | list_dead_letter | history.

        Routes through this subsystem's own methods (``retry_task``,
        ``get_dead_letter_tasks``, ``get_task_failure_history``) so the
        class stays self-contained and the engine facade does not need
        to be involved in internal dispatch.
        """
        if action == "retry":
            if not task_id:
                return {"error": "task_id is required for retry"}
            return self.retry_task(task_id)
        if action == "list_dead_letter":
            return self.get_dead_letter_tasks(limit)
        if action == "history":
            if not task_id:
                return {"error": "task_id is required for history"}
            return self.get_task_failure_history(task_id)
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
        # T7.15: ``_time`` is imported at module top.
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
