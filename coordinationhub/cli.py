"""CoordinationHub CLI — command-line interface for all coordination tool methods.

Parser construction lives in :mod:`cli_parser`; command handlers live in
:mod:`cli_commands` (which re-exports from the ``cli_*`` sub-modules).
This module is just the entry point and the dispatch table.
"""

from __future__ import annotations

import json
import sys

from .cli_parser import create_parser

__all__ = ["create_parser", "main"]


_COMMANDS = {
    "serve": "cmd_serve", "serve-mcp": "cmd_serve_mcp", "serve-sse": "cmd_serve_sse",
    "status": "cmd_status",
    "register": "cmd_register", "heartbeat": "cmd_heartbeat", "deregister": "cmd_deregister",
    "list-agents": "cmd_list_agents", "agent-relations": "cmd_agent_relations",
    "acquire-lock": "cmd_acquire_lock", "release-lock": "cmd_release_lock",
    "refresh-lock": "cmd_refresh_lock", "lock-status": "cmd_lock_status",
    "list-locks": "cmd_list_locks",
    "admin-locks": "cmd_admin_locks", "broadcast": "cmd_broadcast",
    "acknowledge-broadcast": "cmd_acknowledge_broadcast",
    "wait-for-broadcast-acks": "cmd_wait_for_broadcast_acks",
    "acknowledge-handoff": "cmd_acknowledge_handoff",
    "complete-handoff": "cmd_complete_handoff",
    "cancel-handoff": "cmd_cancel_handoff",
    "get-handoffs": "cmd_get_handoffs",
    "wait-for-handoff": "cmd_wait_for_handoff",
    "wait-for-locks": "cmd_wait_for_locks", "notify-change": "cmd_notify_change",
    "get-notifications": "cmd_get_notifications", "prune-notifications": "cmd_prune_notifications",
    "wait-for-notifications": "cmd_wait_for_notifications",
    "get-conflicts": "cmd_get_conflicts", "contention-hotspots": "cmd_contention_hotspots",
    "load-spec": "cmd_load_spec", "validate-spec": "cmd_validate_spec",
    "scan-project": "cmd_scan_project", "dashboard": "cmd_dashboard",
    "agent-status": "cmd_agent_status", "assess": "cmd_assess",
    "agent-tree": "cmd_agent_tree",
    "doctor": "cmd_doctor",
    "init": "cmd_init",
    "auto-start-dashboard": "cmd_auto_start_dashboard",
    "watch": "cmd_watch",
    "await-agent": "cmd_await_agent",
    "send-message": "cmd_send_message",
    "get-messages": "cmd_get_messages",
    "mark-messages-read": "cmd_mark_messages_read",
    "create-task": "cmd_create_task",
    "assign-task": "cmd_assign_task",
    "update-task-status": "cmd_update_task_status",
    "query-tasks": "cmd_query_tasks",
    "create-subtask": "cmd_create_subtask",
    "declare-work-intent": "cmd_declare_work_intent",
    "get-work-intents": "cmd_get_work_intents",
    "clear-work-intent": "cmd_clear_work_intent",
    "declare-dependency": "cmd_declare_dependency",
    "manage-dependencies": "cmd_manage_dependencies",
    "satisfy-dependency": "cmd_satisfy_dependency",
    "get-all-dependencies": "cmd_get_all_dependencies",
    "retry-task": "cmd_retry_task",
    "dead-letter-queue": "cmd_dead_letter_queue",
    "task-failure-history": "cmd_task_failure_history",
    "wait-for-task": "cmd_wait_for_task",
    "get-available-tasks": "cmd_get_available_tasks",
    "acquire-coordinator-lease": "cmd_acquire_coordinator_lease",
    "refresh-coordinator-lease": "cmd_refresh_coordinator_lease",
    "release-coordinator-lease": "cmd_release_coordinator_lease",
    "get-leader": "cmd_get_leader",
    "claim-leadership": "cmd_claim_leadership",
    "leader-status": "cmd_leader_status",
    "ha-dashboard": "cmd_ha_dashboard",
    "spawn-subagent": "cmd_spawn_subagent",
    "report-subagent-spawned": "cmd_report_subagent_spawned",
    "list-pending-spawns": "cmd_list_pending_spawns",
    "cancel-spawn": "cmd_cancel_spawn",
    "request-subagent-deregistration": "cmd_request_subagent_deregistration",
    "await-subagent-stopped": "cmd_await_subagent_stopped",
}


def _get_handler(name: str):
    from . import cli_commands as _cmds
    return getattr(_cmds, name)


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
        rc = handler(args)
        # T3.16: handlers that return an explicit int propagate it as
        # the exit code; otherwise a plain success is 0. This lets
        # individual handlers signal "not found" → 3 or "conflict" → 4
        # without having to raise.
        if isinstance(rc, int):
            return rc
        return 0
    except SystemExit:
        raise
    except Exception as exc:
        if getattr(args, "json_output", False):
            print(json.dumps({"error": str(exc)}, indent=2))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
