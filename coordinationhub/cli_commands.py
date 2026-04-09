"""CoordinationHub CLI command handlers.

Commands are organised into domain-specific sub-modules and re-exported here
so that cli.py's lazy importer only needs to import one module.
"""

from __future__ import annotations

# Re-export all command handlers from domain sub-modules
from .cli_agents import (
    cmd_serve,
    cmd_serve_mcp,
    cmd_status,
    cmd_register,
    cmd_heartbeat,
    cmd_deregister,
    cmd_list_agents,
    cmd_lineage,
    cmd_siblings,
)
from .cli_locks import (
    cmd_acquire_lock,
    cmd_release_lock,
    cmd_refresh_lock,
    cmd_lock_status,
    cmd_list_locks,
    cmd_release_agent_locks,
    cmd_reap_expired_locks,
    cmd_reap_stale_agents,
    cmd_broadcast,
    cmd_wait_for_locks,
)
from .cli_vis import (
    cmd_notify_change,
    cmd_get_notifications,
    cmd_prune_notifications,
    cmd_get_conflicts,
    cmd_load_spec,
    cmd_validate_spec,
    cmd_scan_project,
    cmd_dashboard,
    cmd_agent_status,
    cmd_assess,
    cmd_agent_tree,
)
