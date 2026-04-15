"""Tool schemas for CoordinationHub — all 50 MCP tools.

Organized by functional group for navigation.  These are pure data
declarations with no logic.
"""

from __future__ import annotations


# ------------------------------------------------------------------ #
# Identity & Registration (6 tools)
# ------------------------------------------------------------------ #

TOOL_SCHEMAS_IDENTITY: dict[str, dict] = {
    "register_agent": {
        "description": (
            "Register an agent with the coordination hub and receive a context bundle "
            "containing sibling agents, active locks, coordination URLs, and (if a "
            "coordination graph is loaded) the agent's responsibilities and owned files."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Unique agent identifier (e.g. hub.12345.0)",
                },
                "parent_id": {
                    "type": "string",
                    "description": "Parent agent ID if this is a spawned sub-agent",
                    "default": None,
                },
                "graph_agent_id": {
                    "type": "string",
                    "description": "ID in the coordination graph this agent implements (e.g. 'planner')",
                    "default": None,
                },
                "worktree_root": {
                    "type": "string",
                    "description": "Worktree root path (defaults to project root)",
                    "default": None,
                },
            },
            "required": ["agent_id"],
        },
    },
    "heartbeat": {
        "description": (
            "Send a heartbeat to keep the agent registered and alive. "
            "Call at least every 30 seconds."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent identifier",
                },
            },
            "required": ["agent_id"],
        },
    },
    "deregister_agent": {
        "description": (
            "Deregister an agent, orphan its children to the grandparent, "
            "and release all its locks. Use when an agent is done."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent identifier to deregister",
                },
            },
            "required": ["agent_id"],
        },
    },
    "list_agents": {
        "description": (
            "List all registered agents. Shows active agents by default. "
            "Includes heartbeat age so you can detect stale agents."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "active_only": {
                    "type": "boolean",
                    "description": "Filter to active (non-stopped) agents only",
                    "default": True,
                },
                "stale_timeout": {
                    "type": "number",
                    "description": "Seconds after which an agent is considered stale",
                    "default": 600.0,
                },
            },
        },
    },
    "get_agent_relations": {
        "description": (
            "Get the ancestor chain and descendants (mode='lineage') or "
            "agents that share the same parent (mode='siblings') for a given agent."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent to query",
                },
                "mode": {
                    "type": "string",
                    "description": "'lineage' (default) or 'siblings'",
                    "default": "lineage",
                },
            },
            "required": ["agent_id"],
        },
    },
}


# ------------------------------------------------------------------ #
# Document Locking (8 tools)
# ------------------------------------------------------------------ #

_REGION_PROPS = {
    "region_start": {
        "type": "integer",
        "description": "Start line of the region to lock (omit for whole-file lock)",
    },
    "region_end": {
        "type": "integer",
        "description": "End line of the region to lock (omit for whole-file lock)",
    },
}

TOOL_SCHEMAS_LOCKING: dict[str, dict] = {
    "acquire_lock": {
        "description": (
            "Acquire an exclusive or shared lock on a document path or region. "
            "Supports region locking: provide region_start/region_end (line numbers) "
            "to lock a specific range instead of the whole file. Shared locks allow "
            "concurrent access; exclusive locks block all other locks on overlapping regions. "
            "If the lock is already held by another agent and not expired, "
            "returns conflict info unless force=True (which steals the lock "
            "and records a conflict). Use before writing any shared file. "
            "When retry=True, uses exponential backoff to wait for lock release."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "document_path": {
                    "type": "string",
                    "description": "Path to the document to lock",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Agent requesting the lock",
                },
                "lock_type": {
                    "type": "string",
                    "description": "'exclusive' (default) blocks all other locks on overlapping regions. 'shared' allows concurrent shared locks.",
                    "default": "exclusive",
                },
                "ttl": {
                    "type": "number",
                    "description": "Lock time-to-live in seconds (default: 300)",
                    "default": 300.0,
                },
                "force": {
                    "type": "boolean",
                    "description": "Steal the lock if held by another agent",
                    "default": False,
                },
                "retry": {
                    "type": "boolean",
                    "description": "Retry with exponential backoff on lock contention (default: False)",
                    "default": False,
                },
                "max_retries": {
                    "type": "integer",
                    "description": "Maximum retry attempts when retry=True (default: 5)",
                    "default": 5,
                },
                "backoff_ms": {
                    "type": "number",
                    "description": "Starting backoff in milliseconds for retry (default: 100)",
                    "default": 100.0,
                },
                "timeout_ms": {
                    "type": "number",
                    "description": "Total timeout in milliseconds for retry (default: 5000)",
                    "default": 5000.0,
                },
                **_REGION_PROPS,
            },
            "required": ["document_path", "agent_id"],
        },
    },
    "release_lock": {
        "description": (
            "Release a held lock. Only the lock owner can release it. "
            "Specify region_start/region_end to release a specific region lock. "
            "Omit region params to release a whole-file lock."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "document_path": {
                    "type": "string",
                    "description": "Path to the document",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Agent releasing the lock (must be the owner)",
                },
                **_REGION_PROPS,
            },
            "required": ["document_path", "agent_id"],
        },
    },
    "refresh_lock": {
        "description": (
            "Extend a lock's TTL without releasing and re-acquiring it. "
            "Only the lock owner can refresh. Specify region to refresh a region lock."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "document_path": {
                    "type": "string",
                    "description": "Path to the document",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Agent refreshing the lock (must be the owner)",
                },
                "ttl": {
                    "type": "number",
                    "description": "New TTL in seconds (default: 300)",
                    "default": 300.0,
                },
                **_REGION_PROPS,
            },
            "required": ["document_path", "agent_id"],
        },
    },
    "get_lock_status": {
        "description": (
            "Check if a document is currently locked and by whom. "
            "Returns all active locks on the path (including region locks). "
            "Also cleans up expired locks on read."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "document_path": {
                    "type": "string",
                    "description": "Path to the document",
                },
            },
            "required": ["document_path"],
        },
    },
    "list_locks": {
        "description": (
            "List all active (non-expired) locks. Optionally filter by agent_id. "
            "Returns lock details including document path, holder, expiry time, "
            "lock type, and region (if region lock)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Optional agent ID to filter locks by holder",
                },
            },
        },
    },
    "admin_locks": {
        "description": (
            "Administrative lock operations. "
            "action='release_by_agent' releases all locks for an agent. "
            "action='reap_expired' clears expired locks. "
            "action='reap_stale' marks stale agents stopped and cleans their locks."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["release_by_agent", "reap_expired", "reap_stale"],
                    "description": "Administrative action to perform",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Agent whose locks to release (required for release_by_agent)",
                    "default": None,
                },
                "grace_seconds": {
                    "type": "number",
                    "description": "Grace period for reap_expired",
                    "default": 0.0,
                },
                "timeout": {
                    "type": "number",
                    "description": "Seconds after which an agent is stale (default: 600)",
                    "default": 600.0,
                },
            },
            "required": ["action"],
        },
    },
}


# ------------------------------------------------------------------ #
# Coordination Actions (2 tools)
# ------------------------------------------------------------------ #

TOOL_SCHEMAS_COORDINATION: dict[str, dict] = {
    "broadcast": {
        "description": (
            "Announce an intention to all live sibling agents before taking "
            "an action. Returns which siblings are live and any lock conflicts. "
            "When handoff_targets is provided, performs a formal multi-recipient "
            "handoff recorded in the handoffs table. "
            "When require_ack is True, creates a trackable broadcast record and "
            "sends acknowledgment request messages to each live sibling."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent making the broadcast",
                },
                "document_path": {
                    "type": "string",
                    "description": "Optional document path to check for lock conflicts",
                    "default": None,
                },
                "ttl": {
                    "type": "number",
                    "description": "Staleness cutoff for sibling detection in seconds (default: 30)",
                    "default": 30.0,
                },
                "handoff_targets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Explicit list of agent IDs to hand off to. "
                        "When provided, broadcast becomes a formal multi-recipient handoff "
                        "recorded in the handoffs table and sends messages to each target."
                    ),
                    "default": None,
                },
                "require_ack": {
                    "type": "boolean",
                    "description": "If True, require recipients to acknowledge the broadcast via acknowledge_broadcast",
                    "default": False,
                },
                "message": {
                    "type": "string",
                    "description": "Optional message payload when require_ack is True",
                    "default": None,
                },
            },
            "required": ["agent_id"],
        },
    },
    "acknowledge_broadcast": {
        "description": (
            "Acknowledge receipt of a broadcast. Called by recipient agents "
            "when they receive a broadcast_ack_request message."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "broadcast_id": {
                    "type": "integer",
                    "description": "ID of the broadcast to acknowledge",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Agent acknowledging the broadcast",
                },
            },
            "required": ["broadcast_id", "agent_id"],
        },
    },
    "wait_for_broadcast_acks": {
        "description": (
            "Poll until all expected broadcast acknowledgments are received or timeout expires. "
            "Use after broadcast with require_ack=True to wait for recipients."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "broadcast_id": {
                    "type": "integer",
                    "description": "ID of the broadcast to wait for",
                },
                "timeout_s": {
                    "type": "number",
                    "description": "Maximum seconds to wait (default: 30)",
                    "default": 30.0,
                },
            },
            "required": ["broadcast_id"],
        },
    },
    "wait_for_locks": {
        "description": (
            "Poll until all specified locks are released or a timeout expires. "
            "Useful for waiting for a parallel agent to finish before proceeding."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "document_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of document paths to wait on",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Agent doing the waiting",
                },
                "timeout_s": {
                    "type": "number",
                    "description": "Maximum seconds to wait (default: 60)",
                    "default": 60.0,
                },
            },
            "required": ["document_paths", "agent_id"],
        },
    },
    "await_agent": {
        "description": (
            "Wait for an agent to complete (deregister) before proceeding. "
            "Polls agent status until the agent is stopped or timeout expires. "
            "Use this to coordinate sequential dependencies between agents."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent to wait for",
                },
                "timeout_s": {
                    "type": "number",
                    "description": "Maximum seconds to wait (default: 60)",
                    "default": 60.0,
                },
            },
            "required": ["agent_id"],
        },
    },
}


# ------------------------------------------------------------------ #
# Messaging (2 tools)
# ------------------------------------------------------------------ #

TOOL_SCHEMAS_MESSAGING: dict[str, dict] = {
    "send_message": {
        "description": (
            "Send a direct message to another agent. "
            "Messages are stored and can be retrieved via manage_messages. "
            "Use for query/response patterns between agents."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "from_agent_id": {"type": "string", "description": "Agent sending the message"},
                "to_agent_id": {"type": "string", "description": "Agent to receive the message"},
                "message_type": {"type": "string", "description": "Type of message (e.g. 'query', 'response', 'notification')"},
                "payload": {"type": "object", "description": "Optional JSON payload with message data"},
            },
            "required": ["from_agent_id", "to_agent_id", "message_type"],
        },
    },
    "manage_messages": {
        "description": "Unified messaging: send | get | mark_read. Use action='get' to retrieve messages.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["send", "get", "mark_read"], "description": "Messaging action"},
                "agent_id": {"type": "string", "description": "Agent ID for get/mark_read"},
                "from_agent_id": {"type": "string", "description": "Required for send"},
                "to_agent_id": {"type": "string", "description": "Required for send"},
                "message_type": {"type": "string", "description": "Required for send"},
                "payload": {"type": "object", "description": "Optional payload for send"},
                "unread_only": {"type": "boolean", "default": False, "description": "Only unread messages for get"},
                "limit": {"type": "integer", "default": 50, "description": "Max messages for get"},
                "message_ids": {"type": "array", "items": {"type": "integer"}, "description": "Specific IDs for mark_read"},
            },
            "required": ["action", "agent_id"],
        },
    },
}


# ------------------------------------------------------------------ #
# Change Awareness (2 tools)
# ------------------------------------------------------------------ #

TOOL_SCHEMAS_CHANGE: dict[str, dict] = {
    "notify_change": {
        "description": (
            "Record a change event so other agents can poll for it. "
            "Call after making a change to a shared document."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "document_path": {"type": "string", "description": "Path to the changed document"},
                "change_type": {"type": "string", "description": "Type of change (e.g. 'created', 'modified', 'deleted')"},
                "agent_id": {"type": "string", "description": "Agent that made the change"},
            },
            "required": ["document_path", "change_type", "agent_id"],
        },
    },
    "get_notifications": {
        "description": (
            "Poll for change notifications since a timestamp. "
            "If timeout_s > 0, long-polls until new notifications arrive. "
            "If prune_max_age_seconds or prune_max_entries is provided, prunes old data first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "since": {"type": "number", "description": "Unix timestamp to poll from", "default": None},
                "exclude_agent": {"type": "string", "description": "Agent ID to exclude", "default": None},
                "limit": {"type": "integer", "description": "Maximum notifications", "default": 100},
                "agent_id": {"type": "string", "description": "Waiter identity when timeout_s > 0", "default": None},
                "timeout_s": {"type": "number", "description": "Long-poll timeout (0 = immediate)", "default": 0.0},
                "poll_interval_s": {"type": "number", "description": "Poll interval", "default": 2.0},
                "prune_max_age_seconds": {"type": "number", "description": "Prune before returning", "default": None},
                "prune_max_entries": {"type": "integer", "description": "Prune before returning", "default": None},
            },
        },
    },
}


# ------------------------------------------------------------------ #
# Audit & Status (3 tools)
# ------------------------------------------------------------------ #

TOOL_SCHEMAS_AUDIT: dict[str, dict] = {
    "get_conflicts": {
        "description": (
            "Query the conflict log for lock steals and ownership violations. "
            "Useful for post-mortems and debugging agent interactions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "document_path": {
                    "type": "string",
                    "description": "Filter to a specific document path",
                    "default": None,
                },
                "agent_id": {
                    "type": "string",
                    "description": "Filter to conflicts involving this agent",
                    "default": None,
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of conflicts to return",
                    "default": 20,
                },
            },
        },
    },
    "get_contention_hotspots": {
        "description": (
            "Rank files by lock contention frequency. Returns files ordered by "
            "how many lock conflicts they've been involved in, along with the "
            "agents involved. Use to identify coordination chokepoints — files "
            "that multiple agents need access to and that cause frequent contention."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of hotspots to return",
                    "default": 10,
                },
            },
        },
    },
    "status": {
        "description": (
            "Get a summary of the coordination system state: "
            "registered agents, active locks, pending notifications, conflicts, "
            "and whether a coordination graph is loaded."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
}


# ------------------------------------------------------------------ #
# Graph & Visibility (7 tools)
# ------------------------------------------------------------------ #

TOOL_SCHEMAS_VISIBILITY: dict[str, dict] = {
    "load_coordination_spec": {
        "description": (
            "Reload the coordination spec from disk. Returns whether a graph "
            "was found and loaded, plus the graph's agent list."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to coordination_spec.yaml or .json (default: project root)",
                    "default": None,
                },
            },
        },
    },
    "scan_project": {
        "description": (
            "Perform a file ownership scan of the worktree_root. "
            "For every tracked file (.py, .md, .json, .yaml, .txt, .toml), "
            "upserts an entry into the file_ownership table. "
            "Returns the count of files scanned and owned."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "worktree_root": {
                    "type": "string",
                    "description": "Root directory to scan (default: engine's project root)",
                    "default": None,
                },
                "extensions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "File extensions to scan (default: py, md, json, yaml, txt, toml)",
                    "default": None,
                },
            },
        },
    },
    "get_agent_status": {
        "description": (
            "Get full status for a specific agent: current task, responsibilities "
            "(from the coordination graph), owned files, lineage, and lock state."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent to query",
                },
            },
            "required": ["agent_id"],
        },
    },
    "get_file_agent_map": {
        "description": (
            "Get a map of all tracked files to their assigned Agent ID "
            "and responsibility summary. Returns the full file_ownership table "
            "joined with agent responsibilities."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Filter to files owned by a specific agent",
                    "default": None,
                },
            },
        },
    },
    "update_agent_status": {
        "description": (
            "Update the current task description and/or declared scope for an agent. "
            "Stored in agent_responsibilities for visibility. "
            "Scope is a list of path prefixes that define the agent's working domain. "
            "If an agent declares a scope, lock acquisitions outside that scope are denied."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent updating its status",
                },
                "current_task": {
                    "type": "string",
                    "description": "Human-readable description of what this agent is doing",
                },
                "scope": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of path prefixes defining the agent's scope (e.g. ['/src/services/', '/src/models/'])",
                },
            },
            "required": ["agent_id"],
        },
    },
    "run_assessment": {
        "description": (
            "Run an assessment suite or score the current live session. "
            "If suite_path is provided, loads the JSON suite file and scores it. "
            "If suite_path is omitted, synthesizes a live session trace from DB state "
            "(agents, notifications, lineage) — no hand-authored suite required. "
            "Stores results in SQLite and returns a Markdown or JSON report."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "suite_path": {
                    "type": "string",
                    "description": "Path to the JSON test suite file (omit for live session scoring)",
                    "default": None,
                },
                "format": {
                    "type": "string",
                    "description": "Output format: 'markdown' (default) or 'json'",
                    "default": "markdown",
                },
                "graph_agent_id": {
                    "type": "string",
                    "description": "Optional: filter traces to those involving this graph agent role",
                    "default": None,
                },
                "scope": {
                    "type": "string",
                    "description": "'project' (default) restricts to the current worktree; 'all' scores every agent in the DB",
                    "default": "project",
                },
            },
        },
    },
    "get_agent_tree": {
        "description": (
            "Get the hierarchical agent tree with live work status. Each node shows "
            "the agent's current task, active file locks (with type and region), and "
            "boundary crossing warnings when locking files owned by other agents. "
            "Use as a shared situational reference — any agent sees the same live state. "
            "If agent_id is omitted, returns the tree rooted at the oldest active root agent."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Root of the tree to query (default: oldest active root agent)",
                    "default": None,
                },
            },
        },
    },
}


# ------------------------------------------------------------------ #
# Task Registry
# ------------------------------------------------------------------ #

TOOL_SCHEMAS_TASKS: dict[str, dict] = {
    "create_task": {
        "description": (
            "Create a new task in the shared task registry. "
            "The creating agent (parent_agent_id) assigns the task to a child or sibling agent."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Unique task ID (e.g. hub.12345.0.task.0)",
                },
                "parent_agent_id": {
                    "type": "string",
                    "description": "Agent creating this task",
                },
                "description": {
                    "type": "string",
                    "description": "What this task involves",
                },
                "depends_on": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Task IDs that must complete before this one starts",
                },
                "priority": {
                    "type": "integer",
                    "description": "Task priority (higher values execute first; default 0)",
                    "default": 0,
                },
            },
            "required": ["task_id", "parent_agent_id", "description"],
        },
    },
    "assign_task": {
        "description": "Assign a task to a specific agent.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Task to assign",
                },
                "assigned_agent_id": {
                    "type": "string",
                    "description": "Agent to assign the task to",
                },
            },
            "required": ["task_id", "assigned_agent_id"],
        },
    },
    "update_task_status": {
        "description": (
            "Update a task's status. When a task is completed, include a summary "
            "that a parent agent can compress upward in the hierarchy."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Task to update",
                },
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed", "blocked", "failed"],
                    "description": "New status for the task",
                },
                "summary": {
                    "type": "string",
                    "description": "Completion summary written by the agent (used for compression chains)",
                },
                "blocked_by": {
                    "type": "string",
                    "description": "Task ID that is blocking this task",
                },
                "error": {
                    "type": "string",
                    "description": "Error message when marking a task as failed (records to dead letter queue)",
                },
            },
            "required": ["task_id", "status"],
        },
    },
    "query_tasks": {
        "description": (
            "Unified task query. query_type='task' fetches one task by ID. "
            "query_type='child' fetches tasks created by an agent. "
            "query_type='by_agent' fetches tasks assigned to an agent. "
            "query_type='all' fetches every task. "
            "query_type='subtasks' fetches direct subtasks of a task. "
            "query_type='tree' fetches a task and its nested subtasks."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query_type": {
                    "type": "string",
                    "enum": ["task", "child", "by_agent", "all", "subtasks", "tree"],
                    "description": "Type of query to perform",
                },
                "task_id": {
                    "type": "string",
                    "description": "Task ID (required for query_type='task')",
                    "default": None,
                },
                "parent_agent_id": {
                    "type": "string",
                    "description": "Agent ID (required for query_type='child')",
                    "default": None,
                },
                "assigned_agent_id": {
                    "type": "string",
                    "description": "Agent ID (required for query_type='by_agent')",
                    "default": None,
                },
                "parent_task_id": {
                    "type": "string",
                    "description": "Parent task ID (required for query_type='subtasks')",
                    "default": None,
                },
                "root_task_id": {
                    "type": "string",
                    "description": "Root task ID (required for query_type='tree')",
                    "default": None,
                },
            },
            "required": ["query_type"],
        },
    },
    "create_subtask": {
        "description": (
            "Create a subtask under an existing parent task. "
            "The subtask inherits context from its parent and can be nested further. "
            "Use get_task_tree to retrieve the full hierarchy."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Unique subtask ID (e.g. parent_task_id + '.0')",
                },
                "parent_task_id": {
                    "type": "string",
                    "description": "ID of the parent task this subtask belongs to",
                },
                "parent_agent_id": {
                    "type": "string",
                    "description": "Agent creating this subtask",
                },
                "description": {
                    "type": "string",
                    "description": "What this subtask involves",
                },
                "depends_on": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Task IDs that must complete before this subtask starts",
                },
                "priority": {
                    "type": "integer",
                    "description": "Subtask priority (higher values execute first; default 0)",
                    "default": 0,
                },
            },
            "required": ["task_id", "parent_task_id", "parent_agent_id", "description"],
        },
    },

    "wait_for_task": {
        "description": (
            "Poll until a task reaches a terminal state (completed or failed) "
            "or the timeout expires. Use this to coordinate sequential dependencies "
            "between tasks when depends_on alone is not sufficient (e.g., waiting "
            "for a task completed by an external agent or system)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Task to wait on",
                },
                "timeout_s": {
                    "type": "number",
                    "description": "Maximum seconds to wait (default: 60)",
                    "default": 60.0,
                },
                "poll_interval_s": {
                    "type": "number",
                    "description": "Polling interval in seconds (default: 2)",
                    "default": 2.0,
                },
            },
            "required": ["task_id"],
        },
    },
    "get_available_tasks": {
        "description": (
            "Return tasks whose depends_on are all satisfied (completed) and "
            "that are not currently claimed. A task is \"available\" if its status "
            "is \"pending\" and all tasks in its depends_on list have status \"completed\". "
            "Use this to find work that can be picked up by an idle agent."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Optional agent ID to filter to tasks assigned to this agent",
                },
            },
        },
    },
}


# ------------------------------------------------------------------ #
# Work Intent Board
# ------------------------------------------------------------------ #

TOOL_SCHEMAS_INTENT: dict[str, dict] = {
    "manage_work_intents": {
        "description": "Unified work intent management: declare | get | clear.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["declare", "get", "clear"], "description": "Intent action"},
                "agent_id": {"type": "string", "description": "Agent ID"},
                "document_path": {"type": "string", "description": "Required for declare"},
                "intent": {"type": "string", "description": "Required for declare"},
                "ttl": {"type": "number", "default": 60.0, "description": "Seconds until intent expires"},
            },
            "required": ["action", "agent_id"],
        },
    },
}


# ------------------------------------------------------------------ #
# Handoffs
# ------------------------------------------------------------------ #

TOOL_SCHEMAS_HANDOFFS: dict[str, dict] = {
    "wait_for_handoff": {
        "description": (
            "Unified handoff operation. mode='status' returns the handoff record. "
            "mode='ack' acknowledges. mode='complete' marks completed. "
            "mode='cancel' cancels. mode='completion' waits for completion."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "handoff_id": {"type": "integer", "description": "Handoff ID"},
                "timeout_s": {"type": "number", "default": 30.0, "description": "Wait timeout"},
                "agent_id": {"type": "string", "description": "Required for ack mode", "default": None},
                "mode": {"type": "string", "enum": ["status", "ack", "complete", "cancel", "completion"], "default": "completion", "description": "Handoff action"},
            },
            "required": ["handoff_id"],
        },
    },
}


# ------------------------------------------------------------------ #
# Cross-Agent Dependencies
# ------------------------------------------------------------------ #

TOOL_SCHEMAS_DEPS: dict[str, dict] = {
    "manage_dependencies": {
        "description": (
            "Unified dependency management. mode='declare' creates a dependency. "
            "mode='check'/'blockers'/'assert' queries blockers. "
            "mode='satisfy' marks one satisfied. mode='list' lists all. "
            "mode='wait' polls until a dep_id is satisfied."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["declare", "check", "blockers", "assert", "satisfy", "list", "wait"], "description": "Action mode"},
                "agent_id": {"type": "string", "description": "Agent for check/blockers/assert/list", "default": None},
                "dependent_agent_id": {"type": "string", "description": "Required for declare", "default": None},
                "depends_on_agent_id": {"type": "string", "description": "Required for declare", "default": None},
                "depends_on_task_id": {"type": "string", "description": "Optional for declare", "default": None},
                "condition": {"type": "string", "default": "task_completed", "description": "Condition for declare"},
                "dep_id": {"type": "integer", "description": "Required for satisfy/wait", "default": None},
                "timeout_s": {"type": "number", "default": 60.0, "description": "Wait timeout"},
                "poll_interval_s": {"type": "number", "default": 2.0, "description": "Wait poll interval"},
            },
            "required": ["mode"],
        },
    },
}


TOOL_SCHEMAS_DLQ: dict[str, dict] = {
    "task_failures": {
        "description": (
            "Unified dead-letter queue operations. "
            "action='retry' resurrects a task. "
            "action='list_dead_letter' lists DLQ tasks. "
            "action='history' gets failure history for a task."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["retry", "list_dead_letter", "history"], "description": "DLQ action"},
                "task_id": {"type": "string", "description": "Required for retry/history", "default": None},
                "limit": {"type": "integer", "default": 50, "description": "Max results for list_dead_letter"},
            },
            "required": ["action"],
        },
    },
}


TOOL_SCHEMAS_LEASES: dict[str, dict] = {
    "acquire_coordinator_lease": {
        "description": (
            "Attempt to acquire the coordinator leadership lease (COORDINATOR_LEADER). "
            "If acquired, this agent becomes the active coordinator."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "Agent ID attempting to acquire the lease"},
                "ttl": {"type": "number", "description": "Lease TTL in seconds (default: 10)"},
            },
            "required": ["agent_id"],
        },
    },
    "manage_leases": {
        "description": (
            "Unified lease management. action='acquire' | 'refresh' | 'release' | "
            "'get' (returns current leader) | 'claim' (failover claim)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["acquire", "refresh", "release", "get", "claim"], "description": "Lease action"},
                "agent_id": {"type": "string", "description": "Agent ID"},
                "ttl": {"type": "number", "description": "TTL for acquire/claim", "default": None},
            },
            "required": ["action", "agent_id"],
        },
    },
}


TOOL_SCHEMAS_SPAWNER: dict[str, dict] = {
    "spawn_subagent": {
        "description": (
            "Register intent to spawn a sub-agent and return its spawn ID. "
            "The parent agent calls this before the external system spawns the sub-agent. "
            "This creates a pending spawn record that the spawning system will consume "
            "when the agent is actually spawned, correlating via ``parent_agent_id``. "
            "Returns the spawn ID and pending spawn record."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "parent_agent_id": {
                    "type": "string",
                    "description": "Parent agent ID that intends to spawn the sub-agent",
                },
                "subagent_type": {
                    "type": "string",
                    "description": "Type of sub-agent to spawn (e.g. 'Explore', 'Plan', 'general-purpose')",
                },
                "description": {
                    "type": "string",
                    "description": "Description of the sub-agent's task",
                    "default": None,
                },
                "prompt": {
                    "type": "string",
                    "description": "Prompt or instructions for the sub-agent",
                    "default": None,
                },
                "source": {
                    "type": "string",
                    "description": "Source system that will perform the spawn (e.g. 'claude_code', 'kimi_cli', 'cursor')",
                    "default": "external",
                },
            },
            "required": ["parent_agent_id", "subagent_type"],
        },
    },
    "report_subagent_spawned": {
        "description": (
            "Report that a sub-agent has been spawned by an external system. "
            "Any IDE/CLI (Claude Code, Kimi CLI, Cursor, etc.) calls this after "
            "spawning a sub-agent via its native mechanism. This consumes the "
            "pending spawn record created by ``spawn_subagent`` and links it to "
            "the actual child agent ID."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "parent_agent_id": {
                    "type": "string",
                    "description": "Parent agent ID that spawned the sub-agent",
                },
                "subagent_type": {
                    "type": "string",
                    "description": "Type of sub-agent that was spawned",
                    "default": None,
                },
                "child_agent_id": {
                    "type": "string",
                    "description": "Actual agent ID of the spawned sub-agent",
                },
                "source": {
                    "type": "string",
                    "description": "Source system that performed the spawn (e.g. 'claude_code', 'kimi_cli')",
                    "default": "external",
                },
            },
            "required": ["parent_agent_id", "child_agent_id"],
        },
    },
    "get_pending_spawns": {
        "description": (
            "Get pending (or all) spawn requests for a parent agent. "
            "Returns spawn records with status: pending | registered | expired."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "parent_agent_id": {
                    "type": "string",
                    "description": "Parent agent ID to get pending spawns for",
                },
                "include_consumed": {
                    "type": "boolean",
                    "description": "Include consumed/registered spawns in results",
                    "default": False,
                },
            },
            "required": ["parent_agent_id"],
        },
    },
    "await_subagent_registration": {
        "description": (
            "Poll until a pending spawn is consumed (sub-agent registered) or timeout. "
            "The parent agent calls this after ``spawn_subagent`` to wait for the "
            "external spawning system to report that the sub-agent is alive. "
            "Returns the consumed spawn record on success, or timed_out:true if the "
            "sub-agent did not register within the timeout."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "parent_agent_id": {
                    "type": "string",
                    "description": "Parent agent ID that spawned the sub-agent",
                },
                "subagent_type": {
                    "type": "string",
                    "description": "Wait for a specific sub-agent type (omit to wait for any)",
                    "default": None,
                },
                "timeout": {
                    "type": "number",
                    "description": "Timeout in seconds (default: 300)",
                    "default": 300.0,
                },
            },
            "required": ["parent_agent_id"],
        },
    },
    "request_subagent_deregistration": {
        "description": (
            "Request graceful deregistration of a child agent. "
            "Sets ``stop_requested_at`` on the child agent. The child is expected "
            "to poll ``is_subagent_stop_requested`` and call ``deregister_agent`` "
            "if the stop flag is set. After a timeout, the caller should escalate "
            "to ``deregister_agent`` directly. "
            "Returns ``requested`` if the stop flag was set, ``not_found`` if "
            "the child agent does not exist or is not active."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "parent_agent_id": {
                    "type": "string",
                    "description": "Parent agent ID making the request",
                },
                "child_agent_id": {
                    "type": "string",
                    "description": "Child agent ID to request stop for",
                },
            },
            "required": ["parent_agent_id", "child_agent_id"],
        },
    },
    "is_subagent_stop_requested": {
        "description": (
            "Check if a stop has been requested for this agent. "
            "The agent should call this periodically and deregister if "
            "the stop flag is set."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent ID to check stop-request flag for",
                },
            },
            "required": ["agent_id"],
        },
    },
    "await_subagent_stopped": {
        "description": (
            "Poll until a child agent is stopped or the timeout is reached. "
            "Returns ``stopped: True`` if the child called ``deregister_agent`` "
            "within the timeout. Returns ``timed_out: True`` with ``escalate: True`` "
            "if the child did not stop in time — the caller should then call "
            "``deregister_agent`` directly to force cleanup."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "child_agent_id": {
                    "type": "string",
                    "description": "Child agent ID to wait for",
                },
                "timeout": {
                    "type": "number",
                    "description": "Timeout in seconds (default: 30)",
                    "default": 30.0,
                },
            },
            "required": ["child_agent_id"],
        },
    },
}


# ------------------------------------------------------------------ #
# Aggregate
# ------------------------------------------------------------------ #

TOOL_SCHEMAS: dict[str, dict] = (
    TOOL_SCHEMAS_IDENTITY
    | TOOL_SCHEMAS_LOCKING
    | TOOL_SCHEMAS_COORDINATION
    | TOOL_SCHEMAS_CHANGE
    | TOOL_SCHEMAS_AUDIT
    | TOOL_SCHEMAS_VISIBILITY
    | TOOL_SCHEMAS_MESSAGING
    | TOOL_SCHEMAS_TASKS
    | TOOL_SCHEMAS_INTENT
    | TOOL_SCHEMAS_HANDOFFS
    | TOOL_SCHEMAS_DEPS
    | TOOL_SCHEMAS_DLQ
    | TOOL_SCHEMAS_LEASES
    | TOOL_SCHEMAS_SPAWNER
)
