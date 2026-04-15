"""Cross-Agent Dependencies tool schemas for CoordinationHub.

Pure data declarations — no logic.  Re-exported by :mod:`coordinationhub.schemas`.
"""

from __future__ import annotations


TOOL_SCHEMAS_DEPS: dict[str, dict] = {
    "manage_dependencies": {
        "description": (
            "Unified dependency management. mode='declare' creates a dependency. "
            "mode='check'/'blockers'/'assert' queries blockers. "
            "mode='satisfy' marks one satisfied. mode='list' lists all. "
            "mode='wait' polls until a dep_id is satisfied."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["declare", "check", "blockers", "assert", "satisfy", "list", "wait"], "description": "Action mode"},
                "agent_id": {"type": "string", "description": "Agent for check/blockers/assert/list", "default": None},
                "dependent_agent_id": {"type": "string", "description": "Required for declare", "default": None},
                "depends_on_agent_id": {"type": "string", "description": "Required for declare", "default": None},
                "depends_on_task_id": {"type": "string", "description": "Optional for declare", "default": None},
                "condition": {"type": "string", "default": "task_completed", "description": "Condition for declare"},
                "dep_id": {"type": "integer", "description": "Required for satisfy/wait", "default": None},
                "timeout_s": {"type": "number", "default": 60.0, "description": "Wait timeout"},
                "poll_interval_s": {"type": "number", "default": 2.0, "description": "Wait poll interval"},
            },
            "required": ["mode"],
        },
    },
}

