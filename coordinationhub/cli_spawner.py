"""CLI commands for HA coordinator spawner — sub-agent registry management."""

from __future__ import annotations

from . import spawner as _spawner
from .cli_utils import print_json as _print_json, command as _command


# ------------------------------------------------------------------ #
# spawn-subagent
# ------------------------------------------------------------------ #

@_command()
def cmd_spawn_subagent(engine, args):
    result = engine.spawn_subagent(
        parent_agent_id=args.parent_agent_id,
        subagent_type=args.subagent_type,
        description=getattr(args, "description", None),
        prompt=getattr(args, "prompt", None),
        source=getattr(args, "source", "external"),
    )
    if args.json_output:
        _print_json(result)
    else:
        print(f"Spawn registered: {result['spawn_id']}")
        print(f"  Parent: {result['parent_agent_id']}")
        status = result.get("status", "pending")
        print(f"  Status: {status}")


# ------------------------------------------------------------------ #
# report-subagent-spawned
# ------------------------------------------------------------------ #

@_command()
def cmd_report_subagent_spawned(engine, args):
    result = engine.report_subagent_spawned(
        parent_agent_id=args.parent_agent_id,
        subagent_type=getattr(args, "subagent_type", None),
        child_agent_id=args.child_agent_id,
        source=getattr(args, "source", "external"),
    )
    if args.json_output:
        _print_json(result)
    else:
        if result.get("spawn_id"):
            print(f"Spawn reported: {result['spawn_id']}")
            print(f"  Child: {result['child_agent_id']}")
            print(f"  Description: {result.get('description', '')}")
        else:
            print(f"No pending spawn found for {args.parent_agent_id}")
            print(f"  Child: {result['child_agent_id']}")


# ------------------------------------------------------------------ #
# list-pending-spawns
# ------------------------------------------------------------------ #

@_command()
def cmd_list_pending_spawns(engine, args):
    result = engine.get_pending_spawns(
        parent_agent_id=args.parent_agent_id,
        include_consumed=getattr(args, "include_consumed", False),
    )
    if args.json_output:
        _print_json({"spawns": result})
        return

    if not result:
        print("No pending spawns.")
        return

    import time as _time

    print(f"Pending spawns for {args.parent_agent_id}:")
    for s in result:
        # T7.9: explicit None check. ``s.get('created_at', 0)`` returned
        # 0 on a missing/corrupt column and then ``_time.time() - 0``
        # printed an age of ~1.8e9 seconds, which was noise. A missing
        # timestamp now renders as ``age=unknown``.
        created_at = s.get("created_at")
        if created_at is None:
            age_str = "unknown"
        else:
            age_str = f"{_time.time() - created_at:.1f}s"
        status = s.get("status", "?")
        marker = " [EXPIRED]" if status == "expired" else (" [REGISTERED]" if status == "registered" else "")
        print(f"  {s['id']}{marker}")
        print(f"    type={s.get('subagent_type', '?')} | age={age_str} | desc={s.get('description', '')[:40]}")


# ------------------------------------------------------------------ #
# cancel-spawn
# ------------------------------------------------------------------ #

@_command()
def cmd_cancel_spawn(engine, args):
    # T3.19: route through the engine method instead of reaching into
    # engine._connect + spawner primitives directly.
    result = engine.cancel_spawn(args.spawn_id)
    if args.json_output:
        _print_json(result)
    elif result.get("cancelled"):
        print(f"Spawn cancelled: {args.spawn_id}")
    elif result.get("not_found"):
        # T3.16: not-found → exit code 3, message on stderr so scripts
        # piping stdout don't pick up the error as successful output.
        import sys as _sys
        print(
            f"Spawn not found or already consumed: {args.spawn_id}",
            file=_sys.stderr,
        )
        return 3
    else:
        import sys as _sys
        print(f"Cancel failed: {result.get('error', 'unknown')}", file=_sys.stderr)
        return 1


# ------------------------------------------------------------------ #
# request-subagent-deregistration
# ------------------------------------------------------------------ #

@_command()
def cmd_request_subagent_deregistration(engine, args):
    result = engine.request_subagent_deregistration(
        parent_agent_id=args.parent_agent_id,
        child_agent_id=args.child_agent_id,
    )
    if args.json_output:
        _print_json(result)
    elif result.get("requested"):
        print(f"Stop requested for: {args.child_agent_id}")
    elif result.get("not_found"):
        print(f"Agent not found or not active: {args.child_agent_id}")


# ------------------------------------------------------------------ #
# await-subagent-stopped
# ------------------------------------------------------------------ #

@_command()
def cmd_await_subagent_stopped(engine, args):
    result = engine.await_subagent_stopped(
        child_agent_id=args.child_agent_id,
        timeout=getattr(args, "timeout", 30.0),
    )
    if args.json_output:
        _print_json(result)
    elif result.get("stopped"):
        print(f"Agent stopped: {args.child_agent_id}")
    elif result.get("timed_out"):
        print(f"Timeout waiting for: {args.child_agent_id}")
        print("  Escalate: call deregister_agent to force cleanup")
