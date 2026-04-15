"""Messaging tool schemas for CoordinationHub.

Pure data declarations — no logic.  Re-exported by :mod:`coordinationhub.schemas`.
"""

from __future__ import annotations


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

