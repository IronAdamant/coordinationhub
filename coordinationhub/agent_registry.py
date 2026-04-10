"""Agent lifecycle: register, heartbeat, deregister, lineage management.

Re-exports from domain-specific sub-modules:
- registry_ops.py: register, heartbeat, deregister
- registry_query.py: list_agents, reap_stale_agents, get_lineage, get_siblings

The ``agents`` table is created once by ``db.init_schema`` — no per-module
init function is needed.
"""

from __future__ import annotations

from .registry_ops import (
    register_agent,
    heartbeat,
    deregister_agent,
    find_agent_by_claude_id,
)
from .registry_query import (
    list_agents,
    reap_stale_agents,
    get_lineage,
    get_siblings,
)
