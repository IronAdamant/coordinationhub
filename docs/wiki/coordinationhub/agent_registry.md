# coordinationhub/agent_registry.py

Agent lifecycle primitives: register, heartbeat, deregister, reap/prune, plus lineage and descendant tracking. The `agents` table is created by `db.init_schema`; this module has no per-module init.

## Key Functions / Classes
- `register_agent(connect, agent_id, worktree_root, ...)` — upsert registration; rejects on PID collision (T1.2) and on the `MAX_AGENTS` (10,000) active ceiling (T3.9); records lineage + descendant rows in the same transaction (T1.20).
- `find_agent_by_raw_ide_id(connect, raw_ide_id, ...)` — map a raw IDE-specific ID back to a hub agent_id; vendor-scoped (T3.12), freshness-filtered (T1.17).
- `heartbeat(connect, agent_id)` — refresh `last_heartbeat`; reports `reason` (`not_registered` / `agent_<status>`) when no active row matched (T1.18).
- `deregister_agent(connect, agent_id)` — mark stopped, re-parent children to nearest *live* ancestor or root (T1.6), delete stale lineage rows.
- `list_agents(connect, active_only, stale_timeout, include_stale)` — list with staleness detection; stale rows filtered by default when `active_only` (T1.17).
- `reap_stale_agents(connect, timeout)` — mark stale agents stopped + orphan children inside one `BEGIN IMMEDIATE`; staleness re-verified in UPDATE WHERE to close the heartbeat TOCTOU (T1.6).
- `prune_stopped_agents(connect, retention_seconds)` — hard-delete week-old stopped tombstones plus their lineage/descendant/responsibility rows; skips agents with live children.
- `get_lineage` / `get_siblings` / `get_descendants_status` — ancestry, sibling, and descendant-status queries.
- `_record_descendant_relationship(conn, agent_id, parent_id)` — walks the ancestor chain at register time; `INSERT OR IGNORE` keeps re-registration idempotent.

## Design Notes
- Zero internal dependencies on other coordinationhub modules besides `db.ConnectFn`.
- Deregistration intentionally omits `locks_released` — `core_identity.deregister_agent` patches in the real count from `release_agent_locks` (T7.2).
- `lineage` tracks only the *active* spawning parent (for responsibility inheritance via `scan._get_spawned_agent_responsibilities`); the historical spawner stays in `agents.parent_id`.
- `get_lineage` ancestor entries carry each ancestor's own `parent_id` (T7.1 fixed a duplicated-id rendering bug); descendant walk and reparenting use visited sets to survive cycles.
- Historical records (messages, handoffs, tasks, broadcasts) are deliberately preserved on prune for post-mortem.

## Relationships
Imports: `coordinationhub.db` (ConnectFn)
Imported by: `identity_subsystem.py`, `visibility_subsystem.py`, `broadcast_subsystem.py`, `locking_subsystem.py`
