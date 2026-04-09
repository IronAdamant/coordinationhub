"""CoordinationHub CLI — command-line interface for all 29 coordination tool methods.

Delegates to cli_commands.py for all command handlers.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .core import CoordinationEngine


# ------------------------------------------------------------------ #
# Parser
# ------------------------------------------------------------------ #

def create_parser() -> argparse.ArgumentParser:
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--storage-dir", default=None)
    shared.add_argument("--project-root", default=None)
    shared.add_argument("--namespace", default="hub")
    shared.add_argument("--json", action="store_true", dest="json_output")

    parser = argparse.ArgumentParser(prog="coordinationhub",
                                       description="CoordinationHub — declarative multi-agent coordination")
    sub = parser.add_subparsers(dest="command")

    # serve
    p = sub.add_parser("serve", parents=[shared], help="Start HTTP MCP server")
    p.add_argument("--port", type=int, default=9877)
    p.add_argument("--host", default="127.0.0.1")

    # serve-mcp
    sub.add_parser("serve-mcp", parents=[shared], help="Start MCP server (stdio mode)")

    # status
    sub.add_parser("status", parents=[shared], help="Get coordination system status summary")

    # register
    p = sub.add_parser("register", parents=[shared], help="Register an agent and get context bundle")
    p.add_argument("agent_id", help="Unique agent identifier")
    p.add_argument("--parent-id", default=None)
    p.add_argument("--graph-agent-id", dest="graph_agent_id", default=None,
                   help="ID in the coordination graph this agent implements (e.g. planner)")
    p.add_argument("--worktree-root", default=None)

    # heartbeat
    p = sub.add_parser("heartbeat", parents=[shared], help="Send agent heartbeat")
    p.add_argument("agent_id", help="Agent identifier")

    # deregister
    p = sub.add_parser("deregister", parents=[shared], help="Deregister an agent")
    p.add_argument("agent_id", help="Agent identifier to deregister")

    # list-agents
    p = sub.add_parser("list-agents", parents=[shared], help="List registered agents")
    p.add_argument("--all", action="store_true", dest="include_stale")
    p.add_argument("--stale-timeout", type=float, default=600.0)

    # lineage
    p = sub.add_parser("lineage", parents=[shared], help="Get agent ancestors and descendants")
    p.add_argument("agent_id", help="Agent to query")

    # siblings
    p = sub.add_parser("siblings", parents=[shared], help="Get agent's sibling agents")
    p.add_argument("agent_id", help="Agent whose siblings to find")

    # acquire-lock
    p = sub.add_parser("acquire-lock", parents=[shared], help="Acquire a document lock")
    p.add_argument("document_path", help="Path to the document")
    p.add_argument("agent_id", help="Agent requesting the lock")
    p.add_argument("--lock-type", default="exclusive", choices=["exclusive", "shared"])
    p.add_argument("--ttl", type=float, default=300.0)
    p.add_argument("--force", action="store_true")

    # release-lock
    p = sub.add_parser("release-lock", parents=[shared], help="Release a held lock")
    p.add_argument("document_path", help="Path to the document")
    p.add_argument("agent_id", help="Agent releasing the lock")

    # refresh-lock
    p = sub.add_parser("refresh-lock", parents=[shared], help="Extend a lock's TTL")
    p.add_argument("document_path", help="Path to the document")
    p.add_argument("agent_id", help="Agent refreshing the lock")
    p.add_argument("--ttl", type=float, default=300.0)

    # lock-status
    p = sub.add_parser("lock-status", parents=[shared], help="Check if a document is locked")
    p.add_argument("document_path", help="Path to the document")

    # list-locks
    p = sub.add_parser("list-locks", parents=[shared], help="List all active locks")
    p.add_argument("--agent-id", default=None, help="Filter to locks held by this agent")

    # release-agent-locks
    p = sub.add_parser("release-agent-locks", parents=[shared], help="Release all locks held by an agent")
    p.add_argument("agent_id", help="Agent whose locks to release")

    # reap-expired-locks
    sub.add_parser("reap-expired-locks", parents=[shared], help="Clear all expired locks")

    # reap-stale-agents
    p = sub.add_parser("reap-stale-agents", parents=[shared], help="Mark stale agents as stopped")
    p.add_argument("--timeout", type=float, default=600.0)

    # broadcast
    p = sub.add_parser("broadcast", parents=[shared], help="Announce intention to siblings")
    p.add_argument("agent_id", help="Agent making the broadcast")
    p.add_argument("--document-path", default=None)

    # wait-for-locks
    p = sub.add_parser("wait-for-locks", parents=[shared], help="Poll until locks are released")
    p.add_argument("agent_id", help="Agent doing the waiting")
    p.add_argument("document_paths", nargs="+", help="Document paths to wait on")
    p.add_argument("--timeout", type=float, default=60.0)

    # notify-change
    p = sub.add_parser("notify-change", parents=[shared], help="Record a change event")
    p.add_argument("document_path", help="Path to the changed document")
    p.add_argument("change_type", help="Change type (created/modified/deleted)")
    p.add_argument("agent_id", help="Agent that made the change")

    # get-notifications
    p = sub.add_parser("get-notifications", parents=[shared], help="Poll for change notifications")
    p.add_argument("--since", type=float, default=None)
    p.add_argument("--exclude-agent", default=None)
    p.add_argument("--limit", type=int, default=100)

    # prune-notifications
    p = sub.add_parser("prune-notifications", parents=[shared], help="Clean up old notifications")
    p.add_argument("--max-age", type=float, default=None, dest="max_age_seconds")
    p.add_argument("--max-entries", type=int, default=None, dest="max_entries")

    # get-conflicts
    p = sub.add_parser("get-conflicts", parents=[shared], help="Query the conflict log")
    p.add_argument("--document-path", default=None)
    p.add_argument("--agent-id", default=None)
    p.add_argument("--limit", type=int, default=20)

    # --- NEW SUBMANDS ---

    # load-spec
    p = sub.add_parser("load-spec", parents=[shared], help="Reload coordination spec from disk")
    p.add_argument("path", nargs="?", default=None, help="Path to coordination spec (default: project root)")

    # validate-spec
    sub.add_parser("validate-spec", parents=[shared], help="Validate loaded coordination spec")

    # scan-project
    p = sub.add_parser("scan-project", parents=[shared], help="Perform file ownership scan")
    p.add_argument("--worktree-root", default=None)
    p.add_argument("--extensions", nargs="+", default=None,
                   help="File extensions to scan (default: py md json yaml txt toml)")

    # dashboard
    p = sub.add_parser("dashboard", parents=[shared], help="Print live agent/file status table")
    p.add_argument("--agent-id", default=None, help="Filter to a specific agent")
    p.add_argument("--min", action="store_true", dest="minimal",
                   help="Show only compact one-line-per-agent summary")

    # agent-status
    p = sub.add_parser("agent-status", parents=[shared], help="Get full status for an agent")
    p.add_argument("agent_id", help="Agent to query")
    p.add_argument("--tree", action="store_true", help="Print agent tree instead of flat status")

    # agent-tree
    p = sub.add_parser("agent-tree", parents=[shared], help="Print agent hierarchy as a tree")
    p.add_argument("agent_id", nargs="?", default=None, help="Root agent (default: oldest root)")

    # assess
    p = sub.add_parser("assess", parents=[shared], help="Run an assessment suite")
    p.add_argument("--suite", dest="suite_path", required=True, help="Path to the JSON test suite file")
    p.add_argument("--format", default="markdown", choices=["markdown", "json"],
                   help="Output format (default: markdown)")
    p.add_argument("--output", default=None, dest="output_path",
                   help="Write report to file instead of stdout")
    p.add_argument("--graph-agent-id", dest="graph_agent_id", default=None,
                   help="Filter traces to this graph agent role (e.g. planner, executor)")

    return parser


# ------------------------------------------------------------------ #
# Command dispatch — imports from cli_commands.py on demand
# ------------------------------------------------------------------ #

_COMMANDS = {
    "serve": "cmd_serve", "serve-mcp": "cmd_serve_mcp", "status": "cmd_status",
    "register": "cmd_register", "heartbeat": "cmd_heartbeat", "deregister": "cmd_deregister",
    "list-agents": "cmd_list_agents", "lineage": "cmd_lineage", "siblings": "cmd_siblings",
    "acquire-lock": "cmd_acquire_lock", "release-lock": "cmd_release_lock",
    "refresh-lock": "cmd_refresh_lock", "lock-status": "cmd_lock_status",
    "list-locks": "cmd_list_locks",
    "release-agent-locks": "cmd_release_agent_locks", "reap-expired-locks": "cmd_reap_expired_locks",
    "reap-stale-agents": "cmd_reap_stale_agents", "broadcast": "cmd_broadcast",
    "wait-for-locks": "cmd_wait_for_locks", "notify-change": "cmd_notify_change",
    "get-notifications": "cmd_get_notifications", "prune-notifications": "cmd_prune_notifications",
    "get-conflicts": "cmd_get_conflicts",
    "load-spec": "cmd_load_spec", "validate-spec": "cmd_validate_spec",
    "scan-project": "cmd_scan_project", "dashboard": "cmd_dashboard",
    "agent-status": "cmd_agent_status", "assess": "cmd_assess",
    "agent-tree": "cmd_agent_tree",
}


def _get_handler(name: str):
    """Lazily import and return a command handler."""
    from . import cli_commands as _cmds
    return getattr(_cmds, name)


# ------------------------------------------------------------------ #
# Main
# ------------------------------------------------------------------ #

def main(argv: list[str] | None = None) -> int:
    parser = create_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    handler_name = _COMMANDS.get(args.command)
    if handler_name is None:
        parser.print_help()
        return 1
    try:
        handler = _get_handler(handler_name)
        handler(args)
        return 0
    except Exception as exc:
        if getattr(args, "json_output", False):
            print(json.dumps({"error": str(exc)}, indent=2))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
