"""Spawner tool schemas for CoordinationHub.

Pure data declarations — no logic.  Re-exported by :mod:`coordinationhub.schemas`.
"""

from __future__ import annotations


TOOL_SCHEMAS_SPAWNER: dict[str, dict] = {
    "spawn_subagent": {
        "description": (
            "Register intent to spawn a sub-agent and return its spawn ID. "
            "The parent agent calls this before the external system spawns the sub-agent. "
            "This creates a pending spawn record that the spawning system will consume "
            "when the agent is actually spawned, correlating via ``parent_agent_id``. "
            "Returns the spawn ID and pending spawn record."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "parent_agent_id": {
                    "type": "string",
                    "description": "Parent agent ID that intends to spawn the sub-agent",
                },
                "subagent_type": {
                    "type": "string",
                    "description": "Type of sub-agent to spawn (e.g. 'Explore', 'Plan', 'general-purpose')",
                },
                "description": {
                    "type": "string",
                    "description": "Description of the sub-agent's task",
                    "default": None,
                },
                "prompt": {
                    "type": "string",
                    "description": "Prompt or instructions for the sub-agent",
                    "default": None,
                },
                "source": {
                    "type": "string",
                    "description": "Source system that will perform the spawn (e.g. 'stdio_adapter', 'kimi_cli', 'cursor')",
                    "default": "external",
                },
            },
            "required": ["parent_agent_id", "subagent_type"],
        },
    },
    "report_subagent_spawned": {
        "description": (
            "Report that a sub-agent has been spawned by an external system. "
            "Any IDE/CLI (stdio_adapter, Kimi CLI, Cursor, etc.) calls this after "
            "spawning a sub-agent via its native mechanism. This consumes the "
            "pending spawn record created by ``spawn_subagent`` and links it to "
            "the actual child agent ID."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "parent_agent_id": {
                    "type": "string",
                    "description": "Parent agent ID that spawned the sub-agent",
                },
                "subagent_type": {
                    "type": "string",
                    "description": "Type of sub-agent that was spawned",
                    "default": None,
                },
                "child_agent_id": {
                    "type": "string",
                    "description": "Actual agent ID of the spawned sub-agent",
                },
                "source": {
                    "type": "string",
                    "description": "Source system that performed the spawn (e.g. 'stdio_adapter', 'kimi_cli')",
                    "default": "external",
                },
                "caller_agent_id": {
                    "type": "string",
                    "description": (
                        "Optional caller assertion (T2.4). When supplied, must equal "
                        "parent_agent_id — rejects sibling agents trying to claim "
                        "another parent's child and hijack the spawner.registered event."
                    ),
                },
            },
            "required": ["parent_agent_id", "child_agent_id"],
        },
    },
    "get_pending_spawns": {
        "description": (
            "Get pending (or all) spawn requests for a parent agent. "
            "Returns spawn records with status: pending | registered | expired."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "parent_agent_id": {
                    "type": "string",
                    "description": "Parent agent ID to get pending spawns for",
                },
                "include_consumed": {
                    "type": "boolean",
                    "description": "Include consumed/registered spawns in results",
                    "default": False,
                },
            },
            "required": ["parent_agent_id"],
        },
    },
    "await_subagent_registration": {
        "description": (
            "Poll until a pending spawn is consumed (sub-agent registered) or timeout. "
            "The parent agent calls this after ``spawn_subagent`` to wait for the "
            "external spawning system to report that the sub-agent is alive. "
            "Returns the consumed spawn record on success, or timed_out:true if the "
            "sub-agent did not register within the timeout."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "parent_agent_id": {
                    "type": "string",
                    "description": "Parent agent ID that spawned the sub-agent",
                },
                "subagent_type": {
                    "type": "string",
                    "description": "Wait for a specific sub-agent type (omit to wait for any)",
                    "default": None,
                },
                "timeout": {
                    "type": "number",
                    "description": "Timeout in seconds (default: 300)",
                    "default": 300.0,
                },
            },
            "required": ["parent_agent_id"],
        },
    },
    "request_subagent_deregistration": {
        "description": (
            "Request graceful deregistration of a child agent. "
            "Sets ``stop_requested_at`` on the child agent. The child is expected "
            "to poll ``is_subagent_stop_requested`` and call ``deregister_agent`` "
            "if the stop flag is set. After a timeout, the caller should escalate "
            "to ``deregister_agent`` directly. "
            "Returns ``requested`` if the stop flag was set, ``not_found`` if "
            "the child agent does not exist or is not active."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "parent_agent_id": {
                    "type": "string",
                    "description": "Parent agent ID making the request",
                },
                "child_agent_id": {
                    "type": "string",
                    "description": "Child agent ID to request stop for",
                },
            },
            "required": ["parent_agent_id", "child_agent_id"],
        },
    },
    "is_subagent_stop_requested": {
        "description": (
            "Check if a stop has been requested for this agent. "
            "The agent should call this periodically and deregister if "
            "the stop flag is set."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent ID to check stop-request flag for",
                },
            },
            "required": ["agent_id"],
        },
    },
    "await_subagent_stopped": {
        "description": (
            "Poll until a child agent is stopped or the timeout is reached. "
            "Returns ``stopped: True`` if the child called ``deregister_agent`` "
            "within the timeout. Returns ``timed_out: True`` with ``escalate: True`` "
            "if the child did not stop in time — the caller should then call "
            "``deregister_agent`` directly to force cleanup."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "child_agent_id": {
                    "type": "string",
                    "description": "Child agent ID to wait for",
                },
                "timeout": {
                    "type": "number",
                    "description": "Timeout in seconds (default: 30)",
                    "default": 30.0,
                },
            },
            "required": ["child_agent_id"],
        },
    },
}

