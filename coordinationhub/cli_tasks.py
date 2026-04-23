"""CLI commands for the task registry."""

from __future__ import annotations

from .cli_utils import print_json as _print_json, command as _command


# ------------------------------------------------------------------ #
# create-task
# ------------------------------------------------------------------ #

@_command()
def cmd_create_task(engine, args):
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


# ------------------------------------------------------------------ #
# assign-task
# ------------------------------------------------------------------ #

@_command()
def cmd_assign_task(engine, args):
    result = engine.assign_task(args.task_id, args.assigned_agent_id)
    if args.json_output:
        _print_json(result)
    else:
        print(f"Task assigned: {args.task_id} → {args.assigned_agent_id}")


# ------------------------------------------------------------------ #
# update-task-status
# ------------------------------------------------------------------ #

@_command()
def cmd_update_task_status(engine, args):
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
        # T3.22: guard against args.summary being None (argparse sets the
        # attr to None when the flag is unpassed; ``len(None)`` raised).
        summary = getattr(args, "summary", None) or ""
        if summary:
            print(f"  Summary: {summary[:80]}{'...' if len(summary) > 80 else ''}")


# ------------------------------------------------------------------ #
# query-tasks
# ------------------------------------------------------------------ #

@_command(replica=True)
def cmd_query_tasks(engine, args):
    result = engine.query_tasks(
        query_type=args.query_type,
        task_id=getattr(args, "task_id", None),
        parent_agent_id=getattr(args, "parent_agent_id", None),
        assigned_agent_id=getattr(args, "assigned_agent_id", None),
        parent_task_id=getattr(args, "parent_task_id", None),
        root_task_id=getattr(args, "root_task_id", None),
    )
    if args.json_output:
        _print_json(result)
    else:
        tasks = result.get("tasks", [])
        if not tasks:
            print("No tasks found")
            return
        print(f"Tasks ({len(tasks)}):")
        for t in tasks:
            status = t.get("status", "unknown")
            assigned = t.get("assigned_agent_id") or "(unassigned)"
            print(f"  [{status}] {t['task_id']} — {assigned}")
            if t.get("description"):
                print(f"    {t['description'][:60]}")


# ------------------------------------------------------------------ #
# create-subtask
# ------------------------------------------------------------------ #

@_command()
def cmd_create_subtask(engine, args):
    result = engine.create_subtask(
        parent_task_id=args.parent_task_id,
        task_id=args.task_id,
        parent_agent_id=args.parent_agent_id,
        description=args.description,
        depends_on=getattr(args, "depends_on", None),
        priority=getattr(args, "priority", 0),
    )
    if args.json_output:
        _print_json(result)
    else:
        print(f"Subtask created: {args.task_id}")
        print(f"  Parent task: {args.parent_task_id}")


# ------------------------------------------------------------------ #
# retry-task
# ------------------------------------------------------------------ #

@_command()
def cmd_retry_task(engine, args):
    result = engine.task_failures(action="retry", task_id=args.task_id)
    if args.json_output:
        _print_json(result)
    else:
        if result.get("resurrected"):
            print(f"Task resurrected: {args.task_id}")
        else:
            print(f"Retry failed: {result.get('reason', 'unknown')}")


# ------------------------------------------------------------------ #
# dead-letter-queue
# ------------------------------------------------------------------ #

@_command(replica=True)
def cmd_dead_letter_queue(engine, args):
    result = engine.task_failures(action="list_dead_letter", limit=getattr(args, "limit", 50))
    if args.json_output:
        _print_json(result)
    else:
        tasks = result.get("tasks", [])
        if not tasks:
            print("Dead letter queue is empty")
            return
        print(f"Dead letter queue ({len(tasks)}):")
        for t in tasks:
            print(f"  {t['task_id']} — failed at {t.get('failed_at', '?')}")
            if t.get("error"):
                print(f"    Error: {t['error'][:80]}")


# ------------------------------------------------------------------ #
# task-failure-history
# ------------------------------------------------------------------ #

@_command(replica=True)
def cmd_task_failure_history(engine, args):
    result = engine.task_failures(action="history", task_id=args.task_id)
    if args.json_output:
        _print_json(result)
    else:
        history = result.get("history", [])
        if not history:
            print(f"No failure history for {args.task_id}")
            return
        print(f"Failure history for {args.task_id} ({len(history)} entries):")
        for h in history:
            print(f"  {h.get('timestamp', '?')}: {h.get('error', 'unknown')[:80]}")


# ------------------------------------------------------------------ #
# wait-for-task
# ------------------------------------------------------------------ #

@_command()
def cmd_wait_for_task(engine, args):
    result = engine.wait_for_task(
        task_id=args.task_id,
        timeout_s=getattr(args, "timeout", 60.0),
        poll_interval_s=getattr(args, "poll_interval", 2.0),
    )
    if args.json_output:
        _print_json(result)
    else:
        if result.get("completed"):
            print(f"Task completed: {args.task_id}")
        elif result.get("failed"):
            print(f"Task failed: {args.task_id}")
            if result.get("error"):
                print(f"  Error: {result['error']}")
        else:
            print(f"Timeout waiting for: {args.task_id}")


# ------------------------------------------------------------------ #
# get-available-tasks
# ------------------------------------------------------------------ #

@_command(replica=True)
def cmd_get_available_tasks(engine, args):
    result = engine.get_available_tasks(agent_id=getattr(args, "agent_id", None))
    if args.json_output:
        _print_json(result)
    else:
        tasks = result.get("tasks", [])
        if not tasks:
            print("No available tasks")
            return
        print(f"Available tasks ({len(tasks)}):")
        for t in tasks:
            print(f"  {t['task_id']} — {t.get('description', '')[:60]}")
