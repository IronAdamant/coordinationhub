"""Tool dispatch for CoordinationHub.

Holds both the dispatch *table* (``TOOL_DISPATCH``) and the dispatch
*function* (``dispatch_tool``). Shared by both HTTP and stdio
transports. T7.42: the function used to live in ``mcp_server`` which
was the wrong home — transport code should not own the tool-dispatch
logic. A back-compat re-export is retained on ``mcp_server`` so
existing callers keep working.
"""

from __future__ import annotations

import logging
from typing import Any


_logger = logging.getLogger(__name__)

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
    "list_locks": ("list_locks", ["agent_id", "force_refresh"]),
    "admin_locks": ("admin_locks", ["action", "agent_id", "grace_seconds", "timeout"]),
    # Coordination
    "broadcast": ("broadcast", ["agent_id", "document_path", "ttl", "handoff_targets", "require_ack", "message"]),
    "acknowledge_broadcast": ("acknowledge_broadcast", ["broadcast_id", "agent_id"]),
    "wait_for_broadcast_acks": ("wait_for_broadcast_acks", ["broadcast_id", "timeout_s"]),
    "wait_for_locks": ("wait_for_locks", ["document_paths", "agent_id", "timeout_s"]),
    "await_agent": ("await_agent", ["agent_id", "timeout_s"]),
    # Change awareness
    "notify_change": ("notify_change", ["document_path", "change_type", "agent_id"]),
    "get_notifications": ("get_notifications", ["since", "exclude_agent", "limit", "agent_id", "timeout_s", "poll_interval_s", "prune_max_age_seconds", "prune_max_entries"]),

    # Audit
    "get_conflicts": ("get_conflicts", ["document_path", "agent_id", "limit"]),
    "get_contention_hotspots": ("get_contention_hotspots", ["limit"]),
    # Status
    "status": ("status", []),
    # Graph & Visibility
    "load_coordination_spec": ("load_coordination_spec", ["path"]),

    "scan_project": ("scan_project", ["worktree_root", "extensions"]),
    "get_agent_status": ("get_agent_status", ["agent_id"]),
    "get_file_agent_map": ("get_file_agent_map", ["agent_id"]),
    "update_agent_status": ("update_agent_status", ["agent_id", "current_task", "scope"]),
    "get_agent_tree": ("get_agent_tree", ["agent_id"]),
    "run_assessment": ("run_assessment", ["suite_path", "format", "graph_agent_id", "scope"]),
    # Messaging
    "send_message": ("send_message", ["from_agent_id", "to_agent_id", "message_type", "payload"]),
    "manage_messages": ("manage_messages", ["action", "agent_id", "from_agent_id", "to_agent_id", "message_type", "payload", "unread_only", "limit", "message_ids", "since_id"]),
    # Task Registry
    "create_task": ("create_task", ["task_id", "parent_agent_id", "description", "depends_on", "priority"]),
    "assign_task": ("assign_task", ["task_id", "assigned_agent_id"]),
    "update_task_status": ("update_task_status", ["task_id", "status", "summary", "blocked_by", "error"]),
    "query_tasks": ("query_tasks", ["query_type", "task_id", "parent_agent_id", "assigned_agent_id", "parent_task_id", "root_task_id"]),
    "create_subtask": ("create_subtask", ["task_id", "parent_task_id", "parent_agent_id", "description", "depends_on", "priority"]),
    "wait_for_task": ("wait_for_task", ["task_id", "timeout_s", "poll_interval_s"]),
    "get_available_tasks": ("get_available_tasks", ["agent_id"]),
    # Dead Letter Queue
    "task_failures": ("task_failures", ["action", "task_id", "limit"]),
    # Work Intent Board
    "manage_work_intents": ("manage_work_intents", ["action", "agent_id", "document_path", "intent", "ttl"]),
    # Handoffs
    "wait_for_handoff": ("wait_for_handoff", ["handoff_id", "timeout_s", "agent_id", "mode"]),
    # Dependencies
    "manage_dependencies": ("manage_dependencies", ["mode", "agent_id", "dependent_agent_id", "depends_on_agent_id", "depends_on_task_id", "condition", "dep_id", "timeout_s", "poll_interval_s"]),
    # Leases — HA Coordinator Leadership
    "acquire_coordinator_lease": ("acquire_coordinator_lease", ["agent_id", "ttl"]),
    "manage_leases": ("manage_leases", ["action", "agent_id", "ttl"]),
    # Spawner — Sub-Agent Registry
    "spawn_subagent": ("spawn_subagent", ["parent_agent_id", "subagent_type", "description", "prompt", "source"]),
    "report_subagent_spawned": ("report_subagent_spawned", ["parent_agent_id", "subagent_type", "child_agent_id", "source"]),
    "get_pending_spawns": ("get_pending_spawns", ["parent_agent_id", "include_consumed"]),
    "await_subagent_registration": ("await_subagent_registration", ["parent_agent_id", "subagent_type", "timeout"]),
    "request_subagent_deregistration": ("request_subagent_deregistration", ["parent_agent_id", "child_agent_id"]),
    "is_subagent_stop_requested": ("is_subagent_stop_requested", ["agent_id"]),
    "await_subagent_stopped": ("await_subagent_stopped", ["child_agent_id", "timeout"]),
}


def dispatch_tool(engine, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a tool call to the appropriate engine method.

    Shared by the HTTP and stdio MCP servers so dispatch logic is not
    duplicated. Raises ``ValueError`` for unknown tools or schema
    violations, ``TypeError`` for engine-level signature mismatches.

    T3.5: explicit ``None`` values used to be stripped along with
    missing keys, causing spurious "missing required argument" errors
    for tools whose signature genuinely accepts ``None``
    (e.g. ``report_subagent_spawned(subagent_type: str | None)``).
    Now preserves ``None`` and lets the callee decide.

    T6.11: ``arguments`` are validated against ``TOOL_SCHEMAS[tool_name]
    ["parameters"]`` before dispatch. Pre-fix, schemas were
    display-only — a string where an integer was expected, an unknown
    ``action`` value, or a maxLength-busting prompt would pass through
    the boundary and either corrupt DB state or raise an opaque error
    deep in a primitive. Validation is skipped when the schema is
    absent (unknown-in-tools-version edge).

    Unknown keys are logged at WARNING so callers notice typos (e.g.
    ``agent_ids`` vs ``agent_id``) instead of silently receiving
    "missing required argument" later.
    """
    if tool_name not in TOOL_DISPATCH:
        raise ValueError(
            f"Unknown tool: {tool_name!r}. Available: {sorted(TOOL_DISPATCH)}"
        )

    # T6.11: schema validation gate. Imported lazily so dispatch.py
    # stays importable in minimal environments (e.g. the schema package
    # depends on it transitively through nothing we wire here, but we
    # prefer late binding for any future isolation need).
    from . import validation as _validation
    from .schemas import TOOL_SCHEMAS

    schema = TOOL_SCHEMAS.get(tool_name)
    if schema is not None:
        params_schema = schema.get("parameters")
        if params_schema is not None:
            # Raises ValidationError (subclass of ValueError) on
            # type/required/enum/bound violations.
            _validation.validate_tool_arguments(
                tool_name, arguments, params_schema,
            )

    method_name, allowed_args = TOOL_DISPATCH[tool_name]
    unknown = set(arguments) - set(allowed_args)
    if unknown:
        _logger.warning(
            "tool %r called with unknown argument(s) %s; allowed=%s",
            tool_name, sorted(unknown), sorted(allowed_args),
        )
    # T3.5: keep explicit None so the callee can distinguish "field
    # intentionally unset" from "field missing entirely".
    kwargs = {k: v for k, v in arguments.items() if k in allowed_args}
    return getattr(engine, method_name)(**kwargs)
