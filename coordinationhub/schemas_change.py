"""Change Awareness tool schemas (3 tools)."""

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
}
