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
    "get_agent_relations": ("get_agent_relations", ["agent_id", "mode"]),
    # Locking
    "acquire_lock": ("acquire_lock", ["document_path", "agent_id", "lock_type", "ttl", "force", "region_start", "region_end", "retry", "max_retries", "backoff_ms", "timeout_ms"]),
    "release_lock": ("release_lock", ["document_path", "agent_id", "region_start", "region_end"]),
    "refresh_lock": ("refresh_lock", ["document_path", "agent_id", "ttl", "region_start", "region_end"]),
    "get_lock_status": ("get_lock_status", ["document_path"]),
    "list_locks": ("list_locks", ["agent_id"]),
    "admin_locks": ("admin_locks", ["action", "agent_id", "grace_seconds", "timeout"]),
    # Coordination
    "broadcast": ("broadcast", ["agent_id", "document_path", "ttl", "handoff_targets", "require_ack", "message"]),
    "acknowledge_broadcast": ("acknowledge_broadcast", ["broadcast_id", "agent_id"]),
    "wait_for_broadcast_acks": ("wait_for_broadcast_acks", ["broadcast_id", "timeout_s"]),
    "wait_for_locks": ("wait_for_locks", ["document_paths", "agent_id", "timeout_s"]),
    "await_agent": ("await_agent", ["agent_id", "timeout_s"]),
    # Change awareness
    "notify_change": ("notify_change", ["document_path", "change_type", "agent_id"]),
    "get_notifications": ("get_notifications", ["since", "exclude_agent", "limit"]),
    "prune_notifications": ("prune_notifications", ["max_age_seconds", "max_entries"]),
    "wait_for_notifications": ("wait_for_notifications", ["agent_id", "timeout_s", "poll_interval_s", "exclude_agent"]),
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
    "run_assessment": ("run_assessment", ["suite_path", "format", "graph_agent_id", "scope"]),
    # Messaging
    "send_message": ("send_message", ["from_agent_id", "to_agent_id", "message_type", "payload"]),
    "get_messages": ("get_messages", ["agent_id", "unread_only", "limit"]),
    "mark_messages_read": ("mark_messages_read", ["agent_id", "message_ids"]),
    # Task Registry
    "create_task": ("create_task", ["task_id", "parent_agent_id", "description", "depends_on", "priority"]),
    "assign_task": ("assign_task", ["task_id", "assigned_agent_id"]),
    "update_task_status": ("update_task_status", ["task_id", "status", "summary", "blocked_by", "error"]),
    "query_tasks": ("query_tasks", ["query_type", "task_id", "parent_agent_id", "assigned_agent_id", "parent_task_id", "root_task_id"]),
    "create_subtask": ("create_subtask", ["task_id", "parent_task_id", "parent_agent_id", "description", "depends_on", "priority"]),
    "wait_for_task": ("wait_for_task", ["task_id", "timeout_s", "poll_interval_s"]),
    "get_available_tasks": ("get_available_tasks", ["agent_id"]),
    # Dead Letter Queue
    "retry_task": ("retry_task", ["task_id"]),
    "get_dead_letter_tasks": ("get_dead_letter_tasks", ["limit"]),
    "get_task_failure_history": ("get_task_failure_history", ["task_id"]),
    # Work Intent Board
    "declare_work_intent": ("declare_work_intent", ["agent_id", "document_path", "intent", "ttl"]),
    "get_work_intents": ("get_work_intents", ["agent_id"]),
    "clear_work_intent": ("clear_work_intent", ["agent_id"]),
    # Handoffs
    "acknowledge_handoff": ("acknowledge_handoff", ["handoff_id", "agent_id"]),
    "complete_handoff": ("complete_handoff", ["handoff_id"]),
    "cancel_handoff": ("cancel_handoff", ["handoff_id"]),
    "get_handoffs": ("get_handoffs", ["status", "from_agent_id", "limit"]),
    "wait_for_handoff": ("wait_for_handoff", ["handoff_id", "timeout_s"]),
    # Dependencies
    "declare_dependency": ("declare_dependency", ["dependent_agent_id", "depends_on_agent_id",
                                                    "depends_on_task_id", "condition"]),
    "manage_dependencies": ("manage_dependencies", ["mode", "agent_id"]),
    "satisfy_dependency": ("satisfy_dependency", ["dep_id"]),
    "get_all_dependencies": ("get_all_dependencies", ["dependent_agent_id"]),
    # Leases — HA Coordinator Leadership
    "acquire_coordinator_lease": ("acquire_coordinator_lease", ["agent_id", "ttl"]),
    "refresh_coordinator_lease": ("refresh_coordinator_lease", ["agent_id"]),
    "release_coordinator_lease": ("release_coordinator_lease", ["agent_id"]),
    "get_leader": ("get_leader", []),
    "claim_leadership": ("claim_leadership", ["agent_id", "ttl"]),
    # Spawner — Sub-Agent Registry
    "spawn_subagent": ("spawn_subagent", ["parent_agent_id", "subagent_type", "description", "prompt", "source"]),
    "report_subagent_spawned": ("report_subagent_spawned", ["parent_agent_id", "subagent_type", "child_agent_id", "source"]),
    "get_pending_spawns": ("get_pending_spawns", ["parent_agent_id", "include_consumed"]),
    "await_subagent_registration": ("await_subagent_registration", ["parent_agent_id", "subagent_type", "timeout"]),
    "request_subagent_deregistration": ("request_subagent_deregistration", ["parent_agent_id", "child_agent_id"]),
    "is_subagent_stop_requested": ("is_subagent_stop_requested", ["agent_id"]),
    "await_subagent_stopped": ("await_subagent_stopped", ["child_agent_id", "timeout"]),
}
