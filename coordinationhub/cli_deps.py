"""CLI commands for cross-agent dependency declarations."""

from __future__ import annotations

from .cli_utils import print_json as _print_json, command as _command


# ------------------------------------------------------------------ #
# declare-dependency
# ------------------------------------------------------------------ #

@_command()
def cmd_declare_dependency(engine, args):
    result = engine.declare_dependency(
        dependent_agent_id=args.dependent_agent_id,
        depends_on_agent_id=args.depends_on_agent_id,
        depends_on_task_id=getattr(args, "depends_on_task_id", None),
        condition=getattr(args, "condition", "task_completed"),
    )
    if args.json_output:
        _print_json(result)
    else:
        print(f"Dependency declared: {args.dependent_agent_id} → {args.depends_on_agent_id}")
        if getattr(args, "depends_on_task_id", None):
            print(f"  Task: {args.depends_on_task_id}")
        print(f"  Condition: {getattr(args, 'condition', 'task_completed')}")


# ------------------------------------------------------------------ #
# manage-dependencies
# ------------------------------------------------------------------ #

@_command()
def cmd_manage_dependencies(engine, args):
    result = engine.manage_dependencies(mode=args.mode, agent_id=args.agent_id)
    if args.json_output:
        _print_json(result)
    else:
        if args.mode == "assert":
            if result.get("can_start"):
                print(f"{args.agent_id} can start: no blockers")
            else:
                blockers = result.get("blockers", [])
                print(f"{args.agent_id} CANNOT start — {len(blockers)} blocker(s):")
                for b in blockers:
                    print(f"  → {b['depends_on_agent_id']} ({b['condition']})")
        else:
            blocked = result.get("blocked")
            print(f"Dependencies for {args.agent_id}:")
            print(f"  Blocked: {blocked}")
            unsatisfied = result.get("unsatisfied", [])
            if unsatisfied:
                for d in unsatisfied:
                    print(f"  → {d['depends_on_agent_id']} ({d['condition']})")
                    if d.get("depends_on_task_id"):
                        print(f"    Task: {d['depends_on_task_id']}")
            else:
                print("  (none)")


# ------------------------------------------------------------------ #
# satisfy-dependency
# ------------------------------------------------------------------ #

@_command()
def cmd_satisfy_dependency(engine, args):
    result = engine.satisfy_dependency(args.dep_id)
    if args.json_output:
        _print_json(result)
    else:
        print(f"Dependency {args.dep_id} marked as satisfied")


# ------------------------------------------------------------------ #
# get-all-dependencies
# ------------------------------------------------------------------ #

@_command(replica=True)
def cmd_get_all_dependencies(engine, args):
    result = engine.get_all_dependencies(getattr(args, "dependent_agent_id", None))
    deps = result.get("dependencies", [])
    if args.json_output:
        _print_json(result)
    elif not deps:
        print("No dependencies declared")
    else:
        print(f"{len(deps)} declared dependency/dependencies:")
        # T7.5: ASCII tags instead of check/cross glyphs; cp1252 stdout
        # (default on Windows before PowerShell 7.2) dies on ✓/✗ and
        # the arrow ``→``.
        for d in deps:
            sat = "OK" if d.get("satisfied") else "PENDING"
            print(f"  [{sat}] {d['dependent_agent_id']} -> {d['depends_on_agent_id']} ({d['condition']})")
