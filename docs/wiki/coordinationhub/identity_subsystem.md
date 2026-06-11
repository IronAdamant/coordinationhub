# coordinationhub/identity_subsystem.py

Agent identity: registration (with context bundle), heartbeat, deregistration, lineage/sibling queries, stopped-agent pruning, and IDE-id lookup. Twelfth and final T6.22 extraction from `core_identity.IdentityMixin` — with it the engine MRO contains zero mixins; exposed as `engine._identity`.

## Key Functions / Classes
- `Identity` — subsystem class; ctor takes `connect_fn`, `publish_event_fn`, `locking` (Locking), `lease` (Lease), `effective_worktree_root_getter`, `read_only_connect_fn`, `generate_agent_id_fn`, `default_port`, `heartbeat_interval`.
- `Identity.register_agent(agent_id, parent_id, graph_agent_id, worktree_root, raw_ide_id, ide_vendor)` — register, publish `agent.registered`, optionally store graph responsibilities, return context bundle.
- `Identity.heartbeat(agent_id)` — update `last_heartbeat`; T1.18 propagates the primitive's failure `reason` (`not_registered` vs `agent_stopped`).
- `Identity.deregister_agent(agent_id)` — deregister, release locks via injected `Locking`, release coordinator lease via injected `Lease`, publish `agent.deregistered`.
- `Identity.prune_stopped_agents(retention_seconds)` — hard-delete long-stopped rows (T1.17 tail); rows with active children preserved.
- `Identity.list_agents(active_only, stale_timeout, include_stale)` — T1.17 stale-heartbeat filtering with `include_stale` escape hatch.
- `Identity.get_agent_relations(agent_id, mode)` — lineage or siblings.
- `Identity.find_agent_by_raw_ide_id(raw_ide_id, ide_vendor)` — T3.12 vendor-namespaced lookup so colliding raw ids from different IDEs don't cross-match.
- `Identity._build_context_bundle(agent_id, parent_id)` — private; builds the registration bundle.

## Design Notes
- Two cross-subsystem deps injected directly (Broadcast pattern, commit `fb9e200`): `Locking.release_agent_locks` and `Lease.release_coordinator_lease` on deregister — the lease release prevents orphan leases when leadership-holding subagents are deregistered.
- T1.2 collision guard: registration returns `{"registered": False, "reason": "collision", ...}` if a live agent exists at the same id under a different PID — no event published, no lineage written. T1.20: lineage insert joins the agent-row INSERT in one transaction inside the primitive.
- T7.29: context-bundle reads go through `read_only_connect_fn` so they don't pin a writer-pool slot (pinning stalled concurrent registrations and lock acquires).
- T6.16: worktree fallback uses the storage-level `effective_worktree_root` captured at engine init, not per-call `os.getcwd()`.
- `DEFAULT_PORT` / `HEARTBEAT_INTERVAL` constants live on `CoordinationEngine`, not here; values are passed as ctor args.

## Relationships
Imports: `agent_registry`, `scan`, `context` (build_context_bundle), `lease_subsystem` (Lease), `locking_subsystem` (Locking), `plugins.graph.graphs`
Imported by: `core.py` (constructed by `CoordinationEngine`)
