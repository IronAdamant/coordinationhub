"""HA Coordinator Leases tool schemas for CoordinationHub.

Pure data declarations — no logic.  Re-exported by :mod:`coordinationhub.schemas`.
"""

from __future__ import annotations


TOOL_SCHEMAS_LEASES: dict[str, dict] = {
    "acquire_coordinator_lease": {
        "description": (
            "Attempt to acquire the coordinator leadership lease (COORDINATOR_LEADER). "
            "If acquired, this agent becomes the active coordinator."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "Agent ID attempting to acquire the lease"},
                "ttl": {"type": "number", "description": "Lease TTL in seconds (default: 10)"},
            },
            "required": ["agent_id"],
        },
    },
    "manage_leases": {
        "description": (
            "Unified lease management. action='acquire' | 'refresh' | 'release' | "
            "'get' (returns current leader) | 'claim' (failover claim)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["acquire", "refresh", "release", "get", "claim"], "description": "Lease action"},
                "agent_id": {"type": "string", "description": "Agent ID"},
                "ttl": {"type": "number", "description": "TTL for acquire/claim (seconds, default 10 at the engine)", "minimum": 0},
            },
            "required": ["action", "agent_id"],
        },
    },
}

