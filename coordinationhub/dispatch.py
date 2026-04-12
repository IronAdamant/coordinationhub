"""Tool dispatch table for CoordinationHub.

Maps tool_name -> (engine_method_name, allowed_kwargs).
Shared by both HTTP and stdio transports. Zero internal dependencies.
"""

from __future__ import annotations

# ------------------------------------------------------------------ #
# Dispatch table: tool_name -> (engine_method_name, allowed_kwargs)
# ------------------------------------------------------------------ #

TOOL_DISPATCH: dict[str, tuple[str, list[str]]] = {
    # Identity
    "register_agent": ("register_agent", ["agent_id", "parent_id", "graph_agent_id", "worktree_root"]),
    "heartbeat": ("heartbeat", ["agent_id"]),
    "deregister_agent": ("deregister_agent", ["agent_id"]),
    "list_agents": ("list_agents", ["active_only", "stale_timeout"]),
    "get_lineage": ("get_lineage", ["agent_id"]),
    "get_siblings": ("get_siblings", ["agent_id"]),
    # Locking
    "acquire_lock": ("acquire_lock", ["document_path", "agent_id", "lock_type", "ttl", "force", "region_start", "region_end", "retry", "max_retries", "backoff_ms", "timeout_ms"]),
    "release_lock": ("release_lock", ["document_path", "agent_id", "region_start", "region_end"]),
    "refresh_lock": ("refresh_lock", ["document_path", "agent_id", "ttl", "region_start", "region_end"]),
    "get_lock_status": ("get_lock_status", ["document_path"]),
    "list_locks": ("list_locks", ["agent_id"]),
    "release_agent_locks": ("release_agent_locks", ["agent_id"]),
    "reap_expired_locks": ("reap_expired_locks", []),
    "reap_stale_agents": ("reap_stale_agents", ["timeout"]),
    # Coordination
    "broadcast": ("broadcast", ["agent_id", "document_path", "ttl", "handoff_targets"]),
    "wait_for_locks": ("wait_for_locks", ["document_paths", "agent_id", "timeout_s"]),
    "await_agent": ("await_agent", ["agent_id", "timeout_s"]),
    # Change awareness
    "notify_change": ("notify_change", ["document_path", "change_type", "agent_id"]),
    "get_notifications": ("get_notifications", ["since", "exclude_agent", "limit"]),
    "prune_notifications": ("prune_notifications", ["max_age_seconds", "max_entries"]),
    # Audit
    "get_conflicts": ("get_conflicts", ["document_path", "agent_id", "limit"]),
    "get_contention_hotspots": ("get_contention_hotspots", ["limit"]),
    # Status
    "status": ("status", []),
    # Graph & Visibility
    "load_coordination_spec": ("load_coordination_spec", ["path"]),
    "validate_graph": ("validate_graph", []),
    "scan_project": ("scan_project", ["worktree_root", "extensions"]),
    "get_agent_status": ("get_agent_status", ["agent_id"]),
    "get_file_agent_map": ("get_file_agent_map", ["agent_id"]),
    "update_agent_status": ("update_agent_status", ["agent_id", "current_task", "scope"]),
    "get_agent_tree": ("get_agent_tree", ["agent_id"]),
    "run_assessment": ("run_assessment", ["suite_path", "format", "graph_agent_id"]),
    "assess_current_session": ("assess_current_session", ["format", "graph_agent_id", "scope"]),
    # Messaging
    "send_message": ("send_message", ["from_agent_id", "to_agent_id", "message_type", "payload"]),
    "get_messages": ("get_messages", ["agent_id", "unread_only", "limit"]),
    "mark_messages_read": ("mark_messages_read", ["agent_id", "message_ids"]),
    # Task Registry
    "create_task": ("create_task", ["task_id", "parent_agent_id", "description", "depends_on"]),
    "assign_task": ("assign_task", ["task_id", "assigned_agent_id"]),
    "update_task_status": ("update_task_status", ["task_id", "status", "summary", "blocked_by"]),
    "get_task": ("get_task", ["task_id"]),
    "get_child_tasks": ("get_child_tasks", ["parent_agent_id"]),
    "get_tasks_by_agent": ("get_tasks_by_agent", ["assigned_agent_id"]),
    "get_all_tasks": ("get_all_tasks", []),
    # Work Intent Board
    "declare_work_intent": ("declare_work_intent", ["agent_id", "document_path", "intent", "ttl"]),
    "get_work_intents": ("get_work_intents", ["agent_id"]),
    "clear_work_intent": ("clear_work_intent", ["agent_id"]),
    # Handoffs
    "acknowledge_handoff": ("acknowledge_handoff", ["handoff_id", "agent_id"]),
    "complete_handoff": ("complete_handoff", ["handoff_id"]),
    "cancel_handoff": ("cancel_handoff", ["handoff_id"]),
    "get_handoffs": ("get_handoffs", ["status", "from_agent_id", "limit"]),
    # Dependencies
    "declare_dependency": ("declare_dependency", ["dependent_agent_id", "depends_on_agent_id",
                                                    "depends_on_task_id", "condition"]),
    "check_dependencies": ("check_dependencies", ["agent_id"]),
    "satisfy_dependency": ("satisfy_dependency", ["dep_id"]),
    "get_blockers": ("get_blockers", ["agent_id"]),
    "assert_can_start": ("assert_can_start", ["agent_id"]),
    "get_all_dependencies": ("get_all_dependencies", ["dependent_agent_id"]),
}
