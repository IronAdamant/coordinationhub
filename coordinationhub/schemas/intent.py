"""Work Intent Board tool schemas for CoordinationHub.

Pure data declarations — no logic.  Re-exported by :mod:`coordinationhub.schemas`.
"""

from __future__ import annotations


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

