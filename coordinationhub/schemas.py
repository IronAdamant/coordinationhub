"""Tool schemas for CoordinationHub — all 31 MCP tools.

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
    "get_lineage": {
        "description": (
            "Get the ancestor chain (parent → grandparent) and all descendants "
            "(direct children, grandchildren) of a given agent."
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
    "get_siblings": {
        "description": (
            "Get all agents that share the same parent as the given agent. "
            "Useful for coordination before taking a shared action."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent whose siblings to find",
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
    "release_agent_locks": {
        "description": (
            "Release all locks held by a given agent (including region locks). "
            "Used during agent deregistration or when an agent dies unexpectedly."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent whose locks to release",
                },
            },
            "required": ["agent_id"],
        },
    },
    "reap_expired_locks": {
        "description": (
            "Clear all expired locks from the lock table."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    "reap_stale_agents": {
        "description": (
            "Mark stale agents as stopped and release their locks. "
            "An agent is stale if its heartbeat is older than the timeout."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "timeout": {
                    "type": "number",
                    "description": "Seconds after which an agent is stale (default: 600)",
                    "default": 600.0,
                },
            },
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
    "get_broadcast_status": {
        "description": (
            "Get the current acknowledgment status for a broadcast. "
            "Returns the list of agents that have acknowledged it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "broadcast_id": {
                    "type": "integer",
                    "description": "ID of the broadcast to query",
                },
            },
            "required": ["broadcast_id"],
        },
    },
    "await_broadcast_acks": {
        "description": (
            "Poll until all expected acknowledgments are received or timeout expires. "
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
# Messaging (3 tools)
# ------------------------------------------------------------------ #

TOOL_SCHEMAS_MESSAGING: dict[str, dict] = {
    "send_message": {
        "description": (
            "Send a direct message to another agent. "
            "Messages are stored and can be retrieved via get_messages. "
            "Use for query/response patterns between agents."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "from_agent_id": {
                    "type": "string",
                    "description": "Agent sending the message",
                },
                "to_agent_id": {
                    "type": "string",
                    "description": "Agent to receive the message",
                },
                "message_type": {
                    "type": "string",
                    "description": "Type of message (e.g. 'query', 'response', 'notification')",
                },
                "payload": {
                    "type": "object",
                    "description": "Optional JSON payload with message data",
                },
            },
            "required": ["from_agent_id", "to_agent_id", "message_type"],
        },
    },
    "get_messages": {
        "description": (
            "Get messages sent to an agent. "
            "Returns all messages or only unread ones if unread_only=True."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent to get messages for",
                },
                "unread_only": {
                    "type": "boolean",
                    "description": "Only return unread messages (default: False)",
                    "default": False,
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum messages to return (default: 50)",
                    "default": 50,
                },
            },
            "required": ["agent_id"],
        },
    },
    "mark_messages_read": {
        "description": (
            "Mark messages as read. If message_ids is not provided, marks all unread messages."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent marking messages as read",
                },
                "message_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Specific message IDs to mark as read (default: all unread)",
                },
            },
            "required": ["agent_id"],
        },
    },
}


# ------------------------------------------------------------------ #
# Change Awareness (3 tools)
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
                "document_path": {
                    "type": "string",
                    "description": "Path to the changed document",
                },
                "change_type": {
                    "type": "string",
                    "description": "Type of change (e.g. 'created', 'modified', 'deleted')",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Agent that made the change",
                },
            },
            "required": ["document_path", "change_type", "agent_id"],
        },
    },
    "get_notifications": {
        "description": (
            "Poll for change notifications since a timestamp. "
            "Exclude your own agent_id to see only other-agents' changes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "since": {
                    "type": "number",
                    "description": "Unix timestamp to poll from (default: last 5 minutes)",
                    "default": None,
                },
                "exclude_agent": {
                    "type": "string",
                    "description": "Agent ID to exclude from results",
                    "default": None,
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of notifications to return",
                    "default": 100,
                },
            },
        },
    },
    "prune_notifications": {
        "description": (
            "Clean up old notifications by age or entry count. "
            "Call periodically to prevent unbounded growth."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "max_age_seconds": {
                    "type": "number",
                    "description": "Delete notifications older than this many seconds",
                    "default": None,
                },
                "max_entries": {
                    "type": "integer",
                    "description": "Keep at most this many notifications, deleting oldest",
                    "default": None,
                },
            },
        },
    },
    "wait_for_notifications": {
        "description": (
            "Long-poll for new notifications until one arrives or timeout expires. "
            "Use this instead of polling get_notifications in a loop. "
            "Returns notifications when new ones arrive, or {\"timed_out\": True} "
            "if the timeout expires with no new notifications."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent ID to use as the waiter's identity",
                },
                "timeout_s": {
                    "type": "number",
                    "description": "Maximum seconds to wait (default: 30)",
                    "default": 30.0,
                },
                "poll_interval_s": {
                    "type": "number",
                    "description": "Polling interval in seconds (default: 2)",
                    "default": 2.0,
                },
                "exclude_agent": {
                    "type": "string",
                    "description": "Agent ID to exclude from notifications (default: waiter's own ID)",
                    "default": None,
                },
            },
            "required": ["agent_id"],
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
# Graph & Visibility (8 tools)
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
    "validate_graph": {
        "description": (
            "Validate the currently loaded coordination graph schema. "
            "Returns validation errors if invalid, or an empty error list if valid."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
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
            "Run an assessment suite against the loaded coordination graph. "
            "Loads the suite JSON file, scores all traces on the defined metrics, "
            "outputs a report, and stores results in SQLite for historical comparison. "
            "Use graph_agent_id to filter traces to a specific agent role."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "suite_path": {
                    "type": "string",
                    "description": "Path to the JSON test suite file",
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
            },
            "required": ["suite_path"],
        },
    },
    "assess_current_session": {
        "description": (
            "Score the current live session against the loaded coordination graph. "
            "Synthesizes an assessment trace from DB state (agents, change "
            "notifications, lineage) — no hand-authored suite file required. "
            "Requires a coordination graph to be loaded first via "
            "load_coordination_spec. Scores all 5 metrics, stores results in "
            "assessment_results, and returns a Markdown or JSON report."
        ),
        "parameters": {
            "type": "object",
            "properties": {
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
    "get_task": {
        "description": "Get a single task by ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Task to retrieve",
                },
            },
            "required": ["task_id"],
        },
    },
    "get_child_tasks": {
        "description": "Get all tasks created by a given agent.",
        "parameters": {
            "type": "object",
            "properties": {
                "parent_agent_id": {
                    "type": "string",
                    "description": "Agent whose child tasks to retrieve",
                },
            },
            "required": ["parent_agent_id"],
        },
    },
    "get_tasks_by_agent": {
        "description": "Get all tasks assigned to a given agent.",
        "parameters": {
            "type": "object",
            "properties": {
                "assigned_agent_id": {
                    "type": "string",
                    "description": "Agent whose assigned tasks to retrieve",
                },
            },
            "required": ["assigned_agent_id"],
        },
    },
    "get_all_tasks": {
        "description": "Get all tasks in the task registry.",
        "parameters": {
            "type": "object",
            "properties": {},
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
    "get_subtasks": {
        "description": "Get all direct subtasks of a given task.",
        "parameters": {
            "type": "object",
            "properties": {
                "parent_task_id": {
                    "type": "string",
                    "description": "ID of the parent task whose subtasks to retrieve",
                },
            },
            "required": ["parent_task_id"],
        },
    },
    "get_task_tree": {
        "description": "Get a task with all its subtasks recursively as a nested tree.",
        "parameters": {
            "type": "object",
            "properties": {
                "root_task_id": {
                    "type": "string",
                    "description": "ID of the root task to build the tree from",
                },
            },
            "required": ["root_task_id"],
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
    "declare_work_intent": {
        "description": (
            "Declare intent to work on a file before acquiring a lock. "
            "Other agents checking work_intent receive a proximity_warning "
            "(not a denial) when acquiring a conflicting lock. "
            "Intent expires after ttl seconds."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent declaring the intent",
                },
                "document_path": {
                    "type": "string",
                    "description": "File path the agent intends to work on",
                },
                "intent": {
                    "type": "string",
                    "description": "Short description of the work intent (e.g. 'implementing get_agent_tree')",
                },
                "ttl": {
                    "type": "number",
                    "default": 60.0,
                    "description": "Seconds until intent expires (default: 60)",
                },
            },
            "required": ["agent_id", "document_path", "intent"],
        },
    },
    "get_work_intents": {
        "description": (
            "Get all live (non-expired) work intents. "
            "Optionally filter to a specific agent."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Filter to intents declared by this agent (default: all agents)",
                },
            },
        },
    },
    "clear_work_intent": {
        "description": "Clear an agent's declared work intent (e.g. after lock is acquired).",
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent whose intent to clear",
                },
            },
            "required": ["agent_id"],
        },
    },
}


# ------------------------------------------------------------------ #
# Handoffs
# ------------------------------------------------------------------ #

TOOL_SCHEMAS_HANDOFFS: dict[str, dict] = {
    "acknowledge_handoff": {
        "description": "Acknowledge receipt of a handoff. Called by each target agent.",
        "parameters": {
            "type": "object",
            "properties": {
                "handoff_id": {
                    "type": "integer",
                    "description": "Handoff ID to acknowledge",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Agent acknowledging the handoff",
                },
            },
            "required": ["handoff_id", "agent_id"],
        },
    },
    "complete_handoff": {
        "description": "Mark a handoff as completed (called by the originating agent).",
        "parameters": {
            "type": "object",
            "properties": {
                "handoff_id": {
                    "type": "integer",
                    "description": "Handoff ID to complete",
                },
            },
            "required": ["handoff_id"],
        },
    },
    "cancel_handoff": {
        "description": "Cancel a handoff (abort before completion).",
        "parameters": {
            "type": "object",
            "properties": {
                "handoff_id": {
                    "type": "integer",
                    "description": "Handoff ID to cancel",
                },
            },
            "required": ["handoff_id"],
        },
    },
    "get_handoffs": {
        "description": (
            "Get handoffs with optional status and sender filtering."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter to handoffs with this status (pending/acknowledged/completed/cancelled)",
                },
                "from_agent_id": {
                    "type": "string",
                    "description": "Filter to handoffs from this agent",
                },
                "limit": {
                    "type": "integer",
                    "default": 50,
                    "description": "Maximum handoffs to return (default: 50)",
                },
            },
        },
    },
}


# ------------------------------------------------------------------ #
# Cross-Agent Dependencies
# ------------------------------------------------------------------ #

TOOL_SCHEMAS_DEPS: dict[str, dict] = {
    "declare_dependency": {
        "description": (
            "Declare that dependent_agent needs depends_on_agent to finish "
            "task X (or any task by that agent) before starting work."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "dependent_agent_id": {
                    "type": "string",
                    "description": "Agent that has the dependency",
                },
                "depends_on_agent_id": {
                    "type": "string",
                    "description": "Agent that must complete first",
                },
                "depends_on_task_id": {
                    "type": "string",
                    "description": "Specific task ID (omit for 'any task by that agent')",
                },
                "condition": {
                    "type": "string",
                    "default": "task_completed",
                    "description": "Condition: task_completed, agent_registered, or agent_stopped",
                },
            },
            "required": ["dependent_agent_id", "depends_on_agent_id"],
        },
    },
    "check_dependencies": {
        "description": (
            "Check whether an agent has unsatisfied cross-agent dependencies. "
            "Returns blocked:true if any dependency is not satisfied."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent to check dependencies for",
                },
            },
            "required": ["agent_id"],
        },
    },
    "satisfy_dependency": {
        "description": "Mark a dependency as satisfied (called after condition is met).",
        "parameters": {
            "type": "object",
            "properties": {
                "dep_id": {
                    "type": "integer",
                    "description": "Dependency ID to satisfy",
                },
            },
            "required": ["dep_id"],
        },
    },
    "get_blockers": {
        "description": "Alias for check_dependencies — get unsatisfied blockers for an agent.",
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent to check blockers for",
                },
            },
            "required": ["agent_id"],
        },
    },
    "assert_can_start": {
        "description": (
            "Structured check before starting significant work. "
            "Returns can_start:false with blocker details if dependencies are unmet."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent about to start work",
                },
            },
            "required": ["agent_id"],
        },
    },
    "get_all_dependencies": {
        "description": "Get all declared dependencies, optionally filtered by dependent agent.",
        "parameters": {
            "type": "object",
            "properties": {
                "dependent_agent_id": {
                    "type": "string",
                    "description": "Filter to dependencies declared by this agent",
                },
            },
        },
    },
}


TOOL_SCHEMAS_DLQ: dict[str, dict] = {
    "retry_task": {
        "description": (
            "Retry a task from the dead letter queue. "
            "Resets the task to 'pending' status so it can be reassigned and retried. "
            "Only tasks in dead_letter status can be retried."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Task ID to retry from the dead letter queue",
                },
            },
            "required": ["task_id"],
        },
    },
    "get_dead_letter_tasks": {
        "description": (
            "Get all tasks currently in the dead letter queue. "
            "Tasks enter the DLQ after exceeding max_retries failure attempts. "
            "Use retry_task to resurrect a task from the DLQ."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of dead letter tasks to return (default 50)",
                    "default": 50,
                },
            },
        },
    },
    "get_task_failure_history": {
        "description": (
            "Get the failure history for a task. "
            "Returns all recorded failure attempts including error messages, "
            "attempt counts, and whether the task entered dead_letter status."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Task ID whose failure history to retrieve",
                },
            },
            "required": ["task_id"],
        },
    },
}


TOOL_SCHEMAS_LEASES: dict[str, dict] = {
    "acquire_coordinator_lease": {
        "description": (
            "Attempt to acquire the coordinator leadership lease (COORDINATOR_LEADER). "
            "If acquired, this agent becomes the active coordinator. "
            "The lease has a short TTL (default 10s) and must be refreshed via "
            "refresh_coordinator_lease before it expires. "
            "If leadership is held by another agent, returns the current holder."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent ID attempting to acquire the lease",
                },
                "ttl": {
                    "type": "number",
                    "description": "Lease TTL in seconds (default: 10)",
                },
            },
            "required": ["agent_id"],
        },
    },
    "refresh_coordinator_lease": {
        "description": (
            "Refresh the coordinator leadership lease TTL. "
            "Must be called before the lease expires to maintain leadership. "
            "Returns an error if this agent is not the current lease holder."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent ID refreshing the lease",
                },
            },
            "required": ["agent_id"],
        },
    },
    "release_coordinator_lease": {
        "description": (
            "Release the coordinator leadership lease. "
            "Returns an error if this agent is not the current lease holder. "
            "After release, another agent can acquire leadership via "
            "acquire_coordinator_lease."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent ID releasing the lease",
                },
            },
            "required": ["agent_id"],
        },
    },
    "get_leader": {
        "description": (
            "Return the current coordinator lease holder, or null if the "
            "lease is unheld/expired."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    "claim_leadership": {
        "description": (
            "Claim coordinator leadership when the current leader has failed. "
            "Succeeds only if the current lease is expired or unheld — "
            "it is NOT taken from a live holder. "
            "Use after detecting a failed leader (missed heartbeat or lease expiry). "
            "After claiming, rebuild any in-memory state from the DB."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent ID attempting to claim leadership",
                },
                "ttl": {
                    "type": "number",
                    "description": "Lease TTL in seconds (default: 10)",
                },
            },
            "required": ["agent_id"],
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
