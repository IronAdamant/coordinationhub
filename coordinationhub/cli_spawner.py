"""CLI commands for HA coordinator spawner — sub-agent registry management."""

from __future__ import annotations

from . import spawner as _spawner
from .cli_utils import print_json as _print_json, engine_from_args as _engine_from_args, close as _close


# ------------------------------------------------------------------ #
# spawn-subagent
# ------------------------------------------------------------------ #

def cmd_spawn_subagent(args):
    engine = _engine_from_args(args)
    try:
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
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# report-subagent-spawned
# ------------------------------------------------------------------ #

def cmd_report_subagent_spawned(args):
    engine = _engine_from_args(args)
    try:
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
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# list-pending-spawns
# ------------------------------------------------------------------ #

def cmd_list_pending_spawns(args):
    engine = _engine_from_args(args)
    try:
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
            age = _time.time() - s.get("created_at", 0)
            status = s.get("status", "?")
            marker = " [EXPIRED]" if status == "expired" else (" [REGISTERED]" if status == "registered" else "")
            print(f"  {s['id']}{marker}")
            print(f"    type={s.get('subagent_type', '?')} | age={age:.1f}s | desc={s.get('description', '')[:40]}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# cancel-spawn
# ------------------------------------------------------------------ #

def cmd_cancel_spawn(args):
    engine = _engine_from_args(args)
    try:
        # Direct cancel via spawner primitives
        result = _spawner.cancel_spawn(
            connect=engine._connect,
            spawn_id=args.spawn_id,
        )
        if args.json_output:
            _print_json(result)
        elif result.get("cancelled"):
            print(f"Spawn cancelled: {args.spawn_id}")
        elif result.get("not_found"):
            print(f"Spawn not found or already consumed: {args.spawn_id}")
        else:
            print(f"Cancel failed: {result.get('error', 'unknown')}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# request-subagent-deregistration
# ------------------------------------------------------------------ #

def cmd_request_subagent_deregistration(args):
    engine = _engine_from_args(args)
    try:
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
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# await-subagent-stopped
# ------------------------------------------------------------------ #

def cmd_await_subagent_stopped(args):
    engine = _engine_from_args(args)
    try:
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
    finally:
        _close(engine)