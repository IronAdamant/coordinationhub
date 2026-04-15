#!/usr/bin/env python3
"""Programmatically update schemas.py for Phase 1 tool consolidation."""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_PATH = REPO_ROOT / "coordinationhub" / "schemas.py"
text = SCHEMAS_PATH.read_text(encoding="utf-8")

# ------------------------------------------------------------------
# 1. TOOL_SCHEMAS_MESSAGING -> send_message + manage_messages
# ------------------------------------------------------------------
messaging_new = '''TOOL_SCHEMAS_MESSAGING: dict[str, dict] = {
    "send_message": {
        "description": (
            "Send a direct message to another agent. "
            "Messages are stored and can be retrieved via manage_messages. "
            "Use for query/response patterns between agents."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "from_agent_id": {"type": "string", "description": "Agent sending the message"},
                "to_agent_id": {"type": "string", "description": "Agent to receive the message"},
                "message_type": {"type": "string", "description": "Type of message (e.g. 'query', 'response', 'notification')"},
                "payload": {"type": "object", "description": "Optional JSON payload with message data"},
            },
            "required": ["from_agent_id", "to_agent_id", "message_type"],
        },
    },
    "manage_messages": {
        "description": "Unified messaging: send | get | mark_read. Use action='get' to retrieve messages.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["send", "get", "mark_read"], "description": "Messaging action"},
                "agent_id": {"type": "string", "description": "Agent ID for get/mark_read"},
                "from_agent_id": {"type": "string", "description": "Required for send"},
                "to_agent_id": {"type": "string", "description": "Required for send"},
                "message_type": {"type": "string", "description": "Required for send"},
                "payload": {"type": "object", "description": "Optional payload for send"},
                "unread_only": {"type": "boolean", "default": False, "description": "Only unread messages for get"},
                "limit": {"type": "integer", "default": 50, "description": "Max messages for get"},
                "message_ids": {"type": "array", "items": {"type": "integer"}, "description": "Specific IDs for mark_read"},
            },
            "required": ["action", "agent_id"],
        },
    },
}'''
text = re.sub(
    r'TOOL_SCHEMAS_MESSAGING: dict\[str, dict\] = \{.*?\n\}',
    messaging_new,
    text,
    count=1,
    flags=re.DOTALL,
)

# ------------------------------------------------------------------
# 2. TOOL_SCHEMAS_CHANGE -> notify_change + get_notifications (expanded)
# ------------------------------------------------------------------
change_new = '''TOOL_SCHEMAS_CHANGE: dict[str, dict] = {
    "notify_change": {
        "description": (
            "Record a change event so other agents can poll for it. "
            "Call after making a change to a shared document."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "document_path": {"type": "string", "description": "Path to the changed document"},
                "change_type": {"type": "string", "description": "Type of change (e.g. 'created', 'modified', 'deleted')"},
                "agent_id": {"type": "string", "description": "Agent that made the change"},
            },
            "required": ["document_path", "change_type", "agent_id"],
        },
    },
    "get_notifications": {
        "description": (
            "Poll for change notifications since a timestamp. "
            "If timeout_s > 0, long-polls until new notifications arrive. "
            "If prune_max_age_seconds or prune_max_entries is provided, prunes old data first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "since": {"type": "number", "description": "Unix timestamp to poll from", "default": None},
                "exclude_agent": {"type": "string", "description": "Agent ID to exclude", "default": None},
                "limit": {"type": "integer", "description": "Maximum notifications", "default": 100},
                "agent_id": {"type": "string", "description": "Waiter identity when timeout_s > 0", "default": None},
                "timeout_s": {"type": "number", "description": "Long-poll timeout (0 = immediate)", "default": 0.0},
                "poll_interval_s": {"type": "number", "description": "Poll interval", "default": 2.0},
                "prune_max_age_seconds": {"type": "number", "description": "Prune before returning", "default": None},
                "prune_max_entries": {"type": "integer", "description": "Prune before returning", "default": None},
            },
        },
    },
}'''
text = re.sub(
    r'TOOL_SCHEMAS_CHANGE: dict\[str, dict\] = \{.*?\n\}',
    change_new,
    text,
    count=1,
    flags=re.DOTALL,
)

# ------------------------------------------------------------------
# 3. TOOL_SCHEMAS_VISIBILITY -> remove validate_graph
# ------------------------------------------------------------------
text = re.sub(
    r'    "validate_graph": \{\n        "description": \(\n            "Validate the currently loaded coordination graph schema\. "\n            "Returns validation errors if invalid, or an empty error list if valid\."\n        \),\n        "parameters": \{\n            "type": "object",\n            "properties": \{\},\n        \},\n    \},\n',
    '',
    text,
    count=1,
)

# ------------------------------------------------------------------
# 4. TOOL_SCHEMAS_INTENT -> manage_work_intents
# ------------------------------------------------------------------
intent_new = '''TOOL_SCHEMAS_INTENT: dict[str, dict] = {
    "manage_work_intents": {
        "description": "Unified work intent management: declare | get | clear.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["declare", "get", "clear"], "description": "Intent action"},
                "agent_id": {"type": "string", "description": "Agent ID"},
                "document_path": {"type": "string", "description": "Required for declare"},
                "intent": {"type": "string", "description": "Required for declare"},
                "ttl": {"type": "number", "default": 60.0, "description": "Seconds until intent expires"},
            },
            "required": ["action", "agent_id"],
        },
    },
}'''
text = re.sub(
    r'TOOL_SCHEMAS_INTENT: dict\[str, dict\] = \{.*?\n\}',
    intent_new,
    text,
    count=1,
    flags=re.DOTALL,
)

# ------------------------------------------------------------------
# 5. TOOL_SCHEMAS_HANDOFFS -> wait_for_handoff only
# ------------------------------------------------------------------
handoffs_new = '''TOOL_SCHEMAS_HANDOFFS: dict[str, dict] = {
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
}'''
text = re.sub(
    r'TOOL_SCHEMAS_HANDOFFS: dict\[str, dict\] = \{.*?\n\}',
    handoffs_new,
    text,
    count=1,
    flags=re.DOTALL,
)

# ------------------------------------------------------------------
# 6. TOOL_SCHEMAS_DEPS -> manage_dependencies only
# ------------------------------------------------------------------
deps_new = '''TOOL_SCHEMAS_DEPS: dict[str, dict] = {
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
}'''
text = re.sub(
    r'TOOL_SCHEMAS_DEPS: dict\[str, dict\] = \{.*?\n\}',
    deps_new,
    text,
    count=1,
    flags=re.DOTALL,
)

# ------------------------------------------------------------------
# 7. TOOL_SCHEMAS_DLQ -> task_failures
# ------------------------------------------------------------------
dlq_new = '''TOOL_SCHEMAS_DLQ: dict[str, dict] = {
    "task_failures": {
        "description": (
            "Unified dead-letter queue operations. "
            "action='retry' resurrects a task. "
            "action='list_dead_letter' lists DLQ tasks. "
            "action='history' gets failure history for a task."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["retry", "list_dead_letter", "history"], "description": "DLQ action"},
                "task_id": {"type": "string", "description": "Required for retry/history", "default": None},
                "limit": {"type": "integer", "default": 50, "description": "Max results for list_dead_letter"},
            },
            "required": ["action"],
        },
    },
}'''
text = re.sub(
    r'TOOL_SCHEMAS_DLQ: dict\[str, dict\] = \{.*?\n\}',
    dlq_new,
    text,
    count=1,
    flags=re.DOTALL,
)

# ------------------------------------------------------------------
# 8. TOOL_SCHEMAS_LEASES -> acquire + manage_leases
# ------------------------------------------------------------------
leases_new = '''TOOL_SCHEMAS_LEASES: dict[str, dict] = {
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
                "ttl": {"type": "number", "description": "TTL for acquire/claim", "default": None},
            },
            "required": ["action", "agent_id"],
        },
    },
}'''
text = re.sub(
    r'TOOL_SCHEMAS_LEASES: dict\[str, dict\] = \{.*?\n\}',
    leases_new,
    text,
    count=1,
    flags=re.DOTALL,
)

# ------------------------------------------------------------------
# Update header comment count
# ------------------------------------------------------------------
text = text.replace("all 31 MCP tools", "all 50 MCP tools")
text = text.replace("# Messaging (3 tools)", "# Messaging (2 tools)")
text = text.replace("# Change Awareness (3 tools)", "# Change Awareness (2 tools)")
text = text.replace("# Graph & Visibility (8 tools)", "# Graph & Visibility (7 tools)")
text = text.replace("# Work Intent Board (3 tools)", "# Work Intent Board (1 tool)")
text = text.replace("# Handoffs (5 tools)", "# Handoffs (1 tool)")
text = text.replace("# Cross-Agent Dependencies (4 tools)", "# Cross-Agent Dependencies (1 tool)")
text = text.replace("# Dead Letter Queue (3 tools)", "# Dead Letter Queue (1 tool)")
text = text.replace("# Leases — HA Coordinator Leadership (5 tools)", "# Leases — HA Coordinator Leadership (2 tools)")

SCHEMAS_PATH.write_text(text, encoding="utf-8")
print("schemas.py updated")
