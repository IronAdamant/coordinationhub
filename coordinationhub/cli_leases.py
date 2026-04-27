"""CLI commands for HA coordinator lease management."""

from __future__ import annotations

from .cli_utils import print_json as _print_json, command as _command


# ------------------------------------------------------------------ #
# acquire-coordinator-lease
# ------------------------------------------------------------------ #

@_command()
def cmd_acquire_coordinator_lease(engine, args):
    result = engine.acquire_coordinator_lease(
        agent_id=args.agent_id,
        ttl=getattr(args, "ttl", None),
    )
    if args.json_output:
        _print_json(result)
    elif result.get("acquired"):
        print(f"Lease acquired: {result['lease_name']}")
        print(f"  Holder: {result['holder_id']}")
        print(f"  TTL: {result['ttl']}s")
        print(f"  Expires at: {result['expires_at']}")
    else:
        print("Lease acquisition failed — leadership held by another agent")
        holder = result.get("holder")
        if holder:
            print(f"  Current holder: {holder['holder_id']}")
            print(f"  Expires at: {holder['expires_at']}")


# ------------------------------------------------------------------ #
# refresh-coordinator-lease
# ------------------------------------------------------------------ #

@_command()
def cmd_refresh_coordinator_lease(engine, args):
    result = engine.refresh_coordinator_lease(agent_id=args.agent_id)
    if args.json_output:
        _print_json(result)
        # T3.16 tail: propagate exit codes even with --json so scripts
        # can branch on rc directly. "Not the current lease holder" is
        # a denial (4), not a not-found.
        if not result.get("refreshed"):
            return 4
        return 0
    if result.get("refreshed"):
        print(f"Lease refreshed: {result['lease_name']}")
        print(f"  New expiry: {result['expires_at']}")
        return 0
    # T3.16 tail: caller is not the lease holder → denied (exit 4),
    # message on stderr. Mirrors cmd_acquire_lock's denial path.
    import sys as _sys
    print(
        f"Refresh failed: {result.get('error', 'unknown error')}",
        file=_sys.stderr,
    )
    return 4


# ------------------------------------------------------------------ #
# release-coordinator-lease
# ------------------------------------------------------------------ #

@_command()
def cmd_release_coordinator_lease(engine, args):
    result = engine.release_coordinator_lease(agent_id=args.agent_id)
    if args.json_output:
        _print_json(result)
    elif result.get("released"):
        print(f"Lease released: {result['lease_name']}")
    else:
        print(f"Release failed: {result.get('error', 'unknown error')}")


# ------------------------------------------------------------------ #
# get-leader
# ------------------------------------------------------------------ #

@_command(replica=True)
def cmd_get_leader(engine, args):
    result = engine.manage_leases(action="get")
    if args.json_output:
        _print_json(result)
    else:
        leader = result.get("leader")
        if leader:
            print(f"Current leader: {leader['agent_id']}")
            print(f"  Lease: {leader['lease_name']}")
            print(f"  Expires: {leader['expires_at']}")
        else:
            print("No active coordinator leader")


# ------------------------------------------------------------------ #
# claim-leadership
# ------------------------------------------------------------------ #

@_command()
def cmd_claim_leadership(engine, args):
    result = engine.manage_leases(
        action="claim",
        agent_id=args.agent_id,
        ttl=getattr(args, "ttl", None),
    )
    if args.json_output:
        _print_json(result)
    elif result.get("acquired"):
        print(f"Leadership claimed: {result['lease_name']}")
        print(f"  Holder: {result['holder_id']}")
    else:
        print("Leadership claim failed")
        holder = result.get("holder")
        if holder:
            print(f"  Current holder: {holder['holder_id']}")


# ------------------------------------------------------------------ #
# leader-status
# ------------------------------------------------------------------ #

@_command(replica=True)
def cmd_leader_status(engine, args):
    result = engine.manage_leases(action="get")
    if args.json_output:
        _print_json(result)
    else:
        leader = result.get("leader")
        if leader:
            print(f"Leader: {leader['agent_id']} (expires {leader['expires_at']})")
        else:
            print("No active leader")


# ------------------------------------------------------------------ #
# ha-dashboard
# ------------------------------------------------------------------ #

@_command(replica=True)
def cmd_ha_dashboard(engine, args):
    result = engine.manage_leases(action="get")
    leader = result.get("leader")
    if args.json_output:
        _print_json(result)
    elif leader is None:
        print("No active coordinator lease")
    else:
        print("Coordinator lease:")
        print(f"  Lease: {leader.get('lease_name', '?')}")
        print(f"  Holder: {leader.get('holder_id', '?')}")
        print(f"  Acquired at: {leader.get('acquired_at', '?')}")
        print(f"  Expires at: {leader.get('expires_at', '?')}")
