"""Agent graph responsibilities storage helper.

Stores or updates an agent's role/responsibilities mapping from the
coordination graph. Zero internal dependencies on other coordinationhub modules.
"""

from __future__ import annotations

import json
import time as _time
from typing import Any, Callable


def store_responsibilities(
    connect: Callable[[], Any],
    agent_id: str,
    graph_agent_id: str,
    role: str,
    model: str,
    responsibilities: list[str],
) -> None:
    """Store or update an agent's responsibilities from the coordination graph."""
    now = _time.time()
    with connect() as conn:
        conn.execute("""
            INSERT INTO agent_responsibilities
            (agent_id, graph_agent_id, role, model, responsibilities, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                graph_agent_id = excluded.graph_agent_id,
                role = excluded.role,
                model = excluded.model,
                responsibilities = excluded.responsibilities,
                updated_at = excluded.updated_at
        """, (agent_id, graph_agent_id, role, model, json.dumps(responsibilities), now))
