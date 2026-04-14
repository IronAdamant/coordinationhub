"""CLI commands for HA coordinator lease management."""

from __future__ import annotations

from .cli_utils import print_json as _print_json, engine_from_args as _engine_from_args
from .cli_utils import replica_engine_from_args as _replica_engine_from_args, close as _close


# ------------------------------------------------------------------ #
# acquire-coordinator-lease
# ------------------------------------------------------------------ #

def cmd_acquire_coordinator_lease(args):
    engine = _engine_from_args(args)
    try:
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
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# refresh-coordinator-lease
# ------------------------------------------------------------------ #

def cmd_refresh_coordinator_lease(args):
    engine = _engine_from_args(args)
    try:
        result = engine.refresh_coordinator_lease(agent_id=args.agent_id)
        if args.json_output:
            _print_json(result)
        elif result.get("refreshed"):
            print(f"Lease refreshed: {result['lease_name']}")
            print(f"  New expiry: {result['expires_at']}")
        else:
            print(f"Refresh failed: {result.get('error', 'unknown error')}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# release-coordinator-lease
# ------------------------------------------------------------------ #

def cmd_release_coordinator_lease(args):
    engine = _engine_from_args(args)
    try:
        result = engine.release_coordinator_lease(agent_id=args.agent_id)
        if args.json_output:
            _print_json(result)
        elif result.get("released"):
            print(f"Lease released: {result['lease_name']}")
        else:
            print(f"Release failed: {result.get('error', 'unknown error')}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# get-leader
# ------------------------------------------------------------------ #

def cmd_get_leader(args):
    engine = _replica_engine_from_args(args)
    try:
        result = engine.get_leader()
        if args.json_output:
            _print_json(result if result else {"leader": None})
        elif result is None:
            print("No active leader (lease unheld)")
        else:
            print(f"Leader: {result['holder_id']}")
            print(f"  Lease: {result['lease_name']}")
            print(f"  Acquired at: {result['acquired_at']}")
            print(f"  TTL: {result['ttl']}s")
            print(f"  Expires at: {result['expires_at']}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# leader-status
# ------------------------------------------------------------------ #

def cmd_leader_status(args):
    engine = _replica_engine_from_args(args)
    try:
        result = engine.get_leader()
        if args.json_output:
            _print_json(result if result else {"leader": None})
            return
        if result is None:
            print("No active leader (lease unheld)")
            return
        print(f"Leader: {result['holder_id']}")
        print(f"  Lease: {result['lease_name']}")
        print(f"  Acquired at: {result['acquired_at']}")
        print(f"  TTL: {result['ttl']}s")
        print(f"  Expires at: {result['expires_at']}")

        # Show replica count
        agents_result = engine.list_agents(active_only=False)
        agents = agents_result.get("agents", [])
        print(f"  Registered agents: {len(agents)}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# ha-dashboard
# ------------------------------------------------------------------ #

def cmd_ha_dashboard(args):
    engine = _replica_engine_from_args(args)
    try:
        leader = engine.get_leader()
        agents_result = engine.list_agents(active_only=False)
        agents = agents_result.get("agents", [])

        import time as _time

        if args.json_output:
            _print_json({
                "leader": leader,
                "agent_count": len(agents),
                "agents": [
                    {"agent_id": a["agent_id"], "status": a["status"],
                     "last_seen": a.get("last_seen")}
                    for a in agents
                ],
            })
            return

        print("=" * 60)
        print("HA COORDINATOR DASHBOARD")
        print("=" * 60)

        if leader:
            print(f"\nLeader: {leader['holder_id']}")
            print(f"  Lease: {leader['lease_name']}")
            print(f"  Acquired at: {leader['acquired_at']}")
            print(f"  TTL: {leader['ttl']}s")
            print(f"  Expires at: {leader['expires_at']}")

            # Detect if leader is stale (lease expired)
            now = _time.time()
            if leader["expires_at"] < now:
                print("  [EXPIRED]")
            else:
                print("  [ACTIVE]")
        else:
            print("\nNo active leader (lease unheld)")

        print(f"\nRegistered agents: {len(agents)}")
        for a in agents:
            stale = " [STALE]" if a.get("stale") else ""
            print(f"  {a['agent_id']} [{a['status']}]{stale}")

        print("=" * 60)
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# claim-leadership
# ------------------------------------------------------------------ #

def cmd_claim_leadership(args):
    engine = _engine_from_args(args)
    try:
        result = engine.claim_leadership(
            agent_id=args.agent_id,
            ttl=getattr(args, "ttl", None),
        )
        if args.json_output:
            _print_json(result)
        elif result.get("claimed"):
            print(f"Leadership claimed: {result['lease_name']}")
            print(f"  New leader: {result['holder_id']}")
            print(f"  TTL: {result['ttl']}s")
            print(f"  Expires at: {result['expires_at']}")
        else:
            print(f"Claim failed: {result.get('error', 'unknown error')}")
            holder = result.get("holder")
            if holder:
                print(f"  Current holder: {holder['holder_id']}")
                print(f"  Expires at: {holder['expires_at']}")
    finally:
        _close(engine)
