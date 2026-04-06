"""Agent lifecycle: register, heartbeat, deregister, lineage management.

Re-exports from domain-specific sub-modules:
- registry_ops.py: register, heartbeat, deregister, init_agents_table
- registry_query.py: list_agents, reap_stale_agents, get_lineage, get_siblings

Zero third-party dependencies.
"""

from __future__ import annotations

from .registry_ops import (
    init_agents_table,
    register_agent,
    heartbeat,
    deregister_agent,
)
from .registry_query import (
    list_agents,
    reap_stale_agents,
    get_lineage,
    get_siblings,
)
