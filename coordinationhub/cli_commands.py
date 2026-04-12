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
    cmd_await_agent,
    cmd_send_message,
    cmd_get_messages,
    cmd_mark_messages_read,
    cmd_acknowledge_handoff,
    cmd_complete_handoff,
    cmd_cancel_handoff,
    cmd_get_handoffs,
)
from .cli_vis import (
    cmd_notify_change,
    cmd_get_notifications,
    cmd_prune_notifications,
    cmd_get_conflicts,
    cmd_contention_hotspots,
    cmd_load_spec,
    cmd_validate_spec,
    cmd_scan_project,
    cmd_dashboard,
    cmd_agent_status,
    cmd_assess,
    cmd_assess_session,
    cmd_agent_tree,
)
from .cli_setup import (
    cmd_doctor,
    cmd_init,
    cmd_watch,
)
from .cli_tasks import (
    cmd_create_task,
    cmd_assign_task,
    cmd_update_task_status,
    cmd_get_task,
    cmd_get_child_tasks,
    cmd_get_tasks_by_agent,
    cmd_get_all_tasks,
)
from .cli_intent import (
    cmd_declare_work_intent,
    cmd_get_work_intents,
    cmd_clear_work_intent,
)
from .cli_deps import (
    cmd_declare_dependency,
    cmd_check_dependencies,
    cmd_satisfy_dependency,
    cmd_get_blockers,
    cmd_assert_can_start,
    cmd_get_all_dependencies,
)
