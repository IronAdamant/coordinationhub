"""Change awareness, audit, graph, and assessment CLI commands."""

from __future__ import annotations

from pathlib import Path

from .cli_utils import print_json as _print_json, command as _command


# ------------------------------------------------------------------ #
# notify-change
# ------------------------------------------------------------------ #

@_command()
def cmd_notify_change(engine, args):
    result = engine.notify_change(args.document_path, args.change_type, args.agent_id)
    if args.json_output:
        _print_json(result)
    else:
        print(f"Recorded: {args.change_type} on {args.document_path} by {args.agent_id}")


# ------------------------------------------------------------------ #
# get-notifications
# ------------------------------------------------------------------ #

@_command(replica=True)
def cmd_get_notifications(engine, args):
    result = engine.get_notifications(since=args.since, exclude_agent=args.exclude_agent, limit=args.limit)
    if args.json_output:
        _print_json(result)
    else:
        notifs = result.get("notifications", [])
        if not notifs:
            print("No notifications")
        else:
            print(f"{len(notifs)} notification(s):")
            for n in notifs:
                print(f"  {n['document_path']}: {n['change_type']} by {n['agent_id']}")


# ------------------------------------------------------------------ #
# prune-notifications
# ------------------------------------------------------------------ #

@_command()
def cmd_prune_notifications(engine, args):
    result = engine.prune_notifications(max_age_seconds=args.max_age_seconds, max_entries=args.max_entries)
    if args.json_output:
        _print_json(result)
    else:
        print(f"Pruned {result.get('pruned', 0)} notification(s)")


# ------------------------------------------------------------------ #
# wait-for-notifications
# ------------------------------------------------------------------ #

@_command()
def cmd_wait_for_notifications(engine, args):
    result = engine.wait_for_notifications(
        agent_id=args.agent_id,
        timeout_s=getattr(args, "timeout", 30.0),
        poll_interval_s=getattr(args, "poll_interval", 2.0),
        exclude_agent=getattr(args, "exclude_agent", None),
    )
    if args.json_output:
        _print_json(result)
    else:
        notifs = result.get("notifications", [])
        print(f"Notifications for {args.agent_id}: {len(notifs)}")
        for n in notifs:
            print(f"  {n['document_path']}: {n['change_type']} by {n['agent_id']}")


# ------------------------------------------------------------------ #
# get-conflicts
# ------------------------------------------------------------------ #

@_command(replica=True)
def cmd_get_conflicts(engine, args):
    result = engine.get_conflicts(
        agent_id=getattr(args, "agent_id", None),
        document_path=getattr(args, "document_path", None),
        limit=getattr(args, "limit", 20),
    )
    if args.json_output:
        _print_json(result)
    else:
        conflicts = result.get("conflicts", [])
        if not conflicts:
            print("No conflicts recorded")
            return
        print(f"Conflicts ({len(conflicts)}):")
        for c in conflicts:
            print(f"  {c['document_path']}: {c['agent_id']} stole lock from {c.get('previous_holder', '?')}")


# ------------------------------------------------------------------ #
# contention-hotspots
# ------------------------------------------------------------------ #

@_command(replica=True)
def cmd_contention_hotspots(engine, args):
    result = engine.get_contention_hotspots(limit=getattr(args, "limit", 10))
    if args.json_output:
        _print_json(result)
    else:
        hotspots = result.get("hotspots", [])
        if not hotspots:
            print("No contention hotspots")
            return
        print(f"Contention hotspots ({len(hotspots)}):")
        for h in hotspots:
            print(f"  {h['document_path']}: {h['conflict_count']} conflict(s)")


# ------------------------------------------------------------------ #
# load-spec
# ------------------------------------------------------------------ #

@_command()
def cmd_load_spec(engine, args):
    result = engine.load_coordination_spec(path=getattr(args, "path", None))
    if args.json_output:
        _print_json(result)
    else:
        print(f"Loaded coordination spec")
        print(f"  Agents: {len(result.get('agents', []))}")
        print(f"  Handoffs: {len(result.get('handoffs', []))}")
        errors = result.get("errors", [])
        if errors:
            print(f"  Errors: {len(errors)}")
            for e in errors:
                print(f"    {e}")


# ------------------------------------------------------------------ #
# validate-spec
# ------------------------------------------------------------------ #

@_command()
def cmd_validate_spec(engine, args):
    result = engine.validate_graph()
    if args.json_output:
        _print_json(result)
    else:
        if result.get("valid"):
            print("Coordination spec is valid")
        else:
            print("Coordination spec is INVALID")
            for e in result.get("errors", []):
                print(f"  {e}")


# ------------------------------------------------------------------ #
# scan-project
# ------------------------------------------------------------------ #

@_command()
def cmd_scan_project(engine, args):
    result = engine.scan_project(
        extensions=getattr(args, "extensions", None),
        worktree_root=getattr(args, "worktree_root", None),
    )
    if args.json_output:
        _print_json(result)
    else:
        print(f"Scanned {result.get('files_scanned', 0)} file(s)")
        print(f"  Owned files: {result.get('owned_files', 0)}")


# ------------------------------------------------------------------ #
# dashboard
# ------------------------------------------------------------------ #

@_command(replica=True)
def cmd_dashboard(engine, args):
    # Auto-reap stale agents for consistent display (Review Fourteen)
    engine.reap_stale_agents(timeout=getattr(args, "stale_timeout", 600.0))
    result = engine.status()
    agents = result.get("agents", [])
    locks = result.get("locks", [])
    tasks = result.get("tasks", [])
    if args.json_output:
        _print_json(result)
        return
    print("═" * 60)
    print("CoordinationHub Dashboard")
    print("═" * 60)
    print(f"\nAgents ({len(agents)}):")
    for a in agents:
        stale = " (STALE)" if a.get("stale") else ""
        print(f"  {a['agent_id']}: {a['status']}{stale}")
    print(f"\nLocks ({len(locks)}):")
    if not locks:
        print("  (none)")
    else:
        for lk in locks:
            print(f"  {lk['document_path']} — {lk['locked_by']} ({lk['lock_type']})")
    print(f"\nTasks ({len(tasks)}):")
    if not tasks:
        print("  (none)")
    else:
        for t in tasks:
            print(f"  [{t.get('status', '?')}] {t.get('task_id', '?')}")
    print("═" * 60)


# ------------------------------------------------------------------ #
# agent-status
# ------------------------------------------------------------------ #

@_command(replica=True)
def cmd_agent_status(engine, args):
    result = engine.get_agent_status(agent_id=args.agent_id)
    if args.json_output:
        _print_json(result)
    else:
        print(f"Agent: {result.get('agent_id', args.agent_id)}")
        print(f"  Status: {result.get('status', 'unknown')}")
        print(f"  Task: {result.get('current_task', '(none)')}")
        locks = result.get("locks", [])
        print(f"  Locks: {len(locks)}")
        for lk in locks:
            print(f"    {lk['document_path']} ({lk['lock_type']})")


# ------------------------------------------------------------------ #
# assess
# ------------------------------------------------------------------ #

@_command(replica=True)
def cmd_assess(engine, args):
    result = engine.run_assessment(
        suite_path=getattr(args, "suite_path", None),
        format=getattr(args, "format", "markdown"),
        graph_agent_id=getattr(args, "graph_agent_id", None),
        scope=getattr(args, "scope", "project"),
    )
    if args.json_output:
        _print_json(result)
    else:
        report = result.get("report", "")
        print(report)
    output_path = getattr(args, "output_path", None)
    if output_path:
        Path(output_path).write_text(result.get("report", ""), encoding="utf-8")
        print(f"Report written to {output_path}")


# ------------------------------------------------------------------ #
# agent-tree
# ------------------------------------------------------------------ #

@_command(replica=True)
def cmd_agent_tree(engine, args):
    result = engine.get_agent_tree(agent_id=getattr(args, "agent_id", None))
    if args.json_output:
        _print_json(result)
    else:
        tree = result.get("tree", {})
        if not tree:
            print("No agent tree available")
            return
        _render_agent_tree(tree)


def _render_agent_tree(node, indent=0):
    prefix = "  " * indent
    agent_id = node.get("agent_id", "?")
    status = node.get("status", "?")
    task = node.get("current_task", "")
    print(f"{prefix}{agent_id} [{status}]{f' — {task}' if task else ''}")
    for child in node.get("children", []):
        _render_agent_tree(child, indent + 1)
