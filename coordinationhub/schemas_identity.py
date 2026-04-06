"""Identity & Registration tool schemas (6 tools)."""

from __future__ import annotations

TOOL_SCHEMAS_IDENTITY: dict[str, dict] = {
    "register_agent": {
        "description": (
            "Register an agent with the coordination hub and receive a context bundle "
            "containing sibling agents, active locks, coordination URLs, and (if a "
            "coordination graph is loaded) the agent's responsibilities and owned files."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Unique agent identifier (e.g. hub.12345.0)",
                },
                "parent_id": {
                    "type": "string",
                    "description": "Parent agent ID if this is a spawned sub-agent",
                    "default": None,
                },
                "graph_agent_id": {
                    "type": "string",
                    "description": "ID in the coordination graph this agent implements (e.g. 'planner')",
                    "default": None,
                },
                "worktree_root": {
                    "type": "string",
                    "description": "Worktree root path (defaults to project root)",
                    "default": None,
                },
            },
            "required": ["agent_id"],
        },
    },
    "heartbeat": {
        "description": (
            "Send a heartbeat to keep the agent registered and alive. "
            "Call at least every 30 seconds."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent identifier",
                },
            },
            "required": ["agent_id"],
        },
    },
    "deregister_agent": {
        "description": (
            "Deregister an agent, orphan its children to the grandparent, "
            "and release all its locks. Use when an agent is done."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent identifier to deregister",
                },
            },
            "required": ["agent_id"],
        },
    },
    "list_agents": {
        "description": (
            "List all registered agents. Shows active agents by default. "
            "Includes heartbeat age so you can detect stale agents."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "active_only": {
                    "type": "boolean",
                    "description": "Filter to active (non-stopped) agents only",
                    "default": True,
                },
                "stale_timeout": {
                    "type": "number",
                    "description": "Seconds after which an agent is considered stale",
                    "default": 600.0,
                },
            },
        },
    },
    "get_lineage": {
        "description": (
            "Get the ancestor chain (parent → grandparent) and all descendants "
            "(direct children, grandchildren) of a given agent."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent to query",
                },
            },
            "required": ["agent_id"],
        },
    },
    "get_siblings": {
        "description": (
            "Get all agents that share the same parent as the given agent. "
            "Useful for coordination before taking a shared action."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent whose siblings to find",
                },
            },
            "required": ["agent_id"],
        },
    },
}
