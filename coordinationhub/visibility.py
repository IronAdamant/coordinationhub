"""Visibility helpers for CoordinationHub: file ownership scan, agent status, file map.

Re-exports from domain-specific sub-modules:
- scan.py: file ownership scan (graph-role-aware)
- agent_status.py: agent status and file map query helpers
- responsibilities.py: agent graph responsibility storage

Zero third-party dependencies.
"""

from __future__ import annotations

from .scan import scan_project_tool, _role_based_agent, _get_spawned_agent_responsibilities
from .agent_status import update_agent_status_tool, get_agent_status_tool, get_file_agent_map_tool
from .responsibilities import store_responsibilities
