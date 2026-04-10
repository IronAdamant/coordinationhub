"""Document Locking tool schemas (8 tools).

Supports file-level and region-level locking with shared/exclusive semantics.
"""

from __future__ import annotations

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
