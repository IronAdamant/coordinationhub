# coordinationhub/agent_status.py

Agent status and file-map query helpers: update an agent's current task/scope, fetch full per-agent status, render the hierarchical agent tree, and map files to owning agents.

## Key Functions / Classes
- `update_agent_status_tool(connect, agent_id, current_task, scope)` — upsert `current_task`/`scope` into `agent_responsibilities`; `current_task` truncated to `MAX_CURRENT_TASK` (T6.14).
- `get_agent_status_tool(connect, agent_id, lineage_fn)` — full status bundle: agent row, responsibilities, owned files (with tasks), active locks, lineage (lineage is injected as a callable to avoid a hard dependency).
- `get_agent_tree_tool(connect, agent_id=None)` — hierarchical tree rooted at the given agent or the oldest active root; nodes carry role, current task, active locks (with region info), and `boundary_warning` when a lock crosses another agent's file ownership.
- `_render_rich_tree` / `_render_node` — project-management-style text rendering (box-drawing connectors, `◆` lock items, `⚠` ownership warnings).
- `get_file_agent_map_tool(connect, agent_id=None)` — file → agent + responsibility summary from `file_ownership` joined to `agent_responsibilities`.

## Design Notes
- Zero internal dependencies on other coordinationhub modules at module top; `limits` is imported function-locally.
- Tree recursion is capped at `MAX_AGENT_TREE_DEPTH = 100` with a visited set so a `parent_id` cycle (reparenting bugs under reap) cannot blow the Python stack (T1.14).
- Lock-ownership boundary query builds its `IN (...)` clause via string concatenation rather than an f-string to remove an interpolation footgun (T7.4) — placeholders remain `?`-bound.
- Lock validity in the tree uses `locked_at + lock_ttl > now`, matching the canonical expiry predicate used package-wide.
- `responsibilities` are stored as JSON text and decoded defensively (`or "[]"`).

## Relationships
Imports: `coordinationhub.limits` (function-local: MAX_CURRENT_TASK, truncate)
Imported by: `visibility_subsystem.py`
