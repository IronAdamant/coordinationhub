"""CLI commands for the task registry."""

from __future__ import annotations

from .cli_utils import print_json as _print_json, engine_from_args as _engine_from_args, close as _close


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
        )
        if args.json_output:
            _print_json(result)
        else:
            print(f"Task created: {args.task_id}")
            if getattr(args, "depends_on", None):
                print(f"  Depends on: {', '.join(args.depends_on)}")
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
    engine = _engine_from_args(args)
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
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# get-child-tasks
# ------------------------------------------------------------------ #

def cmd_get_child_tasks(args):
    engine = _engine_from_args(args)
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
                print(f"  [{t['status']}] {t['id']}{assigned} — {t['description'][:50]}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# get-tasks-by-agent
# ------------------------------------------------------------------ #

def cmd_get_tasks_by_agent(args):
    engine = _engine_from_args(args)
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
                print(f"  [{t['status']}] {t['id']} — {t['description'][:50]}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# get-all-tasks
# ------------------------------------------------------------------ #

def cmd_get_all_tasks(args):
    engine = _engine_from_args(args)
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
                print(f"  [{t['status']}] {t['id']}{assigned} — {t['description'][:50]}")
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
        )
        if args.json_output:
            _print_json(result)
        else:
            print(f"Subtask created: {args.task_id} under {args.parent_task_id}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# get-subtasks
# ------------------------------------------------------------------ #

def cmd_get_subtasks(args):
    engine = _engine_from_args(args)
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
    engine = _engine_from_args(args)
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
                print(f"{prefix}[{status}] {task['id']} — {task.get('description', '')[:50]}")
                for child in task.get("subtasks", []):
                    _print_tree(child, indent + 1)
            _print_tree(result)
    finally:
        _close(engine)
