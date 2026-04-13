"""CLI commands for the task registry."""

from __future__ import annotations

from .cli_utils import print_json as _print_json, engine_from_args as _engine_from_args
from .cli_utils import replica_engine_from_args as _replica_engine_from_args, close as _close


# ------------------------------------------------------------------ #
# create-task
# ------------------------------------------------------------------ #

def cmd_create_task(args):
    engine = _engine_from_args(args)
    try:
        result = engine.create_task(
            task_id=args.task_id,
            parent_agent_id=args.parent_agent_id,
            description=args.description,
            depends_on=getattr(args, "depends_on", None),
            priority=getattr(args, "priority", 0),
        )
        if args.json_output:
            _print_json(result)
        else:
            print(f"Task created: {args.task_id}")
            if getattr(args, "depends_on", None):
                print(f"  Depends on: {', '.join(args.depends_on)}")
            p = getattr(args, "priority", 0)
            if p:
                print(f"  Priority: {p}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# assign-task
# ------------------------------------------------------------------ #

def cmd_assign_task(args):
    engine = _engine_from_args(args)
    try:
        result = engine.assign_task(args.task_id, args.assigned_agent_id)
        if args.json_output:
            _print_json(result)
        else:
            print(f"Task assigned: {args.task_id} → {args.assigned_agent_id}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# update-task-status
# ------------------------------------------------------------------ #

def cmd_update_task_status(args):
    engine = _engine_from_args(args)
    try:
        result = engine.update_task_status(
            task_id=args.task_id,
            status=args.status,
            summary=getattr(args, "summary", None),
            blocked_by=getattr(args, "blocked_by", None),
            error=getattr(args, "error", None),
        )
        if args.json_output:
            _print_json(result)
        else:
            print(f"Task updated: {args.task_id} → {args.status}")
            if getattr(args, "summary", None):
                print(f"  Summary: {args.summary[:80]}{'...' if len(getattr(args, 'summary', '')) > 80 else ''}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# get-task
# ------------------------------------------------------------------ #

def cmd_get_task(args):
    engine = _replica_engine_from_args(args)
    try:
        result = engine.get_task(args.task_id)
        if args.json_output:
            _print_json(result)
        elif result is None:
            print(f"Task not found: {args.task_id}")
        else:
            print(f"Task: {result['id']}")
            print(f"  Status: {result['status']}")
            print(f"  Parent: {result['parent_agent_id']}")
            if result.get("assigned_agent_id"):
                print(f"  Assigned: {result['assigned_agent_id']}")
            print(f"  Description: {result['description']}")
            if result.get("depends_on"):
                print(f"  Depends on: {', '.join(result['depends_on'])}")
            if result.get("blocked_by"):
                print(f"  Blocked by: {result['blocked_by']}")
            if result.get("summary"):
                print(f"  Summary: {result['summary']}")
            p = result.get("priority", 0)
            if p:
                print(f"  Priority: {p}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# get-child-tasks
# ------------------------------------------------------------------ #

def cmd_get_child_tasks(args):
    engine = _replica_engine_from_args(args)
    try:
        result = engine.get_child_tasks(args.parent_agent_id)
        tasks = result.get("tasks", [])
        if args.json_output:
            _print_json(result)
        elif not tasks:
            print(f"No tasks from {args.parent_agent_id}")
        else:
            print(f"{len(tasks)} task(s) from {args.parent_agent_id}:")
            for t in tasks:
                assigned = f" → {t['assigned_agent_id']}" if t.get("assigned_agent_id") else ""
                p = t.get("priority", 0)
                prio = f" @{p}" if p else ""
                print(f"  [{t['status']}] {t['id']}{assigned}{prio} — {t['description'][:50]}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# get-tasks-by-agent
# ------------------------------------------------------------------ #

def cmd_get_tasks_by_agent(args):
    engine = _replica_engine_from_args(args)
    try:
        result = engine.get_tasks_by_agent(args.assigned_agent_id)
        tasks = result.get("tasks", [])
        if args.json_output:
            _print_json(result)
        elif not tasks:
            print(f"No tasks for {args.assigned_agent_id}")
        else:
            print(f"{len(tasks)} task(s) for {args.assigned_agent_id}:")
            for t in tasks:
                assigned = f" → {t['assigned_agent_id']}" if t.get("assigned_agent_id") else ""
                p = t.get("priority", 0)
                prio = f" @{p}" if p else ""
                print(f"  [{t['status']}] {t['id']}{assigned}{prio} — {t['description'][:50]}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# get-all-tasks
# ------------------------------------------------------------------ #

def cmd_get_all_tasks(args):
    engine = _replica_engine_from_args(args)
    try:
        result = engine.get_all_tasks()
        tasks = result.get("tasks", [])
        if args.json_output:
            _print_json(result)
        elif not tasks:
            print("Task registry is empty")
        else:
            print(f"{len(tasks)} task(s) in registry:")
            for t in tasks:
                assigned = f" → {t['assigned_agent_id']}" if t.get("assigned_agent_id") else ""
                p = t.get("priority", 0)
                prio = f" @{p}" if p else ""
                print(f"  [{t['status']}] {t['id']}{assigned}{prio} — {t['description'][:50]}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# create-subtask
# ------------------------------------------------------------------ #

def cmd_create_subtask(args):
    engine = _engine_from_args(args)
    try:
        result = engine.create_subtask(
            task_id=args.task_id,
            parent_task_id=args.parent_task_id,
            parent_agent_id=args.parent_agent_id,
            description=args.description,
            depends_on=getattr(args, "depends_on", None),
            priority=getattr(args, "priority", 0),
        )
        if args.json_output:
            _print_json(result)
        else:
            print(f"Subtask created: {args.task_id} under {args.parent_task_id}")
            p = getattr(args, "priority", 0)
            if p:
                print(f"  Priority: {p}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# get-subtasks
# ------------------------------------------------------------------ #

def cmd_get_subtasks(args):
    engine = _replica_engine_from_args(args)
    try:
        result = engine.get_subtasks(args.parent_task_id)
        subtasks = result.get("subtasks", [])
        if args.json_output:
            _print_json(result)
        elif not subtasks:
            print(f"No subtasks for {args.parent_task_id}")
        else:
            print(f"{len(subtasks)} subtask(s) under {args.parent_task_id}:")
            for t in subtasks:
                assigned = f" → {t['assigned_agent_id']}" if t.get("assigned_agent_id") else ""
                print(f"  [{t['status']}] {t['id']}{assigned} — {t['description'][:50]}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# get-task-tree
# ------------------------------------------------------------------ #

def cmd_get_task_tree(args):
    engine = _replica_engine_from_args(args)
    try:
        result = engine.get_task_tree(args.root_task_id)
        if args.json_output:
            _print_json(result)
        elif result.get("error"):
            print(f"Error: {result['error']}")
        else:
            def _print_tree(task, indent=0):
                prefix = "  " * indent
                status = task.get("status", "?")
                p = task.get("priority", 0)
                prio = f" @{p}" if p else ""
                print(f"{prefix}[{status}] {task['id']}{prio} — {task.get('description', '')[:50]}")
                for child in task.get("subtasks", []):
                    _print_tree(child, indent + 1)
            _print_tree(result)
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# retry-task
# ------------------------------------------------------------------ #

def cmd_retry_task(args):
    engine = _engine_from_args(args)
    try:
        result = engine.retry_task(args.task_id)
        if args.json_output:
            _print_json(result)
        elif result.get("retried"):
            print(f"Task retried: {args.task_id}")
        else:
            print(f"Could not retry {args.task_id}: {result.get('reason', 'unknown error')}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# dead-letter-queue
# ------------------------------------------------------------------ #

def cmd_dead_letter_queue(args):
    engine = _engine_from_args(args)
    try:
        result = engine.get_dead_letter_tasks(limit=args.limit)
        tasks = result.get("dead_letter_tasks", [])
        if args.json_output:
            _print_json(result)
        elif not tasks:
            print("Dead letter queue is empty")
        else:
            print(f"{len(tasks)} task(s) in dead letter queue:")
            for t in tasks:
                print(f"  [{t['status']}] {t['task_id']} — {t.get('error', 'no error')[:50]}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# task-failure-history
# ------------------------------------------------------------------ #

def cmd_task_failure_history(args):
    engine = _engine_from_args(args)
    try:
        result = engine.get_task_failure_history(args.task_id)
        if args.json_output:
            _print_json(result)
        elif not result.get("history"):
            print(f"No failure history for {args.task_id}")
        else:
            print(f"Failure history for {args.task_id}:")
            for h in result["history"]:
                status = h.get("status", "?")
                error = h.get("error", "no error")[:50]
                print(f"  [attempt {h['attempt']}] [{status}] {error}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# wait-for-task
# ------------------------------------------------------------------ #

def cmd_wait_for_task(args):
    engine = _engine_from_args(args)
    try:
        result = engine.wait_for_task(
            task_id=args.task_id,
            timeout_s=getattr(args, "timeout", 60.0),
            poll_interval_s=getattr(args, "poll_interval", 2.0),
        )
        if args.json_output:
            _print_json(result)
        elif result.get("timed_out"):
            print(f"Timed out waiting for {args.task_id} (status: {result.get('status', 'unknown')})")
        else:
            print(f"Task {args.task_id} reached terminal state: {result.get('status')}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# get-available-tasks
# ------------------------------------------------------------------ #

def cmd_get_available_tasks(args):
    engine = _replica_engine_from_args(args)
    try:
        result = engine.get_available_tasks(
            agent_id=getattr(args, "agent_id", None),
        )
        tasks = result.get("tasks", [])
        if args.json_output:
            _print_json(result)
        elif not tasks:
            print("No available tasks (all pending tasks have incomplete dependencies)")
        else:
            print(f"{len(tasks)} available task(s):")
            for t in tasks:
                assigned = f" → {t['assigned_agent_id']}" if t.get("assigned_agent_id") else " (unassigned)"
                p = t.get("priority", 0)
                prio = f" @{p}" if p else ""
                deps = t.get("depends_on", [])
                deps_str = f" (deps: {', '.join(deps)})" if deps else ""
                print(f"  [{t['status']}] {t['id']}{assigned}{prio} — {t['description'][:50]}{deps_str}")
    finally:
        _close(engine)
