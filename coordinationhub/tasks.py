"""Task registry primitives for CoordinationHub (work board).

Supports a shared task board where parent agents assign work to child agents
via task IDs. Task summaries enable compression chains: child writes a summary
on completion, parent compresses it upward.

Zero internal dependencies — receives connect() from caller.
"""

from __future__ import annotations

import json
import time
from typing import Any

from .db import ConnectFn
from .limits import (
    MAX_DESCRIPTION,
    MAX_ERROR,
    MAX_SUMMARY,
    truncate,
)


def create_task(
    connect: ConnectFn,
    task_id: str,
    parent_agent_id: str,
    description: str,
    depends_on: list[str] | None = None,
    priority: int = 0,
) -> dict[str, Any]:
    """Create a new task in the task registry (work board).

    T6.14: ``description`` is truncated to :data:`MAX_DESCRIPTION` so
    a runaway caller can't wedge tens of megabytes into the DB. Override
    the cap via ``COORDINATIONHUB_MAX_DESCRIPTION``.
    """
    description = truncate(description, MAX_DESCRIPTION)
    now = time.time()
    with connect() as conn:
        cursor = conn.execute(
            """INSERT INTO tasks
            (id, parent_agent_id, description, created_at, updated_at, depends_on, priority)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (task_id, parent_agent_id, description, now, now,
             json.dumps(depends_on) if depends_on else "[]", priority),
        )
    return {"created": True, "task_id": task_id, "priority": priority}


def assign_task(
    connect: ConnectFn,
    task_id: str,
    assigned_agent_id: str,
) -> dict[str, Any]:
    """Assign a task to an agent.

    T6.32: when an already-assigned task is reassigned, the previous
    assignee's ``current_task`` is cleared so it no longer points at
    work they no longer own. The new assignee's row is upserted in the
    same transaction so no window exists where both agents claim the
    task as current.
    """
    now = time.time()
    with connect() as conn:
        prior = conn.execute(
            "SELECT assigned_agent_id, description FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()
        if prior is None:
            return {"assigned": False, "reason": "task_not_found", "task_id": task_id}
        prior_assignee = prior["assigned_agent_id"]
        description = prior["description"]

        conn.execute(
            "UPDATE tasks SET assigned_agent_id=?, updated_at=? WHERE id=?",
            (assigned_agent_id, now, task_id),
        )
        # T6.32: clear the prior assignee's current_task if it still points
        # at this task. We match on current_task = description rather than
        # task_id because agent_responsibilities stores descriptions — a
        # stricter schema (task_id FK) is deferred to the migration bundle.
        if prior_assignee and prior_assignee != assigned_agent_id:
            conn.execute(
                "UPDATE agent_responsibilities SET current_task = NULL, updated_at = ? "
                "WHERE agent_id = ? AND current_task = ?",
                (now, prior_assignee, description),
            )
        # Sync agent state: update current_task in agent_responsibilities
        conn.execute("""
            INSERT INTO agent_responsibilities (agent_id, current_task, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                current_task = excluded.current_task,
                updated_at = excluded.updated_at
        """, (assigned_agent_id, description, now))
    return {"assigned": True, "task_id": task_id}


# T1.13: canonical task status vocabulary. Any value outside this set
# is rejected at the primitive boundary (the DB CHECK constraint is
# deferred to the v22 schema migration bundle).
_VALID_TASK_STATUSES = {
    "pending", "in_progress", "completed", "blocked", "failed", "dead_letter",
}


def update_task_status(
    connect: ConnectFn,
    task_id: str,
    status: str,
    summary: str | None = None,
    blocked_by: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Update task status, optionally with a completion summary or blocker.

    T1.13: validates ``status`` against a canonical vocabulary and checks
    that the task exists. Returns ``{"updated": False, "reason": ...}`` on
    invalid status or missing task — previously accepted arbitrary strings
    silently (a misspelled status like "done" would be stored and leave
    dependents hanging forever because no status-change event fires on
    unknown values).

    T6.39: ``error`` is persisted alongside every status transition when
    non-None. Callers moving a task to ``blocked`` or ``in_progress`` can
    now preserve diagnostic context on the task row itself (prior to the
    fix, the string was only surfaced via the DLQ on ``failed``).

    T6.38: when the transition is to ``completed`` or ``failed``, the
    dependency-satisfy / DLQ-record side effects fold into the same
    transaction as the status write. Prior code committed the status
    update first and invoked the side-effect primitives in their own
    connections — a crash between left dependents hanging (completed
    without satisfy) or the DLQ empty (failed without record). The fold
    uses local imports to avoid circular deps between the three modules.
    """
    if status not in _VALID_TASK_STATUSES:
        return {
            "updated": False,
            "reason": "invalid_status",
            "task_id": task_id,
            "status": status,
            "valid_statuses": sorted(_VALID_TASK_STATUSES),
        }
    # T6.14: bound the free-text fields before they hit the DB.
    summary = truncate(summary, MAX_SUMMARY)
    error = truncate(error, MAX_ERROR)
    now = time.time()
    failure_record: dict[str, Any] | None = None
    satisfied_count = 0
    with connect() as conn:
        existing = conn.execute(
            "SELECT status FROM tasks WHERE id = ?", (task_id,),
        ).fetchone()
        if existing is None:
            return {
                "updated": False,
                "reason": "task_not_found",
                "task_id": task_id,
            }
        prior_status = existing["status"]
        fields = ["status=?", "updated_at=?"]
        params: list[Any] = [status, now]
        if summary is not None:
            fields.append("summary=?")
            params.append(summary)
        if blocked_by is not None:
            fields.append("blocked_by=?")
            params.append(blocked_by)
        if error is not None:
            fields.append("error=?")
            params.append(error)
        params.append(task_id)
        conn.execute(
            f"UPDATE tasks SET {', '.join(fields)} WHERE id=?",
            tuple(params),
        )

        # T6.38: perform side effects inside the same connection context
        # so a crash after the status UPDATE but before dep-satisfy /
        # DLQ-record can't leave inconsistent state. Only fire on real
        # state transitions (T6.40).
        is_transition = prior_status != status
        if status == "completed" and is_transition:
            cursor = conn.execute(
                """UPDATE agent_dependencies
                   SET satisfied=1, satisfied_at=?
                   WHERE depends_on_task_id=? AND satisfied=0""",
                (now, task_id),
            )
            satisfied_count = cursor.rowcount
        elif status == "failed" and is_transition:
            # Local import: task_failures imports nothing from tasks.py so
            # there's no cycle risk, but keeping it local avoids widening
            # the module header for a conditional code path.
            from . import task_failures as _tf
            failure_record = _tf.record_task_failure(
                lambda: _LiveConn(conn), task_id, error,
            )
    return {
        "updated": True,
        "task_id": task_id,
        "status": status,
        "prior_status": prior_status,
        "dependencies_satisfied": satisfied_count,
        "failure_record": failure_record,
    }


class _LiveConn:
    """Context manager that yields an existing connection without opening
    or committing a transaction. Used by :func:`update_task_status` so
    primitives that accept a ``connect`` callable can piggy-back on an
    already-active transaction (T6.38).
    """
    __slots__ = ("_conn",)

    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self._conn

    def __exit__(self, exc_type, exc, tb):
        return False


def get_task(connect: ConnectFn, task_id: str) -> dict[str, Any] | None:
    """Get a single task by ID."""
    with connect() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
    if d.get("depends_on"):
        d["depends_on"] = json.loads(d["depends_on"])
    return d


def get_child_tasks(
    connect: ConnectFn,
    parent_agent_id: str,
) -> list[dict[str, Any]]:
    """Get all tasks created by a given agent."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE parent_agent_id=? ORDER BY priority DESC, created_at",
            (parent_agent_id,),
        ).fetchall()
        tasks = []
        for row in rows:
            d = dict(row)
            if d.get("depends_on"):
                d["depends_on"] = json.loads(d["depends_on"])
            tasks.append(d)
    return tasks


def get_tasks_by_agent(
    connect: ConnectFn,
    assigned_agent_id: str,
) -> list[dict[str, Any]]:
    """Get all tasks assigned to a given agent."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE assigned_agent_id=? ORDER BY priority DESC, created_at",
            (assigned_agent_id,),
        ).fetchall()
        tasks = []
        for row in rows:
            d = dict(row)
            if d.get("depends_on"):
                d["depends_on"] = json.loads(d["depends_on"])
            tasks.append(d)
    return tasks


def get_all_tasks(connect: ConnectFn) -> list[dict[str, Any]]:
    """Get all tasks in the registry."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY priority DESC, created_at"
        ).fetchall()
        tasks = []
        for row in rows:
            d = dict(row)
            if d.get("depends_on"):
                d["depends_on"] = json.loads(d["depends_on"])
            tasks.append(d)
    return tasks


MAX_TASK_DEPTH = 100


def _would_create_cycle(
    conn,
    task_id: str,
    parent_task_id: str,
) -> bool:
    """Walk up from ``parent_task_id`` via parent_task_id links. If we
    ever reach ``task_id`` (or exceed MAX_TASK_DEPTH), creating the
    subtask would introduce a cycle.

    T1.14: prevents `create_subtask` from wiring a task as a descendant
    of itself, which would send `get_task_tree` into infinite recursion
    and crash the process.
    """
    cursor_id = parent_task_id
    visited = set()
    depth = 0
    while cursor_id is not None:
        if cursor_id == task_id:
            return True
        if cursor_id in visited:
            # Existing cycle in parent chain — reject to avoid propagating.
            return True
        if depth >= MAX_TASK_DEPTH:
            return True
        visited.add(cursor_id)
        row = conn.execute(
            "SELECT parent_task_id FROM tasks WHERE id = ?",
            (cursor_id,),
        ).fetchone()
        if row is None:
            return False
        cursor_id = row["parent_task_id"]
        depth += 1
    return False


def create_subtask(
    connect: ConnectFn,
    task_id: str,
    parent_task_id: str,
    parent_agent_id: str,
    description: str,
    depends_on: list[str] | None = None,
    priority: int = 0,
) -> dict[str, Any]:
    """Create a new subtask under an existing parent task.

    T1.14: rejects creation if it would introduce a cycle in the
    parent_task_id chain, or if the parent chain is already deeper than
    MAX_TASK_DEPTH. Returns ``{"created": False, "reason": "cycle"}`` or
    ``{"created": False, "reason": "parent_not_found"}`` in those cases.

    T6.14: ``description`` is truncated to ``MAX_DESCRIPTION`` before
    the write.
    """
    description = truncate(description, MAX_DESCRIPTION)
    now = time.time()
    with connect() as conn:
        parent_row = conn.execute(
            "SELECT id FROM tasks WHERE id = ?", (parent_task_id,),
        ).fetchone()
        if parent_row is None:
            return {
                "created": False,
                "reason": "parent_not_found",
                "task_id": task_id,
                "parent_task_id": parent_task_id,
            }
        if _would_create_cycle(conn, task_id, parent_task_id):
            return {
                "created": False,
                "reason": "cycle",
                "task_id": task_id,
                "parent_task_id": parent_task_id,
            }
        conn.execute(
            """INSERT INTO tasks
            (id, parent_task_id, parent_agent_id, description, created_at, updated_at, depends_on, priority)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (task_id, parent_task_id, parent_agent_id, description, now, now,
             json.dumps(depends_on) if depends_on else "[]", priority),
        )
    return {"created": True, "task_id": task_id, "parent_task_id": parent_task_id, "priority": priority}


def get_subtasks(connect: ConnectFn, parent_task_id: str) -> list[dict[str, Any]]:
    """Get all direct subtasks of a given task."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE parent_task_id=? ORDER BY priority DESC, created_at",
            (parent_task_id,),
        ).fetchall()
        tasks = []
        for row in rows:
            d = dict(row)
            if d.get("depends_on"):
                d["depends_on"] = json.loads(d["depends_on"])
            tasks.append(d)
    return tasks


def get_task_tree(connect: ConnectFn, root_task_id: str) -> dict[str, Any]:
    """Get a task with all its subtasks recursively.

    Returns a dict with task data + 'subtasks' key containing list of child task trees.

    T1.14: caps recursion at ``MAX_TASK_DEPTH`` and tracks a visited set
    so a cycle in ``parent_task_id`` (which create_subtask now rejects,
    but legacy DBs might contain) can't blow the Python stack.

    T6.36: the whole tree is fetched in a single ``WITH RECURSIVE`` CTE
    query. Previously each node cost one round trip (one SELECT for the
    row, one SELECT for its direct children); deep trees paid O(N)
    connections on the thread-local pool. The CTE walks the tree
    server-side and caps at ``MAX_TASK_DEPTH`` via a depth column in
    the recursive term.
    """
    with connect() as conn:
        rows = conn.execute(
            """
            WITH RECURSIVE tree(id, depth) AS (
                SELECT id, 0 FROM tasks WHERE id = ?
                UNION ALL
                SELECT t.id, tree.depth + 1
                FROM tasks t
                JOIN tree ON t.parent_task_id = tree.id
                WHERE tree.depth < ?
            )
            SELECT t.*, tree.depth AS _depth
            FROM tree
            JOIN tasks t ON t.id = tree.id
            ORDER BY tree.depth ASC, t.created_at ASC
            """,
            (root_task_id, MAX_TASK_DEPTH),
        ).fetchall()

    if not rows:
        return {}

    # Build parent → children map from the flat row set.
    by_id: dict[str, dict[str, Any]] = {}
    children_of: dict[str | None, list[str]] = {}
    for row in rows:
        d = dict(row)
        d.pop("_depth", None)
        if d.get("depends_on"):
            d["depends_on"] = json.loads(d["depends_on"])
        d["subtasks"] = []
        by_id[d["id"]] = d
        parent = d.get("parent_task_id")
        children_of.setdefault(parent, []).append(d["id"])

    # T1.14 cycle-guard carries over — a DB cycle would make the CTE
    # infinite, but SQLite caps recursion at its own limit; we also
    # track a visited set at the Python-side assembly to guarantee a
    # finite tree even if the CTE leaked a duplicate node.
    visited: set[str] = set()

    def _attach(node_id: str) -> dict[str, Any]:
        visited.add(node_id)
        node = by_id[node_id]
        for child_id in children_of.get(node_id, []):
            if child_id in visited or child_id not in by_id:
                continue
            node["subtasks"].append(_attach(child_id))
        return node

    if root_task_id not in by_id:
        return {}
    return _attach(root_task_id)


# T6.42: the polling ``wait_for_task`` primitive was replaced by the
# event-bus-based ``TaskMixin.wait_for_task`` in core_tasks.py. The
# polling version had no callers inside the package. Deleted.


def get_available_tasks(
    connect: ConnectFn,
    agent_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return tasks whose dependencies are all satisfied.

    A task is "available" if:
    - Its status is "pending" (not yet claimed)
    - All tasks in its ``depends_on`` list have status "completed"
    - The assigned agent (if any) has no unsatisfied rows in
      ``agent_dependencies`` (T1.12)
    - Optionally filtered to a specific agent_id

    T6.3: dependency resolution now uses the already-loaded ``all_tasks``
    rather than calling ``get_task`` per dep (one connection per dep).
    For N tasks with average D deps each, the pre-fix cost was O(N*D)
    round trips plus O(N) agent-blocker queries. The rewrite performs
    one SELECT for the task universe and one SELECT per distinct
    candidate agent.
    """
    import json
    all_tasks = get_all_tasks(connect)
    available = []

    # T6.3: index all tasks by id once so per-dep lookup is O(1) in-memory.
    status_by_id = {t["id"]: t.get("status") for t in all_tasks}

    # T1.12 + T6.3: cache per-agent blocker state; share one connection
    # for every agent_dependencies query (the prior implementation also
    # reused a conn but issued per-dep get_task calls on a separate one).
    agent_blocker_cache: dict[str, bool] = {}
    with connect() as conn:
        def _agent_has_unsatisfied_deps(aid: str) -> bool:
            cached = agent_blocker_cache.get(aid)
            if cached is not None:
                return cached
            # Inline the check_dependencies logic without auto-satisfying
            # side effects — get_available_tasks is a read-heavy query and
            # should not mutate state.
            rows = conn.execute(
                "SELECT depends_on_task_id, condition, depends_on_agent_id "
                "FROM agent_dependencies WHERE dependent_agent_id = ? AND satisfied = 0",
                (aid,),
            ).fetchall()
            blocked = False
            for r in rows:
                cond = r["condition"]
                if cond == "task_completed" and r["depends_on_task_id"]:
                    # Dep is a task — satisfied iff its status is 'completed'.
                    # Use the in-memory status map to skip a round trip.
                    dep_status = status_by_id.get(r["depends_on_task_id"])
                    if dep_status != "completed":
                        # If the task isn't in the universe (deleted or
                        # cross-scope), fall back to a direct read so we
                        # can tell "not found" from "pending".
                        if dep_status is None:
                            blocker = conn.execute(
                                "SELECT status FROM tasks WHERE id = ?",
                                (r["depends_on_task_id"],),
                            ).fetchone()
                            if not blocker or blocker["status"] != "completed":
                                blocked = True
                                break
                        else:
                            blocked = True
                            break
                elif cond in ("agent_stopped", "agent_registered"):
                    blocker = conn.execute(
                        "SELECT status FROM agents WHERE agent_id = ?",
                        (r["depends_on_agent_id"],),
                    ).fetchone()
                    if cond == "agent_stopped":
                        if not blocker or blocker["status"] != "stopped":
                            blocked = True
                            break
                    elif cond == "agent_registered":
                        if not blocker or blocker["status"] != "active":
                            blocked = True
                            break
                else:
                    # Unknown condition: be conservative, treat as blocked.
                    blocked = True
                    break
            agent_blocker_cache[aid] = blocked
            return blocked

        for task in all_tasks:
            if task.get("status") not in (None, "pending"):
                continue
            if agent_id and task.get("assigned_agent_id") != agent_id:
                continue

            # Task-level depends_on (JSON array of task IDs)
            depends_on = task.get("depends_on") or []
            if isinstance(depends_on, str):
                try:
                    depends_on = json.loads(depends_on)
                except json.JSONDecodeError:
                    depends_on = []
            deps_satisfied = True
            for dep_id in depends_on:
                # T6.3: O(1) in-memory lookup instead of a fresh
                # ``get_task(connect, dep_id)`` round trip.
                dep_status = status_by_id.get(dep_id)
                if dep_status != "completed":
                    deps_satisfied = False
                    break
            if not deps_satisfied:
                continue

            # Agent-level declared dependencies (T1.12)
            assigned = task.get("assigned_agent_id")
            if assigned and _agent_has_unsatisfied_deps(assigned):
                continue

            available.append(task)
    return available


def suggest_task_assignments(connect: ConnectFn) -> list[dict[str, Any]]:
    """Suggest available tasks for idle agents.

    Returns a list of {task_id, description, suggested_agents} where each
    suggested agent has no currently assigned pending/in_progress tasks.
    """
    available = get_available_tasks(connect)
    if not available:
        return []

    with connect() as conn:
        # Find agents with no active tasks
        agent_rows = conn.execute(
            "SELECT agent_id FROM agents WHERE status = 'active'"
        ).fetchall()
        all_agent_ids = {r["agent_id"] for r in agent_rows}

        busy_rows = conn.execute(
            "SELECT assigned_agent_id FROM tasks WHERE status IN ('pending', 'in_progress')"
        ).fetchall()
        busy_agents = {r["assigned_agent_id"] for r in busy_rows if r["assigned_agent_id"]}

        idle_agents = sorted(all_agent_ids - busy_agents)
    suggestions: list[dict[str, Any]] = []
    for task in available:
        task_id = task["id"]
        description = task.get("description", "")
        # If task is already assigned to an idle agent, highlight that first
        assigned = task.get("assigned_agent_id")
        suggested = []
        if assigned and assigned in idle_agents:
            suggested.append(assigned)
        for aid in idle_agents:
            if aid not in suggested:
                suggested.append(aid)
        suggestions.append({
            "task_id": task_id,
            "description": description,
            "suggested_agents": suggested,
        })
    return suggestions
