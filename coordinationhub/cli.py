"""CoordinationHub CLI — command-line interface for all 55 coordination tool methods.

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
    p.add_argument("--region-start", type=int, default=None, help="Start line for region lock")
    p.add_argument("--region-end", type=int, default=None, help="End line for region lock")
    p.add_argument("--retry", action="store_true", help="Retry with exponential backoff on contention")
    p.add_argument("--max-retries", type=int, default=5, help="Maximum retry attempts (default: 5)")
    p.add_argument("--backoff-ms", type=float, default=100.0, help="Starting backoff in ms (default: 100)")
    p.add_argument("--timeout-ms", type=float, default=5000.0, help="Total timeout in ms (default: 5000)")

    # release-lock
    p = sub.add_parser("release-lock", parents=[shared], help="Release a held lock")
    p.add_argument("document_path", help="Path to the document")
    p.add_argument("agent_id", help="Agent releasing the lock")
    p.add_argument("--region-start", type=int, default=None, help="Start line of region lock to release")
    p.add_argument("--region-end", type=int, default=None, help="End line of region lock to release")

    # refresh-lock
    p = sub.add_parser("refresh-lock", parents=[shared], help="Extend a lock's TTL")
    p.add_argument("document_path", help="Path to the document")
    p.add_argument("agent_id", help="Agent refreshing the lock")
    p.add_argument("--ttl", type=float, default=300.0)
    p.add_argument("--region-start", type=int, default=None, help="Start line of region lock to refresh")
    p.add_argument("--region-end", type=int, default=None, help="End line of region lock to refresh")

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
    p.add_argument("--handoff-targets", nargs="+", default=None, dest="handoff_targets",
                   help="Agent IDs to hand off to (triggers formal handoff)")

    # acknowledge-handoff
    p = sub.add_parser("acknowledge-handoff", parents=[shared], help="Acknowledge a handoff")
    p.add_argument("handoff_id", type=int, help="Handoff ID to acknowledge")
    p.add_argument("agent_id", help="Agent acknowledging")

    # complete-handoff
    p = sub.add_parser("complete-handoff", parents=[shared], help="Complete a handoff")
    p.add_argument("handoff_id", type=int, help="Handoff ID to complete")

    # cancel-handoff
    p = sub.add_parser("cancel-handoff", parents=[shared], help="Cancel a handoff")
    p.add_argument("handoff_id", type=int, help="Handoff ID to cancel")

    # get-handoffs
    p = sub.add_parser("get-handoffs", parents=[shared], help="Get handoffs")
    p.add_argument("--status", default=None, help="Filter by status (pending/acknowledged/completed/cancelled)")
    p.add_argument("--from-agent-id", default=None, dest="from_agent_id", help="Filter by sender")
    p.add_argument("--limit", type=int, default=50)

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

    # contention-hotspots
    p = sub.add_parser("contention-hotspots", parents=[shared],
                       help="Rank files by lock contention frequency")
    p.add_argument("--limit", type=int, default=10)

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

    # assess-session
    p = sub.add_parser(
        "assess-session", parents=[shared],
        help="Score the current live session (no suite file needed)",
    )
    p.add_argument("--format", default="markdown", choices=["markdown", "json"],
                   help="Output format (default: markdown)")
    p.add_argument("--output", default=None,
                   help="Write report to file instead of stdout")
    p.add_argument("--graph-agent-id", dest="graph_agent_id", default=None,
                   help="Filter traces to this graph agent role")
    p.add_argument("--scope", default="project", choices=["project", "all"],
                   help="'project' (default) limits to current worktree; 'all' scores every agent")

    # doctor
    sub.add_parser("doctor", parents=[shared], help="Validate CoordinationHub setup and diagnose issues")

    # init
    sub.add_parser("init", parents=[shared], help="Set up CoordinationHub: create DB, configure hooks")

    # watch
    p = sub.add_parser("watch", parents=[shared], help="Live-refresh agent tree (Ctrl+C to stop)")
    p.add_argument("agent_id", nargs="?", default=None, help="Root agent (default: oldest root)")
    p.add_argument("--interval", type=int, default=5, help="Refresh interval in seconds (default: 5)")

    # await-agent
    p = sub.add_parser("await-agent", parents=[shared], help="Wait for an agent to complete")
    p.add_argument("agent_id", help="Agent to wait for")
    p.add_argument("--timeout", type=float, default=60.0, help="Timeout in seconds (default: 60)")

    # send-message
    p = sub.add_parser("send-message", parents=[shared], help="Send a message to another agent")
    p.add_argument("from_agent_id", help="Agent sending the message")
    p.add_argument("to_agent_id", help="Agent to receive the message")
    p.add_argument("message_type", help="Message type (e.g. query, response)")
    p.add_argument("--payload", default=None, help="JSON payload")

    # get-messages
    p = sub.add_parser("get-messages", parents=[shared], help="Get messages for an agent")
    p.add_argument("agent_id", help="Agent to get messages for")
    p.add_argument("--unread-only", action="store_true", help="Only return unread messages")
    p.add_argument("--limit", type=int, default=50, help="Maximum messages to return (default: 50)")

    # mark-messages-read
    p = sub.add_parser("mark-messages-read", parents=[shared], help="Mark messages as read")
    p.add_argument("agent_id", help="Agent marking messages as read")
    p.add_argument("--message-ids", type=int, nargs="+", default=None, help="Specific message IDs to mark")

    # --- TASK REGISTRY ---
    p = sub.add_parser("create-task", parents=[shared], help="Create a new task in the shared registry")
    p.add_argument("task_id", help="Unique task ID (e.g. hub.12345.0.task.0)")
    p.add_argument("parent_agent_id", help="Agent creating this task")
    p.add_argument("description", help="What this task involves")
    p.add_argument("--depends-on", nargs="+", default=None, dest="depends_on",
                   help="Task IDs that must complete first")

    p = sub.add_parser("assign-task", parents=[shared], help="Assign a task to an agent")
    p.add_argument("task_id", help="Task to assign")
    p.add_argument("assigned_agent_id", help="Agent to assign the task to")

    p = sub.add_parser("update-task-status", parents=[shared], help="Update a task's status")
    p.add_argument("task_id", help="Task to update")
    p.add_argument("status", choices=["pending", "in_progress", "completed", "blocked"],
                   help="New status")
    p.add_argument("--summary", default=None, help="Completion summary (used for compression chains)")
    p.add_argument("--blocked-by", default=None, dest="blocked_by",
                   help="Task ID blocking this task")

    p = sub.add_parser("get-task", parents=[shared], help="Get a single task by ID")
    p.add_argument("task_id", help="Task to retrieve")

    p = sub.add_parser("get-child-tasks", parents=[shared], help="Get all tasks created by an agent")
    p.add_argument("parent_agent_id", help="Agent whose child tasks to retrieve")

    p = sub.add_parser("get-tasks-by-agent", parents=[shared], help="Get all tasks assigned to an agent")
    p.add_argument("assigned_agent_id", help="Agent whose assigned tasks to retrieve")

    sub.add_parser("get-all-tasks", parents=[shared], help="Get all tasks in the registry")

    p = sub.add_parser("create-subtask", parents=[shared], help="Create a subtask under an existing parent task")
    p.add_argument("task_id", help="Unique subtask ID (e.g. parent_task_id.0)")
    p.add_argument("parent_task_id", help="ID of the parent task this subtask belongs to")
    p.add_argument("parent_agent_id", help="Agent creating this subtask")
    p.add_argument("description", help="What this subtask involves")
    p.add_argument("--depends-on", nargs="+", default=None, dest="depends_on",
                   help="Task IDs that must complete first")

    p = sub.add_parser("get-subtasks", parents=[shared], help="Get all direct subtasks of a task")
    p.add_argument("parent_task_id", help="ID of the parent task whose subtasks to retrieve")

    p = sub.add_parser("get-task-tree", parents=[shared], help="Get a task with all subtasks recursively")
    p.add_argument("root_task_id", help="ID of the root task to build the tree from")

    # --- WORK INTENT BOARD ---
    p = sub.add_parser("declare-work-intent", parents=[shared], help="Declare intent to work on a file")
    p.add_argument("agent_id", help="Agent declaring intent")
    p.add_argument("document_path", help="File path the agent intends to work on")
    p.add_argument("intent", help="Short description of work intent")
    p.add_argument("--ttl", type=float, default=60.0, help="Seconds until intent expires (default: 60)")

    p = sub.add_parser("get-work-intents", parents=[shared], help="Get all active work intents")
    p.add_argument("--agent-id", default=None, dest="agent_id", help="Filter to a specific agent")

    p = sub.add_parser("clear-work-intent", parents=[shared], help="Clear an agent's declared work intent")
    p.add_argument("agent_id", help="Agent whose intent to clear")

    # --- CROSS-AGENT DEPENDENCIES ---
    p = sub.add_parser("declare-dependency", parents=[shared], help="Declare a dependency between agents")
    p.add_argument("dependent_agent_id", help="Agent that depends on another")
    p.add_argument("depends_on_agent_id", help="Agent being depended upon")
    p.add_argument("--depends-on-task-id", default=None, dest="depends_on_task_id",
                   help="Specific task ID that must complete")
    p.add_argument("--condition", default="task_completed",
                   choices=["task_completed", "agent_registered", "agent_stopped"],
                   help="Condition for dependency satisfaction (default: task_completed)")

    p = sub.add_parser("check-dependencies", parents=[shared], help="Check if an agent's dependencies are satisfied")
    p.add_argument("agent_id", help="Agent whose dependencies to check")

    p = sub.add_parser("satisfy-dependency", parents=[shared], help="Mark a dependency as satisfied")
    p.add_argument("dep_id", type=int, help="Dependency ID to mark as satisfied")

    p = sub.add_parser("get-blockers", parents=[shared], help="Get blocking dependencies for an agent")
    p.add_argument("agent_id", help="Agent to check blockers for")

    p = sub.add_parser("assert-can-start", parents=[shared], help="Assert whether an agent can start work")
    p.add_argument("agent_id", help="Agent to check start eligibility for")

    p = sub.add_parser("get-all-dependencies", parents=[shared], help="List all declared dependencies")
    p.add_argument("--dependent-agent-id", default=None, dest="dependent_agent_id",
                   help="Filter by dependent agent")

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
    "acknowledge-handoff": "cmd_acknowledge_handoff",
    "complete-handoff": "cmd_complete_handoff",
    "cancel-handoff": "cmd_cancel_handoff",
    "get-handoffs": "cmd_get_handoffs",
    "wait-for-locks": "cmd_wait_for_locks", "notify-change": "cmd_notify_change",
    "get-notifications": "cmd_get_notifications", "prune-notifications": "cmd_prune_notifications",
    "get-conflicts": "cmd_get_conflicts", "contention-hotspots": "cmd_contention_hotspots",
    "load-spec": "cmd_load_spec", "validate-spec": "cmd_validate_spec",
    "scan-project": "cmd_scan_project", "dashboard": "cmd_dashboard",
    "agent-status": "cmd_agent_status", "assess": "cmd_assess",
    "assess-session": "cmd_assess_session",
    "agent-tree": "cmd_agent_tree",
    "doctor": "cmd_doctor",
    "init": "cmd_init",
    "watch": "cmd_watch",
    "await-agent": "cmd_await_agent",
    "send-message": "cmd_send_message",
    "get-messages": "cmd_get_messages",
    "mark-messages-read": "cmd_mark_messages_read",
    "create-task": "cmd_create_task",
    "assign-task": "cmd_assign_task",
    "update-task-status": "cmd_update_task_status",
    "get-task": "cmd_get_task",
    "get-child-tasks": "cmd_get_child_tasks",
    "get-tasks-by-agent": "cmd_get_tasks_by_agent",
    "get-all-tasks": "cmd_get_all_tasks",
    "create-subtask": "cmd_create_subtask",
    "get-subtasks": "cmd_get_subtasks",
    "get-task-tree": "cmd_get_task_tree",
    "declare-work-intent": "cmd_declare_work_intent",
    "get-work-intents": "cmd_get_work_intents",
    "clear-work-intent": "cmd_clear_work_intent",
    "declare-dependency": "cmd_declare_dependency",
    "check-dependencies": "cmd_check_dependencies",
    "satisfy-dependency": "cmd_satisfy_dependency",
    "get-blockers": "cmd_get_blockers",
    "assert-can-start": "cmd_assert_can_start",
    "get-all-dependencies": "cmd_get_all_dependencies",
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
