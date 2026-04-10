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
    "acquire_lock": ("acquire_lock", ["document_path", "agent_id", "lock_type", "ttl", "force", "region_start", "region_end"]),
    "release_lock": ("release_lock", ["document_path", "agent_id", "region_start", "region_end"]),
    "refresh_lock": ("refresh_lock", ["document_path", "agent_id", "ttl", "region_start", "region_end"]),
    "get_lock_status": ("get_lock_status", ["document_path"]),
    "list_locks": ("list_locks", ["agent_id"]),
    "release_agent_locks": ("release_agent_locks", ["agent_id"]),
    "reap_expired_locks": ("reap_expired_locks", []),
    "reap_stale_agents": ("reap_stale_agents", ["timeout"]),
    # Coordination
    "broadcast": ("broadcast", ["agent_id", "document_path", "ttl"]),
    "wait_for_locks": ("wait_for_locks", ["document_paths", "agent_id", "timeout_s"]),
    # Change awareness
    "notify_change": ("notify_change", ["document_path", "change_type", "agent_id"]),
    "get_notifications": ("get_notifications", ["since", "exclude_agent", "limit"]),
    "prune_notifications": ("prune_notifications", ["max_age_seconds", "max_entries"]),
    # Audit
    "get_conflicts": ("get_conflicts", ["document_path", "agent_id", "limit"]),
    # Status
    "status": ("status", []),
    # Graph & Visibility
    "load_coordination_spec": ("load_coordination_spec", ["path"]),
    "validate_graph": ("validate_graph", []),
    "scan_project": ("scan_project", ["worktree_root", "extensions"]),
    "get_agent_status": ("get_agent_status", ["agent_id"]),
    "get_file_agent_map": ("get_file_agent_map", ["agent_id"]),
    "update_agent_status": ("update_agent_status", ["agent_id", "current_task"]),
    "get_agent_tree": ("get_agent_tree", ["agent_id"]),
    "run_assessment": ("run_assessment", ["suite_path", "format", "graph_agent_id"]),
}
