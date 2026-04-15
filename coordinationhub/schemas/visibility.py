"""Graph & Visibility tool schemas for CoordinationHub.

Pure data declarations — no logic.  Re-exported by :mod:`coordinationhub.schemas`.
"""

from __future__ import annotations


TOOL_SCHEMAS_VISIBILITY: dict[str, dict] = {
    "load_coordination_spec": {
        "description": (
            "Reload the coordination spec from disk. Returns whether a graph "
            "was found and loaded, plus the graph's agent list."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to coordination_spec.yaml or .json (default: project root)",
                    "default": None,
                },
            },
        },
    },
    "scan_project": {
        "description": (
            "Perform a file ownership scan of the worktree_root. "
            "For every tracked file (.py, .md, .json, .yaml, .txt, .toml), "
            "upserts an entry into the file_ownership table. "
            "Returns the count of files scanned and owned."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "worktree_root": {
                    "type": "string",
                    "description": "Root directory to scan (default: engine's project root)",
                    "default": None,
                },
                "extensions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "File extensions to scan (default: py, md, json, yaml, txt, toml)",
                    "default": None,
                },
            },
        },
    },
    "get_agent_status": {
        "description": (
            "Get full status for a specific agent: current task, responsibilities "
            "(from the coordination graph), owned files, lineage, and lock state."
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
    "get_file_agent_map": {
        "description": (
            "Get a map of all tracked files to their assigned Agent ID "
            "and responsibility summary. Returns the full file_ownership table "
            "joined with agent responsibilities."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Filter to files owned by a specific agent",
                    "default": None,
                },
            },
        },
    },
    "update_agent_status": {
        "description": (
            "Update the current task description and/or declared scope for an agent. "
            "Stored in agent_responsibilities for visibility. "
            "Scope is a list of path prefixes that define the agent's working domain. "
            "If an agent declares a scope, lock acquisitions outside that scope are denied."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent updating its status",
                },
                "current_task": {
                    "type": "string",
                    "description": "Human-readable description of what this agent is doing",
                },
                "scope": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of path prefixes defining the agent's scope (e.g. ['/src/services/', '/src/models/'])",
                },
            },
            "required": ["agent_id"],
        },
    },
    "run_assessment": {
        "description": (
            "Run an assessment suite or score the current live session. "
            "If suite_path is provided, loads the JSON suite file and scores it. "
            "If suite_path is omitted, synthesizes a live session trace from DB state "
            "(agents, notifications, lineage) — no hand-authored suite required. "
            "Stores results in SQLite and returns a Markdown or JSON report."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "suite_path": {
                    "type": "string",
                    "description": "Path to the JSON test suite file (omit for live session scoring)",
                    "default": None,
                },
                "format": {
                    "type": "string",
                    "description": "Output format: 'markdown' (default) or 'json'",
                    "default": "markdown",
                },
                "graph_agent_id": {
                    "type": "string",
                    "description": "Optional: filter traces to those involving this graph agent role",
                    "default": None,
                },
                "scope": {
                    "type": "string",
                    "description": "'project' (default) restricts to the current worktree; 'all' scores every agent in the DB",
                    "default": "project",
                },
            },
        },
    },
    "get_agent_tree": {
        "description": (
            "Get the hierarchical agent tree with live work status. Each node shows "
            "the agent's current task, active file locks (with type and region), and "
            "boundary crossing warnings when locking files owned by other agents. "
            "Use as a shared situational reference — any agent sees the same live state. "
            "If agent_id is omitted, returns the tree rooted at the oldest active root agent."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Root of the tree to query (default: oldest active root agent)",
                    "default": None,
                },
            },
        },
    },
}

