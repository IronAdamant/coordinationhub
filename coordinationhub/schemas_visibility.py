"""Graph & Visibility tool schemas (8 tools — new in v0.3.1)."""

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
    "validate_graph": {
        "description": (
            "Validate the currently loaded coordination graph schema. "
            "Returns validation errors if invalid, or an empty error list if valid."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
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
            "Update the current task description for an agent. "
            "Stored in agent_responsibilities.current_task for visibility. "
            "Use this so other agents and human developers can see what is in flight."
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
            },
            "required": ["agent_id", "current_task"],
        },
    },
    "run_assessment": {
        "description": (
            "Run an assessment suite against the loaded coordination graph. "
            "Loads the suite JSON file, scores all traces on the defined metrics, "
            "outputs a report, and stores results in SQLite for historical comparison. "
            "Use graph_agent_id to filter traces to a specific agent role."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "suite_path": {
                    "type": "string",
                    "description": "Path to the JSON test suite file",
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
            },
            "required": ["suite_path"],
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
