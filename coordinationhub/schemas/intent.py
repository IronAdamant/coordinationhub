"""Work Intent Board tool schemas for CoordinationHub.

Pure data declarations — no logic.  Re-exported by :mod:`coordinationhub.schemas`.

T7.46: the ``declare`` action requires ``document_path`` and ``intent``
whereas ``get`` and ``clear`` don't. A flat ``required`` list can't
express that, so the validator walks an additional ``oneOf`` branch that
picks the matching per-action sub-schema.
"""

from __future__ import annotations

from ..limits import MAX_INTENT


TOOL_SCHEMAS_INTENT: dict[str, dict] = {
    "manage_work_intents": {
        "description": "Unified work intent management: declare | get | clear.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["declare", "get", "clear"],
                    "description": (
                        "Intent action. 'declare' requires document_path and intent. "
                        "'get' / 'clear' only need agent_id."
                    ),
                },
                "agent_id": {"type": "string", "minLength": 1, "description": "Agent ID"},
                "document_path": {
                    "type": "string",
                    "description": "File path the intent targets (required for declare)",
                },
                "intent": {
                    "type": "string",
                    "maxLength": MAX_INTENT,
                    "description": "Free-text intent (required for declare)",
                },
                "ttl": {
                    "type": "number",
                    "minimum": 0,
                    "default": 60.0,
                    "description": "Seconds until intent expires (declare)",
                },
            },
            "required": ["action", "agent_id"],
            # T7.46: per-action required-field enforcement.
            "oneOf": [
                {
                    "properties": {"action": {"enum": ["declare"]}},
                    "required": ["action", "agent_id", "document_path", "intent"],
                },
                {
                    "properties": {"action": {"enum": ["get"]}},
                    "required": ["action", "agent_id"],
                },
                {
                    "properties": {"action": {"enum": ["clear"]}},
                    "required": ["action", "agent_id"],
                },
            ],
        },
    },
}

