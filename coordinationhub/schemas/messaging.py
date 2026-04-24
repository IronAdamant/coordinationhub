"""Messaging tool schemas for CoordinationHub.

Pure data declarations — no logic.  Re-exported by :mod:`coordinationhub.schemas`.

T6.12 / T7.49: ``payload`` is declared ``object`` (dict-like); primitives
truncate the JSON serialization to ``MAX_MESSAGE`` at write time so a
malicious caller can't wedge megabytes of JSON into SQLite. The schema
here constrains shape, not size — size is enforced at the primitive.

T7.46: ``manage_messages`` has per-action required fields. ``send``
requires the addressing triple; ``get`` / ``mark_read`` only need the
recipient agent id.
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
                "from_agent_id": {"type": "string", "minLength": 1, "description": "Agent sending the message"},
                "to_agent_id": {"type": "string", "minLength": 1, "description": "Agent to receive the message"},
                "message_type": {"type": "string", "minLength": 1, "description": "Type of message (e.g. 'query', 'response', 'notification')"},
                "payload": {
                    "type": "object",
                    "description": (
                        "Optional JSON payload. Serialized JSON is truncated to MAX_MESSAGE "
                        "at the primitive (T6.14) so a bloated payload can't corrupt storage."
                    ),
                },
                "caller_agent_id": {
                    "type": "string",
                    "description": (
                        "Optional caller assertion (T2.4). When supplied, must equal "
                        "from_agent_id — rejects impersonation where a compromised caller "
                        "forges a message 'from' another agent."
                    ),
                },
            },
            "required": ["from_agent_id", "to_agent_id", "message_type"],
        },
    },
    "manage_messages": {
        "description": "Unified messaging: send | get | mark_read. Use action='get' to retrieve messages.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["send", "get", "mark_read"],
                    "description": (
                        "Messaging action. 'send' requires from/to/message_type. "
                        "'get' and 'mark_read' only need agent_id."
                    ),
                },
                "agent_id": {"type": "string", "minLength": 1, "description": "Recipient agent ID"},
                "from_agent_id": {"type": "string", "description": "Sender (required for send)"},
                "to_agent_id": {"type": "string", "description": "Recipient (required for send)"},
                "message_type": {"type": "string", "description": "Type tag (required for send)"},
                "payload": {"type": "object", "description": "Optional payload for send"},
                "unread_only": {"type": "boolean", "default": False, "description": "Only unread messages for get"},
                "limit": {"type": "integer", "minimum": 1, "default": 50, "description": "Max messages for get"},
                "message_ids": {"type": "array", "items": {"type": "integer", "minimum": 1}, "description": "Specific IDs for mark_read"},
                "since_id": {"type": "integer", "minimum": 0, "description": "Return only messages with id > since_id (cursor for incremental polling, action='get')"},
                "caller_agent_id": {
                    "type": "string",
                    "description": (
                        "Optional caller assertion (T2.4). For send, must equal "
                        "from_agent_id; for get/mark_read, must equal agent_id. "
                        "Blocks cross-agent impersonation / inbox siphoning."
                    ),
                },
            },
            "required": ["action", "agent_id"],
            "oneOf": [
                {
                    "properties": {"action": {"enum": ["send"]}},
                    "required": [
                        "action", "agent_id",
                        "from_agent_id", "to_agent_id", "message_type",
                    ],
                },
                {
                    "properties": {"action": {"enum": ["get"]}},
                    "required": ["action", "agent_id"],
                },
                {
                    "properties": {"action": {"enum": ["mark_read"]}},
                    "required": ["action", "agent_id"],
                },
            ],
        },
    },
}

