"""Audit & Status tool schemas for CoordinationHub.

Pure data declarations — no logic.  Re-exported by :mod:`coordinationhub.schemas`.
"""

from __future__ import annotations


TOOL_SCHEMAS_AUDIT: dict[str, dict] = {
    "get_conflicts": {
        "description": (
            "Query the conflict log for lock steals and ownership violations. "
            "Useful for post-mortems and debugging agent interactions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "document_path": {
                    "type": "string",
                    "description": "Filter to a specific document path",
                    "default": None,
                },
                "agent_id": {
                    "type": "string",
                    "description": "Filter to conflicts involving this agent",
                    "default": None,
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of conflicts to return",
                    "default": 20,
                },
            },
        },
    },
    "get_contention_hotspots": {
        "description": (
            "Rank files by lock contention frequency. Returns files ordered by "
            "how many lock conflicts they've been involved in, along with the "
            "agents involved. Use to identify coordination chokepoints — files "
            "that multiple agents need access to and that cause frequent contention."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of hotspots to return",
                    "default": 10,
                },
            },
        },
    },
    "status": {
        "description": (
            "Get a summary of the coordination system state: "
            "registered agents, active locks, pending notifications, conflicts, "
            "and whether a coordination graph is loaded."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
}

