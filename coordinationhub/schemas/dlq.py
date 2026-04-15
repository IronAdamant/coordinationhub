"""Dead Letter Queue tool schemas for CoordinationHub.

Pure data declarations — no logic.  Re-exported by :mod:`coordinationhub.schemas`.
"""

from __future__ import annotations


TOOL_SCHEMAS_DLQ: dict[str, dict] = {
    "task_failures": {
        "description": (
            "Unified dead-letter queue operations. "
            "action='retry' resurrects a task. "
            "action='list_dead_letter' lists DLQ tasks. "
            "action='history' gets failure history for a task."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["retry", "list_dead_letter", "history"], "description": "DLQ action"},
                "task_id": {"type": "string", "description": "Required for retry/history", "default": None},
                "limit": {"type": "integer", "default": 50, "description": "Max results for list_dead_letter"},
            },
            "required": ["action"],
        },
    },
}

