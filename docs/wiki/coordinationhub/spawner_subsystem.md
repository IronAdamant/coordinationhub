# coordinationhub/spawner_subsystem.py

Sub-agent spawn lifecycle for the HA coordinator: stash pending spawns, report/correlate actual spawns from external IDEs/CLIs, await registration, and request/await graceful child deregistration. First T6.22 extraction (from `core_spawner.SpawnerMixin`); exposed as `engine._spawner`.

## Key Functions / Classes
- `Spawner` — subsystem class; three-dep ctor (`connect_fn`, `publish_event_fn`, `hybrid_wait_fn`). `DEFAULT_SPAWN_TIMEOUT = 300.0`.
- `Spawner.spawn_subagent(parent_agent_id, subagent_type, description, prompt, source)` — record spawn intent in the pending_tasks queue before the external system spawns; returns the spawn ID.
- `Spawner.get_pending_spawns(parent_agent_id, include_consumed)` — pending (or all) spawn records.
- `Spawner.report_subagent_spawned(parent_agent_id, subagent_type, child_agent_id, source, caller_agent_id)` — consume the pending record, link the real child id, publish `spawner.registered`.
- `Spawner.await_subagent_registration(parent_agent_id, subagent_type, timeout)` — fast-path check, then `_hybrid_wait` on `spawner.registered`, then re-query for the full record.
- `Spawner.cancel_spawn(spawn_id, caller_agent_id)` — cancel a pending spawn (T3.19: routed through the engine, not direct CLI-to-primitive).
- `Spawner.request_subagent_deregistration(parent_agent_id, child_agent_id)` — set `stop_requested_at` on the child.
- `Spawner.is_subagent_stop_requested(agent_id)` — child-side poll for the stop flag.
- `Spawner.await_subagent_stopped(child_agent_id, timeout)` — wait on `agent.deregistered`; on timeout returns `escalate: True` telling the caller to force `deregister_agent`.

## Design Notes
- T1.9: spawn-id generation and stash happen in one BEGIN IMMEDIATE inside the primitive so concurrent spawns from the same (parent, subagent_type) cannot collide on seq.
- T2.4 opt-in authz: `caller_agent_id`, when supplied, must equal `parent_agent_id` on report (prevents a sibling claiming another parent's child and luring `await_subagent_registration`), and must match the pending spawn's parent `scope_id` on cancel.
- Graceful-stop protocol: parent requests stop, child polls `is_subagent_stop_requested` and deregisters itself; after timeout the parent escalates to direct `deregister_agent`.
- Correlation with the external spawner is via `parent_agent_id` (+ optional `subagent_type`), not the child id, since the child id is unknown until spawn.

## Relationships
Imports: `spawner` (spawn DB primitives)
Imported by: `core.py` (constructed by `CoordinationEngine`)
