"""Context bundle builder for CoordinationHub agent registration responses.

Assembles the full context dict returned by ``register_agent`` — including
sibling agents, active locks, coordination URLs, and graph-responsibility
data. Zero internal dependencies on other coordinationhub modules.
"""

from __future__ import annotations

import json
import os
import time
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    import graphs as _g

DEFAULT_PORT = 9877


def build_context_bundle(
    connect_fn: Callable[[], Any],
    agent_id: str,
    parent_id: str | None,
    project_root: str | None,
    graph_getter: Callable[[], Any],
    list_agents_fn: Callable[[Callable[[], Any], bool, float], list[dict[str, Any]]],
    default_port: int = DEFAULT_PORT,
) -> dict[str, Any]:
    """Assemble and return a context bundle for *agent_id*.

    Args:
        connect_fn: callable returning a SQLite connection (context manager)
        agent_id: the registering agent's ID
        parent_id: the parent agent ID, if any
        project_root: resolved project root path string
        graph_getter: callable returning the current CoordinationGraph or None
        list_agents_fn: callable(connect_fn, active_only, stale_timeout) -> agent list
        default_port: default HTTP port for the coordination URL

    Returns a dict containing the agent's identity, parent/child lineage, active
    locks held by other agents, recent change notifications, and coordination
    URL. Other MCP servers (Stele, Chisel, Trammel) are not included — configure
    those via their own environment variables if they are running.
    """
    agents = list_agents_fn(connect_fn, active_only=True, stale_timeout=600.0)
    with connect_fn() as conn:
        locks = conn.execute(
            "SELECT document_path, locked_by, locked_at, lock_ttl FROM document_locks"
        ).fetchall()
        active_locks = []
        now = time.time()
        for row in locks:
            if now <= row["locked_at"] + row["lock_ttl"]:
                active_locks.append({
                    "document_path": row["document_path"],
                    "locked_by": row["locked_by"],
                    "expires_at": row["locked_at"] + row["lock_ttl"],
                })
        notifs = conn.execute(
            "SELECT document_path, change_type, agent_id, created_at "
            "FROM change_notifications WHERE created_at > ? ORDER BY created_at DESC LIMIT 20",
            (now - 300,),
        ).fetchall()
        resp_row = conn.execute(
            "SELECT graph_agent_id, role, responsibilities, current_task "
            "FROM agent_responsibilities WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        owned_files = conn.execute(
            "SELECT document_path FROM file_ownership WHERE assigned_agent_id = ?", (agent_id,)
        ).fetchall()
    resp = dict(resp_row) if resp_row else {}
    responsibilities = json.loads(resp.get("responsibilities", "[]")) if resp else []
    bundle: dict[str, Any] = {
        "agent_id": agent_id,
        "parent_id": parent_id,
        "worktree_root": project_root or os.getcwd(),
        "registered_agents": [
            {"agent_id": a["agent_id"], "status": a["status"], "last_heartbeat": a["last_heartbeat"]}
            for a in agents
        ],
        "active_locks": active_locks,
        "pending_notifications": [dict(n) for n in notifs],
        "coordination_url": os.environ.get(
            "COORDINATIONHUB_COORDINATION_URL",
            f"http://localhost:{default_port}",
        ),
        "graph_loaded": graph_getter() is not None,
    }
    if resp:
        bundle["responsibilities"] = responsibilities
        bundle["role"] = resp.get("role")
        bundle["graph_agent_id"] = resp.get("graph_agent_id")
        bundle["current_task"] = resp.get("current_task")
    if owned_files:
        bundle["owned_files"] = [f["document_path"] for f in owned_files]
    return bundle
