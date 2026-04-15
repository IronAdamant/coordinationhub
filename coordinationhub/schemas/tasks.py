"""Task Registry tool schemas for CoordinationHub.

Pure data declarations — no logic.  Re-exported by :mod:`coordinationhub.schemas`.
"""

from __future__ import annotations


TOOL_SCHEMAS_TASKS: dict[str, dict] = {
    "create_task": {
        "description": (
            "Create a new task in the shared task registry. "
            "The creating agent (parent_agent_id) assigns the task to a child or sibling agent."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Unique task ID (e.g. hub.12345.0.task.0)",
                },
                "parent_agent_id": {
                    "type": "string",
                    "description": "Agent creating this task",
                },
                "description": {
                    "type": "string",
                    "description": "What this task involves",
                },
                "depends_on": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Task IDs that must complete before this one starts",
                },
                "priority": {
                    "type": "integer",
                    "description": "Task priority (higher values execute first; default 0)",
                    "default": 0,
                },
            },
            "required": ["task_id", "parent_agent_id", "description"],
        },
    },
    "assign_task": {
        "description": "Assign a task to a specific agent.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Task to assign",
                },
                "assigned_agent_id": {
                    "type": "string",
                    "description": "Agent to assign the task to",
                },
            },
            "required": ["task_id", "assigned_agent_id"],
        },
    },
    "update_task_status": {
        "description": (
            "Update a task's status. When a task is completed, include a summary "
            "that a parent agent can compress upward in the hierarchy."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Task to update",
                },
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed", "blocked", "failed"],
                    "description": "New status for the task",
                },
                "summary": {
                    "type": "string",
                    "description": "Completion summary written by the agent (used for compression chains)",
                },
                "blocked_by": {
                    "type": "string",
                    "description": "Task ID that is blocking this task",
                },
                "error": {
                    "type": "string",
                    "description": "Error message when marking a task as failed (records to dead letter queue)",
                },
            },
            "required": ["task_id", "status"],
        },
    },
    "query_tasks": {
        "description": (
            "Unified task query. query_type='task' fetches one task by ID. "
            "query_type='child' fetches tasks created by an agent. "
            "query_type='by_agent' fetches tasks assigned to an agent. "
            "query_type='all' fetches every task. "
            "query_type='subtasks' fetches direct subtasks of a task. "
            "query_type='tree' fetches a task and its nested subtasks."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query_type": {
                    "type": "string",
                    "enum": ["task", "child", "by_agent", "all", "subtasks", "tree"],
                    "description": "Type of query to perform",
                },
                "task_id": {
                    "type": "string",
                    "description": "Task ID (required for query_type='task')",
                    "default": None,
                },
                "parent_agent_id": {
                    "type": "string",
                    "description": "Agent ID (required for query_type='child')",
                    "default": None,
                },
                "assigned_agent_id": {
                    "type": "string",
                    "description": "Agent ID (required for query_type='by_agent')",
                    "default": None,
                },
                "parent_task_id": {
                    "type": "string",
                    "description": "Parent task ID (required for query_type='subtasks')",
                    "default": None,
                },
                "root_task_id": {
                    "type": "string",
                    "description": "Root task ID (required for query_type='tree')",
                    "default": None,
                },
            },
            "required": ["query_type"],
        },
    },
    "create_subtask": {
        "description": (
            "Create a subtask under an existing parent task. "
            "The subtask inherits context from its parent and can be nested further. "
            "Use get_task_tree to retrieve the full hierarchy."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Unique subtask ID (e.g. parent_task_id + '.0')",
                },
                "parent_task_id": {
                    "type": "string",
                    "description": "ID of the parent task this subtask belongs to",
                },
                "parent_agent_id": {
                    "type": "string",
                    "description": "Agent creating this subtask",
                },
                "description": {
                    "type": "string",
                    "description": "What this subtask involves",
                },
                "depends_on": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Task IDs that must complete before this subtask starts",
                },
                "priority": {
                    "type": "integer",
                    "description": "Subtask priority (higher values execute first; default 0)",
                    "default": 0,
                },
            },
            "required": ["task_id", "parent_task_id", "parent_agent_id", "description"],
        },
    },

    "wait_for_task": {
        "description": (
            "Poll until a task reaches a terminal state (completed or failed) "
            "or the timeout expires. Use this to coordinate sequential dependencies "
            "between tasks when depends_on alone is not sufficient (e.g., waiting "
            "for a task completed by an external agent or system)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Task to wait on",
                },
                "timeout_s": {
                    "type": "number",
                    "description": "Maximum seconds to wait (default: 60)",
                    "default": 60.0,
                },
                "poll_interval_s": {
                    "type": "number",
                    "description": "Polling interval in seconds (default: 2)",
                    "default": 2.0,
                },
            },
            "required": ["task_id"],
        },
    },
    "get_available_tasks": {
        "description": (
            "Return tasks whose depends_on are all satisfied (completed) and "
            "that are not currently claimed. A task is \"available\" if its status "
            "is \"pending\" and all tasks in its depends_on list have status \"completed\". "
            "Use this to find work that can be picked up by an idle agent."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Optional agent ID to filter to tasks assigned to this agent",
                },
            },
        },
    },
}

