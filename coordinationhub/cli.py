"""CoordinationHub CLI — command-line interface for all coordination tool methods."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .core import CoordinationEngine


# ------------------------------------------------------------------ #
# Parser
# ------------------------------------------------------------------ #

def create_parser() -> argparse.ArgumentParser:
    """Build the argparse parser with all subcommands and global flags."""
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument(
        "--storage-dir",
        default=None,
        help="Storage directory (default: <project_root>/.coordinationhub/)",
    )
    shared.add_argument(
        "--project-root",
        default=None,
        help="Project root directory (default: auto-detect via .git)",
    )
    shared.add_argument(
        "--namespace",
        default="hub",
        help="Agent ID namespace prefix (default: hub)",
    )
    shared.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output results as JSON",
    )

    parser = argparse.ArgumentParser(
        prog="coordinationhub",
        description="CoordinationHub — multi-agent swarm coordination",
    )

    sub = parser.add_subparsers(dest="command")

    # serve
    p_serve = sub.add_parser("serve", parents=[shared],
                             help="Start HTTP MCP server")
    p_serve.add_argument("--port", type=int, default=9877,
                         help="Port number (default: 9877)")
    p_serve.add_argument("--host", default="127.0.0.1",
                         help="Host to bind to (default: 127.0.0.1)")

    # serve-mcp
    sub.add_parser("serve-mcp", parents=[shared],
                   help="Start MCP server (stdio mode, requires mcp package)")

    # status
    sub.add_parser("status", parents=[shared],
                   help="Get coordination system status summary")

    # register
    p_reg = sub.add_parser("register", parents=[shared],
                           help="Register an agent and get context bundle")
    p_reg.add_argument("agent_id", help="Unique agent identifier")
    p_reg.add_argument("--parent-id", default=None,
                       help="Parent agent ID if spawned sub-agent")
    p_reg.add_argument("--worktree-root", default=None,
                       help="Worktree root path")

    # heartbeat
    p_hb = sub.add_parser("heartbeat", parents=[shared],
                          help="Send agent heartbeat")
    p_hb.add_argument("agent_id", help="Agent identifier")

    # deregister
    p_dereg = sub.add_parser("deregister", parents=[shared],
                             help="Deregister an agent")
    p_dereg.add_argument("agent_id", help="Agent identifier to deregister")

    # list-agents
    p_la = sub.add_parser("list-agents", parents=[shared],
                           help="List registered agents")
    p_la.add_argument("--all", action="store_true", dest="include_stale",
                      help="Include stopped/stale agents")
    p_la.add_argument("--stale-timeout", type=float, default=600.0,
                      help="Seconds before agent is stale (default: 600)")

    # lineage
    p_lin = sub.add_parser("lineage", parents=[shared],
                           help="Get agent ancestors and descendants")
    p_lin.add_argument("agent_id", help="Agent to query")

    # siblings
    p_sib = sub.add_parser("siblings", parents=[shared],
                            help="Get agent's sibling agents")
    p_sib.add_argument("agent_id", help="Agent whose siblings to find")

    # acquire-lock
    p_acq = sub.add_parser("acquire-lock", parents=[shared],
                           help="Acquire a document lock")
    p_acq.add_argument("document_path", help="Path to the document")
    p_acq.add_argument("agent_id", help="Agent requesting the lock")
    p_acq.add_argument("--lock-type", default="exclusive",
                       choices=["exclusive", "shared"],
                       help="Lock type (default: exclusive)")
    p_acq.add_argument("--ttl", type=float, default=300.0,
                       help="Lock TTL in seconds (default: 300)")
    p_acq.add_argument("--force", action="store_true",
                       help="Steal lock if held by another agent")

    # release-lock
    p_rel = sub.add_parser("release-lock", parents=[shared],
                           help="Release a held lock")
    p_rel.add_argument("document_path", help="Path to the document")
    p_rel.add_argument("agent_id", help="Agent releasing the lock")

    # refresh-lock
    p_ref = sub.add_parser("refresh-lock", parents=[shared],
                            help="Extend a lock's TTL")
    p_ref.add_argument("document_path", help="Path to the document")
    p_ref.add_argument("agent_id", help="Agent refreshing the lock")
    p_ref.add_argument("--ttl", type=float, default=300.0,
                       help="New TTL in seconds (default: 300)")

    # lock-status
    p_ls = sub.add_parser("lock-status", parents=[shared],
                           help="Check if a document is locked")
    p_ls.add_argument("document_path", help="Path to the document")

    # release-agent-locks
    p_ral = sub.add_parser("release-agent-locks", parents=[shared],
                            help="Release all locks held by an agent")
    p_ral.add_argument("agent_id", help="Agent whose locks to release")

    # reap-expired-locks
    sub.add_parser("reap-expired-locks", parents=[shared],
                   help="Clear all expired locks")

    # reap-stale-agents
    p_rsa = sub.add_parser("reap-stale-agents", parents=[shared],
                            help="Mark stale agents as stopped")
    p_rsa.add_argument("--timeout", type=float, default=600.0,
                       help="Seconds after which agent is stale (default: 600)")

    # broadcast
    p_bc = sub.add_parser("broadcast", parents=[shared],
                           help="Announce intention to siblings")
    p_bc.add_argument("agent_id", help="Agent making the broadcast")
    p_bc.add_argument("message", help="Message about intended action")
    p_bc.add_argument("--document-path", default=None,
                       help="Relevant document path")
    p_bc.add_argument("--action", default=None,
                       help="Action type hint")
    p_bc.add_argument("--ttl", type=float, default=30.0,
                       help="Broadcast TTL in seconds (default: 30)")

    # wait-for-locks
    p_wfl = sub.add_parser("wait-for-locks", parents=[shared],
                            help="Poll until locks are released")
    p_wfl.add_argument("agent_id", help="Agent doing the waiting")
    p_wfl.add_argument("document_paths", nargs="+",
                       help="Document paths to wait on")
    p_wfl.add_argument("--timeout", type=float, default=60.0,
                       help="Maximum seconds to wait (default: 60)")

    # notify-change
    p_nc = sub.add_parser("notify-change", parents=[shared],
                           help="Record a change event")
    p_nc.add_argument("document_path", help="Path to the changed document")
    p_nc.add_argument("change_type", help="Change type (created/modified/deleted)")
    p_nc.add_argument("agent_id", help="Agent that made the change")

    # get-notifications
    p_gn = sub.add_parser("get-notifications", parents=[shared],
                           help="Poll for change notifications")
    p_gn.add_argument("--since", type=float, default=None,
                      help="Unix timestamp to poll from")
    p_gn.add_argument("--exclude-agent", default=None,
                       help="Agent ID to exclude")
    p_gn.add_argument("--limit", type=int, default=100,
                       help="Max notifications (default: 100)")

    # prune-notifications
    p_pn = sub.add_parser("prune-notifications", parents=[shared],
                           help="Clean up old notifications")
    p_pn.add_argument("--max-age", type=float, default=None,
                      dest="max_age_seconds",
                      help="Delete notifications older than N seconds")
    p_pn.add_argument("--max-entries", type=int, default=None,
                      dest="max_entries",
                      help="Keep at most N notifications")

    # get-conflicts
    p_gc = sub.add_parser("get-conflicts", parents=[shared],
                           help="Query the conflict log")
    p_gc.add_argument("--document-path", default=None,
                       help="Filter by document path")
    p_gc.add_argument("--agent-id", default=None,
                       help="Filter by agent")
    p_gc.add_argument("--limit", type=int, default=20,
                       help="Max conflicts to return (default: 20)")

    return parser


# ------------------------------------------------------------------ #
# Output helpers
# ------------------------------------------------------------------ #

def _print_json(data: Any) -> None:
    """Print data as formatted JSON."""
    print(json.dumps(data, indent=2, default=str))


def _run_engine(args: argparse.Namespace) -> CoordinationEngine:
    """Construct and start a CoordinationEngine from CLI arguments."""
    storage_dir = Path(args.storage_dir) if args.storage_dir else None
    project_root = Path(args.project_root) if args.project_root else None
    namespace = getattr(args, "namespace", "hub")
    engine = CoordinationEngine(
        storage_dir=storage_dir,
        project_root=project_root,
        namespace=namespace,
    )
    engine.start()
    return engine


def _close_engine(engine: CoordinationEngine) -> None:
    """Close engine and suppress errors."""
    try:
        engine.close()
    except Exception:
        pass


# ------------------------------------------------------------------ #
# Command handlers
# ------------------------------------------------------------------ #

def cmd_serve(args: argparse.Namespace) -> None:
    """Handle the 'serve' subcommand."""
    from .mcp_server import CoordinationHubMCPServer
    storage_dir = args.storage_dir
    project_root = args.project_root
    namespace = getattr(args, "namespace", "hub")
    server = CoordinationHubMCPServer(
        storage_dir=storage_dir,
        project_root=project_root,
        namespace=namespace,
        host=args.host,
        port=args.port,
    )
    print(f"Starting CoordinationHub HTTP server on {server.get_url()}")
    try:
        server.start(blocking=True)
    finally:
        server.stop()


def cmd_serve_mcp(args: argparse.Namespace) -> None:
    """Handle the 'serve-mcp' subcommand."""
    if args.storage_dir is not None:
        os.environ["COORDINATIONHUB_STORAGE_DIR"] = args.storage_dir
    if args.project_root is not None:
        os.environ["COORDINATIONHUB_PROJECT_ROOT"] = args.project_root
    if getattr(args, "namespace", None):
        os.environ["COORDINATIONHUB_NAMESPACE"] = args.namespace
    from .mcp_stdio import main as mcp_main
    mcp_main()


def cmd_status(args: argparse.Namespace) -> None:
    """Handle the 'status' subcommand."""
    engine = _run_engine(args)
    try:
        result = engine.status()
        if args.json_output:
            _print_json(result)
        else:
            print("CoordinationHub Status:")
            for key, value in result.items():
                print(f"  {key.replace('_', ' ').title()}: {value}")
    finally:
        _close_engine(engine)


def cmd_register(args: argparse.Namespace) -> None:
    """Handle the 'register' subcommand."""
    engine = _run_engine(args)
    try:
        result = engine.register_agent(
            agent_id=args.agent_id,
            parent_id=args.parent_id,
            worktree_root=args.worktree_root,
        )
        if args.json_output:
            _print_json(result)
        else:
            print(f"Registered: {args.agent_id}")
            if result.get("parent_id"):
                print(f"  Parent: {result['parent_id']}")
            print(f"  Worktree: {result.get('worktree_root')}")
    finally:
        _close_engine(engine)


def cmd_heartbeat(args: argparse.Namespace) -> None:
    """Handle the 'heartbeat' subcommand."""
    engine = _run_engine(args)
    try:
        result = engine.heartbeat(args.agent_id)
        if args.json_output:
            _print_json(result)
        else:
            print(f"Heartbeat: {args.agent_id}")
            print(f"  Updated: {result.get('updated')}")
            print(f"  Stale released: {result.get('stale_released')}")
    finally:
        _close_engine(engine)


def cmd_deregister(args: argparse.Namespace) -> None:
    """Handle the 'deregister' subcommand."""
    engine = _run_engine(args)
    try:
        result = engine.deregister_agent(args.agent_id)
        if args.json_output:
            _print_json(result)
        else:
            print(f"Deregistered: {args.agent_id}")
            print(f"  Children orphaned: {result.get('children_orphaned')}")
            print(f"  Locks released: {result.get('locks_released')}")
    finally:
        _close_engine(engine)


def cmd_list_agents(args: argparse.Namespace) -> None:
    """Handle the 'list-agents' subcommand."""
    engine = _run_engine(args)
    try:
        result = engine.list_agents(
            active_only=not args.include_stale,
            stale_timeout=args.stale_timeout,
        )
        agents = result.get("agents", [])
        if args.json_output:
            _print_json(result)
        elif not agents:
            print("No agents registered")
        else:
            print(f"{len(agents)} agent(s):")
            for a in agents:
                stale = " (STALE)" if a.get("stale") else ""
                print(f"  {a['agent_id']}: {a['status']}{stale}")
    finally:
        _close_engine(engine)


def cmd_lineage(args: argparse.Namespace) -> None:
    """Handle the 'lineage' subcommand."""
    engine = _run_engine(args)
    try:
        result = engine.get_lineage(args.agent_id)
        if args.json_output:
            _print_json(result)
        else:
            ancestors = result.get("ancestors", [])
            descendants = result.get("descendants", [])
            print(f"Lineage for {args.agent_id}:")
            if ancestors:
                print(f"  Ancestors: {', '.join(a['agent_id'] for a in ancestors)}")
            else:
                print("  Ancestors: (none)")
            if descendants:
                print(f"  Descendants: {', '.join(d['agent_id'] for d in descendants)}")
            else:
                print("  Descendants: (none)")
    finally:
        _close_engine(engine)


def cmd_siblings(args: argparse.Namespace) -> None:
    """Handle the 'siblings' subcommand."""
    engine = _run_engine(args)
    try:
        result = engine.get_siblings(args.agent_id)
        siblings = result.get("siblings", [])
        if args.json_output:
            _print_json(result)
        elif not siblings:
            print(f"No siblings for {args.agent_id}")
        else:
            print(f"{len(siblings)} sibling(s):")
            for s in siblings:
                print(f"  {s['agent_id']}: {s['status']}")
    finally:
        _close_engine(engine)


def _fmt_lock_result(result: dict[str, Any], args: argparse.Namespace) -> None:
    """Format a lock operation result for console output."""
    if result.get("acquired"):
        print(f"LOCKED: {args.document_path}")
        print(f"  Agent: {result.get('locked_by')}")
        print(f"  Expires: {result.get('expires_at')}")
    elif result.get("released"):
        print(f"RELEASED: {args.document_path}")
    elif result.get("refreshed"):
        print(f"REFRESHED: {args.document_path}")
        print(f"  Expires: {result.get('expires_at')}")
    else:
        locked_by = result.get("locked_by", "unknown")
        expires = result.get("expires_at", "unknown")
        if result.get("stale_released"):
            print(f"EXPIRED lock on {args.document_path} was cleaned up")
        else:
            print(f"FAILED: {args.document_path} is locked by {locked_by}")
            print(f"  Expires: {expires}")


def cmd_acquire_lock(args: argparse.Namespace) -> None:
    """Handle the 'acquire-lock' subcommand."""
    engine = _run_engine(args)
    try:
        result = engine.acquire_lock(
            document_path=args.document_path,
            agent_id=args.agent_id,
            lock_type=args.lock_type,
            ttl=args.ttl,
            force=args.force,
        )
        if args.json_output:
            _print_json(result)
        else:
            _fmt_lock_result(result, args)
    finally:
        _close_engine(engine)


def cmd_release_lock(args: argparse.Namespace) -> None:
    """Handle the 'release-lock' subcommand."""
    engine = _run_engine(args)
    try:
        result = engine.release_lock(args.document_path, args.agent_id)
        if args.json_output:
            _print_json(result)
        else:
            if result.get("released"):
                print(f"RELEASED: {args.document_path}")
            else:
                print(f"FAILED: {result.get('reason')}")
    finally:
        _close_engine(engine)


def cmd_refresh_lock(args: argparse.Namespace) -> None:
    """Handle the 'refresh-lock' subcommand."""
    engine = _run_engine(args)
    try:
        result = engine.refresh_lock(args.document_path, args.agent_id, ttl=args.ttl)
        if args.json_output:
            _print_json(result)
        else:
            if result.get("refreshed"):
                print(f"REFRESHED: {args.document_path}")
                print(f"  Expires: {result.get('expires_at')}")
            else:
                print(f"FAILED: {result.get('reason')}")
    finally:
        _close_engine(engine)


def cmd_lock_status(args: argparse.Namespace) -> None:
    """Handle the 'lock-status' subcommand."""
    engine = _run_engine(args)
    try:
        result = engine.get_lock_status(args.document_path)
        if args.json_output:
            _print_json(result)
        else:
            if result.get("locked"):
                print(f"LOCKED: {args.document_path}")
                print(f"  By: {result.get('locked_by')}")
                print(f"  Expires: {result.get('expires_at')}")
                print(f"  Worktree: {result.get('worktree')}")
            else:
                print(f"UNLOCKED: {args.document_path}")
    finally:
        _close_engine(engine)


def cmd_release_agent_locks(args: argparse.Namespace) -> None:
    """Handle the 'release-agent-locks' subcommand."""
    engine = _run_engine(args)
    try:
        result = engine.release_agent_locks(args.agent_id)
        if args.json_output:
            _print_json(result)
        else:
            print(f"Released {result.get('released', 0)} lock(s) for {args.agent_id}")
    finally:
        _close_engine(engine)


def cmd_reap_expired_locks(args: argparse.Namespace) -> None:
    """Handle the 'reap-expired-locks' subcommand."""
    engine = _run_engine(args)
    try:
        result = engine.reap_expired_locks()
        if args.json_output:
            _print_json(result)
        else:
            print(f"Reaped {result.get('reaped', 0)} expired lock(s)")
    finally:
        _close_engine(engine)


def cmd_reap_stale_agents(args: argparse.Namespace) -> None:
    """Handle the 'reap-stale-agents' subcommand."""
    engine = _run_engine(args)
    try:
        result = engine.reap_stale_agents(timeout=args.timeout)
        if args.json_output:
            _print_json(result)
        else:
            print(f"Reaped {result.get('reaped', 0)} stale agent(s)")
            print(f"  Orphaned children: {result.get('orphaned_children', 0)}")
    finally:
        _close_engine(engine)


def cmd_broadcast(args: argparse.Namespace) -> None:
    """Handle the 'broadcast' subcommand."""
    engine = _run_engine(args)
    try:
        result = engine.broadcast(
            agent_id=args.agent_id,
            message=args.message,
            document_path=args.document_path,
            action=args.action,
            ttl=args.ttl,
        )
        if args.json_output:
            _print_json(result)
        else:
            ack = result.get("acknowledged_by", [])
            conflicts = result.get("conflicts", [])
            print(f"Broadcast from {args.agent_id}: {args.message}")
            print(f"  Acknowledged by: {ack or '(none)'}")
            if conflicts:
                print(f"  Conflicts:")
                for c in conflicts:
                    print(f"    {c['document_path']} locked by {c['locked_by']}")
    finally:
        _close_engine(engine)


def cmd_wait_for_locks(args: argparse.Namespace) -> None:
    """Handle the 'wait-for-locks' subcommand."""
    engine = _run_engine(args)
    try:
        result = engine.wait_for_locks(
            document_paths=args.document_paths,
            agent_id=args.agent_id,
            timeout_s=args.timeout,
        )
        if args.json_output:
            _print_json(result)
        else:
            released = result.get("released", [])
            timed_out = result.get("timed_out", [])
            print(f"Released: {released or '(none)'}")
            print(f"Timed out: {timed_out or '(none)'}")
    finally:
        _close_engine(engine)


def cmd_notify_change(args: argparse.Namespace) -> None:
    """Handle the 'notify-change' subcommand."""
    engine = _run_engine(args)
    try:
        result = engine.notify_change(
            document_path=args.document_path,
            change_type=args.change_type,
            agent_id=args.agent_id,
        )
        if args.json_output:
            _print_json(result)
        else:
            print(f"Recorded: {args.change_type} on {args.document_path} by {args.agent_id}")
    finally:
        _close_engine(engine)


def cmd_get_notifications(args: argparse.Namespace) -> None:
    """Handle the 'get-notifications' subcommand."""
    engine = _run_engine(args)
    try:
        result = engine.get_notifications(
            since=args.since,
            exclude_agent=args.exclude_agent,
            limit=args.limit,
        )
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
    finally:
        _close_engine(engine)


def cmd_prune_notifications(args: argparse.Namespace) -> None:
    """Handle the 'prune-notifications' subcommand."""
    engine = _run_engine(args)
    try:
        result = engine.prune_notifications(
            max_age_seconds=args.max_age_seconds,
            max_entries=args.max_entries,
        )
        if args.json_output:
            _print_json(result)
        else:
            print(f"Pruned {result.get('pruned', 0)} notification(s)")
    finally:
        _close_engine(engine)


def cmd_get_conflicts(args: argparse.Namespace) -> None:
    """Handle the 'get-conflicts' subcommand."""
    engine = _run_engine(args)
    try:
        result = engine.get_conflicts(
            document_path=args.document_path,
            agent_id=args.agent_id,
            limit=args.limit,
        )
        if args.json_output:
            _print_json(result)
        else:
            conflicts = result.get("conflicts", [])
            if not conflicts:
                print("No conflicts")
            else:
                print(f"{len(conflicts)} conflict(s):")
                for c in conflicts:
                    print(f"  {c['document_path']}: {c['conflict_type']} "
                          f"({c['agent_a']} vs {c['agent_b']}) resolved: {c['resolution']}")
    finally:
        _close_engine(engine)


# ------------------------------------------------------------------ #
# Dispatch table
# ------------------------------------------------------------------ #

_COMMANDS: dict[str, callable] = {
    "serve": cmd_serve,
    "serve-mcp": cmd_serve_mcp,
    "status": cmd_status,
    "register": cmd_register,
    "heartbeat": cmd_heartbeat,
    "deregister": cmd_deregister,
    "list-agents": cmd_list_agents,
    "lineage": cmd_lineage,
    "siblings": cmd_siblings,
    "acquire-lock": cmd_acquire_lock,
    "release-lock": cmd_release_lock,
    "refresh-lock": cmd_refresh_lock,
    "lock-status": cmd_lock_status,
    "release-agent-locks": cmd_release_agent_locks,
    "reap-expired-locks": cmd_reap_expired_locks,
    "reap-stale-agents": cmd_reap_stale_agents,
    "broadcast": cmd_broadcast,
    "wait-for-locks": cmd_wait_for_locks,
    "notify-change": cmd_notify_change,
    "get-notifications": cmd_get_notifications,
    "prune-notifications": cmd_prune_notifications,
    "get-conflicts": cmd_get_conflicts,
}


# ------------------------------------------------------------------ #
# Main entry point
# ------------------------------------------------------------------ #

def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch to the appropriate handler.

    Args:
        argv: Command-line arguments (default: sys.argv[1:]).

    Returns:
        Exit code (0 on success, 1 on error).
    """
    parser = create_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    handler = _COMMANDS.get(args.command)
    if handler is None:
        parser.print_help()
        return 1

    try:
        handler(args)
        return 0
    except Exception as exc:
        if args.json_output:
            _print_json({"error": str(exc)})
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
