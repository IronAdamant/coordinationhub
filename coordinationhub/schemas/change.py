"""Change Awareness tool schemas for CoordinationHub.

Pure data declarations — no logic.  Re-exported by :mod:`coordinationhub.schemas`.
"""

from __future__ import annotations


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

