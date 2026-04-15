"""Coordination Actions tool schemas for CoordinationHub.

Pure data declarations — no logic.  Re-exported by :mod:`coordinationhub.schemas`.
"""

from __future__ import annotations


TOOL_SCHEMAS_COORDINATION: dict[str, dict] = {
    "broadcast": {
        "description": (
            "Announce an intention to all live sibling agents before taking "
            "an action. Returns which siblings are live and any lock conflicts. "
            "When handoff_targets is provided, performs a formal multi-recipient "
            "handoff recorded in the handoffs table. "
            "When require_ack is True, creates a trackable broadcast record and "
            "sends acknowledgment request messages to each live sibling."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent making the broadcast",
                },
                "document_path": {
                    "type": "string",
                    "description": "Optional document path to check for lock conflicts",
                    "default": None,
                },
                "ttl": {
                    "type": "number",
                    "description": "Staleness cutoff for sibling detection in seconds (default: 30)",
                    "default": 30.0,
                },
                "handoff_targets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Explicit list of agent IDs to hand off to. "
                        "When provided, broadcast becomes a formal multi-recipient handoff "
                        "recorded in the handoffs table and sends messages to each target."
                    ),
                    "default": None,
                },
                "require_ack": {
                    "type": "boolean",
                    "description": "If True, require recipients to acknowledge the broadcast via acknowledge_broadcast",
                    "default": False,
                },
                "message": {
                    "type": "string",
                    "description": "Optional message payload when require_ack is True",
                    "default": None,
                },
            },
            "required": ["agent_id"],
        },
    },
    "acknowledge_broadcast": {
        "description": (
            "Acknowledge receipt of a broadcast. Called by recipient agents "
            "when they receive a broadcast_ack_request message."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "broadcast_id": {
                    "type": "integer",
                    "description": "ID of the broadcast to acknowledge",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Agent acknowledging the broadcast",
                },
            },
            "required": ["broadcast_id", "agent_id"],
        },
    },
    "wait_for_broadcast_acks": {
        "description": (
            "Poll until all expected broadcast acknowledgments are received or timeout expires. "
            "Use after broadcast with require_ack=True to wait for recipients."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "broadcast_id": {
                    "type": "integer",
                    "description": "ID of the broadcast to wait for",
                },
                "timeout_s": {
                    "type": "number",
                    "description": "Maximum seconds to wait (default: 30)",
                    "default": 30.0,
                },
            },
            "required": ["broadcast_id"],
        },
    },
    "wait_for_locks": {
        "description": (
            "Poll until all specified locks are released or a timeout expires. "
            "Useful for waiting for a parallel agent to finish before proceeding."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "document_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of document paths to wait on",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Agent doing the waiting",
                },
                "timeout_s": {
                    "type": "number",
                    "description": "Maximum seconds to wait (default: 60)",
                    "default": 60.0,
                },
            },
            "required": ["document_paths", "agent_id"],
        },
    },
    "await_agent": {
        "description": (
            "Wait for an agent to complete (deregister) before proceeding. "
            "Polls agent status until the agent is stopped or timeout expires. "
            "Use this to coordinate sequential dependencies between agents."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent to wait for",
                },
                "timeout_s": {
                    "type": "number",
                    "description": "Maximum seconds to wait (default: 60)",
                    "default": 60.0,
                },
            },
            "required": ["agent_id"],
        },
    },
}

