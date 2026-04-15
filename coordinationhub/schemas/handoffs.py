"""Handoffs tool schemas for CoordinationHub.

Pure data declarations — no logic.  Re-exported by :mod:`coordinationhub.schemas`.
"""

from __future__ import annotations


TOOL_SCHEMAS_HANDOFFS: dict[str, dict] = {
    "wait_for_handoff": {
        "description": (
            "Unified handoff operation. mode='status' returns the handoff record. "
            "mode='ack' acknowledges. mode='complete' marks completed. "
            "mode='cancel' cancels. mode='completion' waits for completion."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "handoff_id": {"type": "integer", "description": "Handoff ID"},
                "timeout_s": {"type": "number", "default": 30.0, "description": "Wait timeout"},
                "agent_id": {"type": "string", "description": "Required for ack mode", "default": None},
                "mode": {"type": "string", "enum": ["status", "ack", "complete", "cancel", "completion"], "default": "completion", "description": "Handoff action"},
            },
            "required": ["handoff_id"],
        },
    },
}

