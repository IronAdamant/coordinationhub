"""Tool schemas and dispatch table for CoordinationHub.

Shared by both HTTP and stdio transports. Zero internal dependencies.
"""

from __future__ import annotations

# ------------------------------------------------------------------ #
# Tool schemas (JSON Schema per tool)
# ------------------------------------------------------------------ #

TOOL_SCHEMAS: dict[str, dict] = {
    # ------------------------------------------------------------------ #
    # Identity & Registration
    # ------------------------------------------------------------------ #
    "register_agent": {
        "description": (
            "Register an agent with the coordination hub and receive a context bundle "
            "containing sibling agents, active locks, and coordination URLs. "
            "Use this as the first call when spawning a new agent."
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
            "Also reaps any expired locks in the same call. "
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
    # ------------------------------------------------------------------ #
    # Document Locking
    # ------------------------------------------------------------------ #
    "acquire_lock": {
        "description": (
            "Acquire an exclusive or shared lock on a document path. "
            "If the lock is already held by another agent and not expired, "
            "returns conflict info unless force=True (which steals the lock "
            "and records a conflict). Use before writing any shared file."
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
                    "description": "'exclusive' (default) or 'shared'",
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
            },
            "required": ["document_path", "agent_id"],
        },
    },
    "release_lock": {
        "description": (
            "Release a held lock. Only the lock owner can release it. "
            "Use when done writing a shared document."
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
            },
            "required": ["document_path", "agent_id"],
        },
    },
    "refresh_lock": {
        "description": (
            "Extend a lock's TTL without releasing and re-acquiring it. "
            "Only the lock owner can refresh."
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
            },
            "required": ["document_path", "agent_id"],
        },
    },
    "get_lock_status": {
        "description": (
            "Check if a document is currently locked and by whom. "
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
    "release_agent_locks": {
        "description": (
            "Release all locks held by a given agent. "
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
            "Clear all expired locks from the lock table. "
            "Called automatically by heartbeat; can also be called manually."
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
    # ------------------------------------------------------------------ #
    # Coordination Actions
    # ------------------------------------------------------------------ #
    "broadcast": {
        "description": (
            "Announce an intention to all live sibling agents before taking "
            "an action. Returns which siblings are live and any lock conflicts. "
            "Does not wait for responses — just checks current state."
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
            },
            "required": ["agent_id", "message"],
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
    # ------------------------------------------------------------------ #
    # Change Awareness
    # ------------------------------------------------------------------ #
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
    # ------------------------------------------------------------------ #
    # Conflict Audit
    # ------------------------------------------------------------------ #
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
    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #
    "status": {
        "description": (
            "Get a summary of the coordination system state: "
            "registered agents, active locks, pending notifications, conflicts."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
}


# ------------------------------------------------------------------ #
# Dispatch table: tool_name -> (engine_method_name, allowed_kwargs)
# ------------------------------------------------------------------ #

TOOL_DISPATCH: dict[str, tuple[str, list[str]]] = {
    # Identity
    "register_agent": ("register_agent", ["agent_id", "parent_id", "worktree_root"]),
    "heartbeat": ("heartbeat", ["agent_id"]),
    "deregister_agent": ("deregister_agent", ["agent_id"]),
    "list_agents": ("list_agents", ["active_only", "stale_timeout"]),
    "get_lineage": ("get_lineage", ["agent_id"]),
    "get_siblings": ("get_siblings", ["agent_id"]),
    # Locking
    "acquire_lock": ("acquire_lock", ["document_path", "agent_id", "lock_type", "ttl", "force"]),
    "release_lock": ("release_lock", ["document_path", "agent_id"]),
    "refresh_lock": ("refresh_lock", ["document_path", "agent_id", "ttl"]),
    "get_lock_status": ("get_lock_status", ["document_path"]),
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
}
