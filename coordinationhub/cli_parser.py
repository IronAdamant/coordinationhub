"""Argument parser for the CoordinationHub CLI.

Extracted from :mod:`cli` so the entry-point module stays well under the
500-LOC module budget. Pure argparse construction — no command handlers.
"""

from __future__ import annotations

import argparse


# ------------------------------------------------------------------ #
# Shared flags
# ------------------------------------------------------------------ #

def _make_shared() -> argparse.ArgumentParser:
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--storage-dir", default=None)
    shared.add_argument("--project-root", default=None)
    shared.add_argument("--namespace", default="hub")
    shared.add_argument("--json", action="store_true", dest="json_output")
    shared.add_argument("--replica", action="store_true",
                        help="Use read-replica mode (direct WAL read, no writer round-trip)")
    return shared


# ------------------------------------------------------------------ #
# Topical sub-parser builders
# ------------------------------------------------------------------ #

def _add_serve(sub, shared) -> None:
    p = sub.add_parser("serve", parents=[shared], help="Start HTTP MCP server")
    p.add_argument("--port", type=int, default=9877)
    p.add_argument("--host", default="127.0.0.1")

    sub.add_parser("serve-mcp", parents=[shared], help="Start MCP server (stdio mode)")

    p = sub.add_parser("serve-sse", parents=[shared], help="Start HTTP server with SSE dashboard")
    p.add_argument("--port", type=int, default=9898)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--no-browser", action="store_true", dest="no_browser",
                   help="Don't open the browser automatically")

    sub.add_parser("status", parents=[shared], help="Get coordination system status summary")


def _add_identity(sub, shared) -> None:
    p = sub.add_parser("register", parents=[shared], help="Register an agent and get context bundle")
    p.add_argument("agent_id", help="Unique agent identifier")
    p.add_argument("--parent-id", default=None)
    p.add_argument("--graph-agent-id", dest="graph_agent_id", default=None,
                   help="ID in the coordination graph this agent implements (e.g. planner)")
    p.add_argument("--worktree-root", default=None)

    p = sub.add_parser("heartbeat", parents=[shared], help="Send agent heartbeat")
    p.add_argument("agent_id", help="Agent identifier")

    p = sub.add_parser("deregister", parents=[shared], help="Deregister an agent")
    p.add_argument("agent_id", help="Agent identifier to deregister")

    p = sub.add_parser("list-agents", parents=[shared], help="List registered agents")
    p.add_argument("--all", action="store_true", dest="include_stale")
    p.add_argument("--stale-timeout", type=float, default=600.0)

    p = sub.add_parser("agent-relations", parents=[shared], help="Get agent lineage or siblings")
    p.add_argument("agent_id", help="Agent to query")
    p.add_argument("--mode", default="lineage", choices=["lineage", "siblings"],
                   help="Relation type to fetch")


def _add_locking(sub, shared) -> None:
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

    p = sub.add_parser("release-lock", parents=[shared], help="Release a held lock")
    p.add_argument("document_path", help="Path to the document")
    p.add_argument("agent_id", help="Agent releasing the lock")
    p.add_argument("--region-start", type=int, default=None, help="Start line of region lock to release")
    p.add_argument("--region-end", type=int, default=None, help="End line of region lock to release")

    p = sub.add_parser("refresh-lock", parents=[shared], help="Extend a lock's TTL")
    p.add_argument("document_path", help="Path to the document")
    p.add_argument("agent_id", help="Agent refreshing the lock")
    p.add_argument("--ttl", type=float, default=300.0)
    p.add_argument("--region-start", type=int, default=None, help="Start line of region lock to refresh")
    p.add_argument("--region-end", type=int, default=None, help="End line of region lock to refresh")

    p = sub.add_parser("lock-status", parents=[shared], help="Check if a document is locked")
    p.add_argument("document_path", help="Path to the document")

    p = sub.add_parser("list-locks", parents=[shared], help="List all active locks")
    p.add_argument("--agent-id", default=None, help="Filter to locks held by this agent")

    p = sub.add_parser("admin-locks", parents=[shared], help="Administrative lock operations")
    p.add_argument("action", choices=["release_by_agent", "reap_expired", "reap_stale"],
                   help="Action to perform")
    p.add_argument("--agent-id", default=None, help="Agent whose locks to release (for release_by_agent)")
    p.add_argument("--grace-seconds", type=float, default=0.0, help="Grace period for reap_expired")
    p.add_argument("--timeout", type=float, default=600.0, help="Stale timeout for reap_stale")


def _add_broadcast_handoff(sub, shared) -> None:
    p = sub.add_parser("broadcast", parents=[shared], help="Announce intention to siblings")
    p.add_argument("agent_id", help="Agent making the broadcast")
    p.add_argument("--document-path", default=None)
    p.add_argument("--handoff-targets", nargs="+", default=None, dest="handoff_targets",
                   help="Agent IDs to hand off to (triggers formal handoff)")
    p.add_argument("--require-ack", action="store_true", default=False,
                   help="Require recipients to acknowledge via acknowledge-broadcast")
    p.add_argument("--message", default=None, help="Message payload when ack is required")

    p = sub.add_parser("acknowledge-broadcast", parents=[shared],
                       help="Acknowledge receipt of a broadcast")
    p.add_argument("broadcast_id", type=int, help="Broadcast ID to acknowledge")
    p.add_argument("agent_id", help="Agent acknowledging the broadcast")

    p = sub.add_parser("wait-for-broadcast-acks", parents=[shared],
                       help="Wait until all broadcast acknowledgments are received")
    p.add_argument("broadcast_id", type=int, help="Broadcast ID to wait for")
    p.add_argument("--timeout", type=float, default=30.0, help="Timeout in seconds (default: 30)")

    p = sub.add_parser("acknowledge-handoff", parents=[shared], help="Acknowledge a handoff")
    p.add_argument("handoff_id", type=int, help="Handoff ID to acknowledge")
    p.add_argument("agent_id", help="Agent acknowledging")

    p = sub.add_parser("complete-handoff", parents=[shared], help="Complete a handoff")
    p.add_argument("handoff_id", type=int, help="Handoff ID to complete")

    p = sub.add_parser("cancel-handoff", parents=[shared], help="Cancel a handoff")
    p.add_argument("handoff_id", type=int, help="Handoff ID to cancel")

    p = sub.add_parser("get-handoffs", parents=[shared], help="Get handoffs")
    p.add_argument("--status", default=None,
                   help="Filter by status (pending/acknowledged/completed/cancelled)")
    p.add_argument("--from-agent-id", default=None, dest="from_agent_id", help="Filter by sender")
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("wait-for-handoff", parents=[shared], help="Wait until a handoff is completed")
    p.add_argument("handoff_id", type=int, help="Handoff ID to wait for")
    p.add_argument("--timeout", type=float, default=30.0, help="Timeout in seconds (default: 30)")

    p = sub.add_parser("wait-for-locks", parents=[shared], help="Poll until locks are released")
    p.add_argument("agent_id", help="Agent doing the waiting")
    p.add_argument("document_paths", nargs="+", help="Document paths to wait on")
    p.add_argument("--timeout", type=float, default=60.0)


def _add_notifications(sub, shared) -> None:
    p = sub.add_parser("notify-change", parents=[shared], help="Record a change event")
    p.add_argument("document_path", help="Path to the changed document")
    p.add_argument("change_type", help="Change type (created/modified/deleted)")
    p.add_argument("agent_id", help="Agent that made the change")

    p = sub.add_parser("get-notifications", parents=[shared], help="Poll for change notifications")
    p.add_argument("--since", type=float, default=None)
    p.add_argument("--exclude-agent", default=None)
    p.add_argument("--limit", type=int, default=100)

    p = sub.add_parser("prune-notifications", parents=[shared], help="Clean up old notifications")
    p.add_argument("--max-age", type=float, default=None, dest="max_age_seconds")
    p.add_argument("--max-entries", type=int, default=None, dest="max_entries")

    p = sub.add_parser("wait-for-notifications", parents=[shared],
                       help="Long-poll for new notifications until one arrives or timeout expires")
    p.add_argument("agent_id", help="Agent ID doing the waiting")
    p.add_argument("--timeout", type=float, default=30.0, help="Timeout in seconds (default: 30)")
    p.add_argument("--poll-interval", type=float, default=2.0, dest="poll_interval",
                   help="Polling interval in seconds (default: 2)")
    p.add_argument("--exclude-agent", default=None, help="Agent ID to exclude from notifications")

    p = sub.add_parser("get-conflicts", parents=[shared], help="Query the conflict log")
    p.add_argument("--document-path", default=None)
    p.add_argument("--agent-id", default=None)
    p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser("contention-hotspots", parents=[shared],
                       help="Rank files by lock contention frequency")
    p.add_argument("--limit", type=int, default=10)


def _add_visibility(sub, shared) -> None:
    p = sub.add_parser("load-spec", parents=[shared], help="Reload coordination spec from disk")
    p.add_argument("path", nargs="?", default=None, help="Path to coordination spec (default: project root)")

    sub.add_parser("validate-spec", parents=[shared], help="Validate loaded coordination spec")

    p = sub.add_parser("scan-project", parents=[shared], help="Perform file ownership scan")
    p.add_argument("--worktree-root", default=None)
    p.add_argument("--extensions", nargs="+", default=None,
                   help="File extensions to scan (default: py md json yaml txt toml)")

    p = sub.add_parser("dashboard", parents=[shared], help="Print live agent/file status table")
    p.add_argument("--agent-id", default=None, help="Filter to a specific agent")
    p.add_argument("--min", action="store_true", dest="minimal",
                   help="Show only compact one-line-per-agent summary")

    p = sub.add_parser("agent-status", parents=[shared], help="Get full status for an agent")
    p.add_argument("agent_id", help="Agent to query")
    p.add_argument("--tree", action="store_true", help="Print agent tree instead of flat status")

    p = sub.add_parser("agent-tree", parents=[shared], help="Print agent hierarchy as a tree")
    p.add_argument("agent_id", nargs="?", default=None, help="Root agent (default: oldest root)")

    p = sub.add_parser("assess", parents=[shared],
                       help="Run an assessment suite or score the live session")
    p.add_argument("--suite", dest="suite_path", default=None,
                   help="Path to the JSON test suite file (omit for live session)")
    p.add_argument("--format", default="markdown", choices=["markdown", "json"],
                   help="Output format (default: markdown)")
    p.add_argument("--output", default=None, dest="output_path",
                   help="Write report to file instead of stdout")
    p.add_argument("--graph-agent-id", dest="graph_agent_id", default=None,
                   help="Filter traces to this graph agent role (e.g. planner, executor)")
    p.add_argument("--scope", default="project", choices=["project", "all"],
                   help="'project' (default) limits to current worktree; 'all' scores every agent")


def _add_setup(sub, shared) -> None:
    sub.add_parser("doctor", parents=[shared],
                   help="Validate CoordinationHub setup and diagnose issues")

    p = sub.add_parser("init", parents=[shared],
                       help="Set up CoordinationHub: create DB, configure hooks")
    p.add_argument("--auto-dashboard", action="store_true",
                   help="Also install a SessionStart hook that auto-launches the SSE dashboard "
                        "at http://127.0.0.1:9898")
    p.add_argument("--monitor-skill", action="store_true",
                   help="Also install the 'coordinationhub-monitor' skill at ~/.claude/skills/ "
                        "for LLMs to watch the swarm")

    p = sub.add_parser("auto-start-dashboard", parents=[shared],
                       help="Idempotently launch the SSE dashboard if not already running")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=9898)

    p = sub.add_parser("watch", parents=[shared], help="Live-refresh agent tree (Ctrl+C to stop)")
    p.add_argument("agent_id", nargs="?", default=None, help="Root agent (default: oldest root)")
    p.add_argument("--interval", type=int, default=5, help="Refresh interval in seconds (default: 5)")

    p = sub.add_parser("await-agent", parents=[shared], help="Wait for an agent to complete")
    p.add_argument("agent_id", help="Agent to wait for")
    p.add_argument("--timeout", type=float, default=60.0, help="Timeout in seconds (default: 60)")


def _add_messaging(sub, shared) -> None:
    p = sub.add_parser("send-message", parents=[shared], help="Send a message to another agent")
    p.add_argument("from_agent_id", help="Agent sending the message")
    p.add_argument("to_agent_id", help="Agent to receive the message")
    p.add_argument("message_type", help="Message type (e.g. query, response)")
    p.add_argument("--payload", default=None, help="JSON payload")

    p = sub.add_parser("get-messages", parents=[shared], help="Get messages for an agent")
    p.add_argument("agent_id", help="Agent to get messages for")
    p.add_argument("--unread-only", action="store_true", help="Only return unread messages")
    p.add_argument("--limit", type=int, default=50, help="Maximum messages to return (default: 50)")

    p = sub.add_parser("mark-messages-read", parents=[shared], help="Mark messages as read")
    p.add_argument("agent_id", help="Agent marking messages as read")
    p.add_argument("--message-ids", type=int, nargs="+", default=None, help="Specific message IDs to mark")


def _add_tasks(sub, shared) -> None:
    p = sub.add_parser("create-task", parents=[shared], help="Create a new task in the shared registry")
    p.add_argument("task_id", help="Unique task ID (e.g. hub.12345.0.task.0)")
    p.add_argument("parent_agent_id", help="Agent creating this task")
    p.add_argument("description", help="What this task involves")
    p.add_argument("--depends-on", nargs="+", default=None, dest="depends_on",
                   help="Task IDs that must complete first")
    p.add_argument("--priority", type=int, default=0,
                   help="Task priority (higher values execute first; default 0)")

    p = sub.add_parser("assign-task", parents=[shared], help="Assign a task to an agent")
    p.add_argument("task_id", help="Task to assign")
    p.add_argument("assigned_agent_id", help="Agent to assign the task to")

    p = sub.add_parser("update-task-status", parents=[shared], help="Update a task's status")
    p.add_argument("task_id", help="Task to update")
    p.add_argument("status", choices=["pending", "in_progress", "completed", "blocked", "failed"],
                   help="New status")
    p.add_argument("--summary", default=None, help="Completion summary (used for compression chains)")
    p.add_argument("--blocked-by", default=None, dest="blocked_by",
                   help="Task ID blocking this task")
    p.add_argument("--error", default=None, help="Error message (records to dead letter queue)")

    p = sub.add_parser("query-tasks", parents=[shared], help="Unified task query")
    p.add_argument("query_type", choices=["task", "child", "by_agent", "all", "subtasks", "tree"],
                   help="Query type")
    p.add_argument("--task-id", default=None, help="Task ID (for query_type='task')")
    p.add_argument("--parent-agent-id", default=None, help="Agent ID (for query_type='child')")
    p.add_argument("--assigned-agent-id", default=None, help="Agent ID (for query_type='by_agent')")
    p.add_argument("--parent-task-id", default=None, help="Parent task ID (for query_type='subtasks')")
    p.add_argument("--root-task-id", default=None, help="Root task ID (for query_type='tree')")

    p = sub.add_parser("create-subtask", parents=[shared],
                       help="Create a subtask under an existing parent task")
    p.add_argument("task_id", help="Unique subtask ID (e.g. parent_task_id.0)")
    p.add_argument("parent_task_id", help="ID of the parent task this subtask belongs to")
    p.add_argument("parent_agent_id", help="Agent creating this subtask")
    p.add_argument("description", help="What this subtask involves")
    p.add_argument("--depends-on", nargs="+", default=None, dest="depends_on",
                   help="Task IDs that must complete first")
    p.add_argument("--priority", type=int, default=0,
                   help="Subtask priority (higher values execute first; default 0)")

    p = sub.add_parser("retry-task", parents=[shared], help="Retry a task from the dead letter queue")
    p.add_argument("task_id", help="Task ID to retry from the dead letter queue")

    p = sub.add_parser("dead-letter-queue", parents=[shared],
                       help="Get all tasks in the dead letter queue")
    p.add_argument("--limit", type=int, default=50,
                   help="Maximum number of tasks to return (default 50)")

    p = sub.add_parser("task-failure-history", parents=[shared], help="Get failure history for a task")
    p.add_argument("task_id", help="Task ID whose failure history to retrieve")

    p = sub.add_parser("wait-for-task", parents=[shared],
                       help="Poll until a task reaches terminal state or timeout expires")
    p.add_argument("task_id", help="Task ID to wait on")
    p.add_argument("--timeout", type=float, default=60.0, help="Timeout in seconds (default: 60)")
    p.add_argument("--poll-interval", type=float, default=2.0, dest="poll_interval",
                   help="Polling interval in seconds (default: 2)")

    p = sub.add_parser("get-available-tasks", parents=[shared],
                       help="Get tasks whose dependencies are all satisfied and are not claimed")
    p.add_argument("--agent-id", default=None, dest="agent_id",
                   help="Filter to tasks assigned to a specific agent")


def _add_intent_and_deps(sub, shared) -> None:
    p = sub.add_parser("declare-work-intent", parents=[shared], help="Declare intent to work on a file")
    p.add_argument("agent_id", help="Agent declaring intent")
    p.add_argument("document_path", help="File path the agent intends to work on")
    p.add_argument("intent", help="Short description of work intent")
    p.add_argument("--ttl", type=float, default=60.0, help="Seconds until intent expires (default: 60)")

    p = sub.add_parser("get-work-intents", parents=[shared], help="Get all active work intents")
    p.add_argument("--agent-id", default=None, dest="agent_id", help="Filter to a specific agent")

    p = sub.add_parser("clear-work-intent", parents=[shared], help="Clear an agent's declared work intent")
    p.add_argument("agent_id", help="Agent whose intent to clear")

    p = sub.add_parser("declare-dependency", parents=[shared], help="Declare a dependency between agents")
    p.add_argument("dependent_agent_id", help="Agent that depends on another")
    p.add_argument("depends_on_agent_id", help="Agent being depended upon")
    p.add_argument("--depends-on-task-id", default=None, dest="depends_on_task_id",
                   help="Specific task ID that must complete")
    p.add_argument("--condition", default="task_completed",
                   choices=["task_completed", "agent_registered", "agent_stopped"],
                   help="Condition for dependency satisfaction (default: task_completed)")

    p = sub.add_parser("manage-dependencies", parents=[shared], help="Unified dependency query")
    p.add_argument("mode", choices=["check", "blockers", "assert"], help="Query mode")
    p.add_argument("agent_id", help="Agent whose dependencies to check")

    p = sub.add_parser("satisfy-dependency", parents=[shared], help="Mark a dependency as satisfied")
    p.add_argument("dep_id", type=int, help="Dependency ID to mark as satisfied")

    p = sub.add_parser("get-all-dependencies", parents=[shared], help="List all declared dependencies")
    p.add_argument("--dependent-agent-id", default=None, dest="dependent_agent_id",
                   help="Filter by dependent agent")


def _add_leases(sub, shared) -> None:
    p = sub.add_parser("acquire-coordinator-lease", parents=[shared],
                       help="Attempt to acquire the coordinator leadership lease")
    p.add_argument("agent_id", help="Agent ID attempting to acquire the lease")
    p.add_argument("--ttl", type=float, default=None, help="Lease TTL in seconds (default: 10)")

    p = sub.add_parser("refresh-coordinator-lease", parents=[shared],
                       help="Refresh the coordinator lease TTL")
    p.add_argument("agent_id", help="Agent ID refreshing the lease")

    p = sub.add_parser("release-coordinator-lease", parents=[shared],
                       help="Release the coordinator leadership lease")
    p.add_argument("agent_id", help="Agent ID releasing the lease")

    sub.add_parser("get-leader", parents=[shared],
                   help="Print the current coordinator lease holder")

    p = sub.add_parser("claim-leadership", parents=[shared],
                       help="Claim coordinator leadership from a failed leader")
    p.add_argument("agent_id", help="Agent ID attempting to claim leadership")
    p.add_argument("--ttl", type=float, default=None, help="Lease TTL in seconds (default: 10)")

    sub.add_parser("leader-status", parents=[shared],
                   help="Print coordinator leader and lease details")

    p = sub.add_parser("ha-dashboard", parents=[shared],
                       help="HA dashboard: lease state, replica count, agent status")
    p.add_argument("--agent-id", default=None)


def _add_spawner(sub, shared) -> None:
    p = sub.add_parser("spawn-subagent", parents=[shared],
                       help="Register intent to spawn a sub-agent")
    p.add_argument("parent_agent_id", help="Parent agent ID that is spawning")
    p.add_argument("subagent_type", help="Type of sub-agent to spawn (e.g. Explore, Plan)")
    p.add_argument("--description", default=None, help="Description of the sub-agent's task")
    p.add_argument("--prompt", default=None, help="Prompt or instructions for the sub-agent")
    p.add_argument("--source", default="external", help="Spawning system (e.g. claude_code, kimi_cli)")

    p = sub.add_parser("report-subagent-spawned", parents=[shared],
                       help="Report that a sub-agent was spawned by an external system")
    p.add_argument("parent_agent_id", help="Parent agent ID that spawned the sub-agent")
    p.add_argument("child_agent_id", help="Actual agent ID of the spawned sub-agent")
    p.add_argument("--subagent-type", default=None, help="Type of sub-agent that was spawned")
    p.add_argument("--source", default="external", help="Spawning system (e.g. claude_code, kimi_cli)")

    p = sub.add_parser("list-pending-spawns", parents=[shared],
                       help="List pending sub-agent spawn requests for a parent agent")
    p.add_argument("parent_agent_id", help="Parent agent ID to query")
    p.add_argument("--all", action="store_true", dest="include_consumed",
                   help="Include registered/expired spawns")

    p = sub.add_parser("cancel-spawn", parents=[shared], help="Cancel a pending spawn request")
    p.add_argument("spawn_id", help="Spawn ID to cancel")

    p = sub.add_parser("request-subagent-deregistration", parents=[shared],
                       help="Request graceful deregistration of a child agent")
    p.add_argument("parent_agent_id", help="Parent agent ID making the request")
    p.add_argument("child_agent_id", help="Child agent ID to request stop for")

    p = sub.add_parser("await-subagent-stopped", parents=[shared],
                       help="Poll until a child agent is stopped or timeout")
    p.add_argument("child_agent_id", help="Child agent ID to wait for")
    p.add_argument("--timeout", type=float, default=30.0, help="Timeout in seconds (default: 30)")


# ------------------------------------------------------------------ #
# Top-level builder
# ------------------------------------------------------------------ #

def create_parser() -> argparse.ArgumentParser:
    shared = _make_shared()
    parser = argparse.ArgumentParser(
        prog="coordinationhub",
        description="CoordinationHub — declarative multi-agent coordination",
    )
    sub = parser.add_subparsers(dest="command")

    _add_serve(sub, shared)
    _add_identity(sub, shared)
    _add_locking(sub, shared)
    _add_broadcast_handoff(sub, shared)
    _add_notifications(sub, shared)
    _add_visibility(sub, shared)
    _add_setup(sub, shared)
    _add_messaging(sub, shared)
    _add_tasks(sub, shared)
    _add_intent_and_deps(sub, shared)
    _add_leases(sub, shared)
    _add_spawner(sub, shared)

    return parser
