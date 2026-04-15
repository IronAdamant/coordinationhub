"""Document Locking tool schemas for CoordinationHub.

Pure data declarations — no logic.  Re-exported by :mod:`coordinationhub.schemas`.
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

