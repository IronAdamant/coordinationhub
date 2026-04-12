"""CLI commands for cross-agent dependency declarations."""

from __future__ import annotations

from .cli_utils import print_json as _print_json, engine_from_args as _engine_from_args, close as _close


# ------------------------------------------------------------------ #
# declare-dependency
# ------------------------------------------------------------------ #

def cmd_declare_dependency(args):
    engine = _engine_from_args(args)
    try:
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
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# check-dependencies
# ------------------------------------------------------------------ #

def cmd_check_dependencies(args):
    engine = _engine_from_args(args)
    try:
        result = engine.check_dependencies(args.agent_id)
        if args.json_output:
            _print_json(result)
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
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# satisfy-dependency
# ------------------------------------------------------------------ #

def cmd_satisfy_dependency(args):
    engine = _engine_from_args(args)
    try:
        result = engine.satisfy_dependency(args.dep_id)
        if args.json_output:
            _print_json(result)
        else:
            print(f"Dependency {args.dep_id} marked as satisfied")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# get-blockers
# ------------------------------------------------------------------ #

def cmd_get_blockers(args):
    engine = _engine_from_args(args)
    try:
        result = engine.get_blockers(args.agent_id)
        blockers = result.get("unsatisfied", [])
        if args.json_output:
            _print_json(result)
        elif not blockers:
            print(f"No blockers for {args.agent_id}")
        else:
            print(f"{len(blockers)} blocker(s) for {args.agent_id}:")
            for b in blockers:
                print(f"  → {b['depends_on_agent_id']} ({b['condition']})")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# assert-can-start
# ------------------------------------------------------------------ #

def cmd_assert_can_start(args):
    engine = _engine_from_args(args)
    try:
        result = engine.assert_can_start(args.agent_id)
        if args.json_output:
            _print_json(result)
        else:
            if result.get("can_start"):
                print(f"{args.agent_id} can start: no blockers")
            else:
                blockers = result.get("blockers", [])
                print(f"{args.agent_id} CANNOT start — {len(blockers)} blocker(s):")
                for b in blockers:
                    print(f"  → {b['depends_on_agent_id']} ({b['condition']})")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# get-all-dependencies
# ------------------------------------------------------------------ #

def cmd_get_all_dependencies(args):
    engine = _engine_from_args(args)
    try:
        result = engine.get_all_dependencies(getattr(args, "dependent_agent_id", None))
        deps = result.get("dependencies", [])
        if args.json_output:
            _print_json(result)
        elif not deps:
            print("No dependencies declared")
        else:
            print(f"{len(deps)} declared dependency/dependencies:")
            for d in deps:
                sat = "✓" if d.get("satisfied") else "✗"
                print(f"  [{sat}] {d['dependent_agent_id']} → {d['depends_on_agent_id']} ({d['condition']})")
    finally:
        _close(engine)
