# coordinationhub/handoff_subsystem.py

Lifecycle side of one-to-many handoffs: acknowledge, complete, cancel, query, and wait-for-completion. Sixth T6.22 extraction from `core_handoffs.HandoffMixin`; exposed as `engine._handoff`. Handoff *creation* lives in `broadcast_subsystem.Broadcast._handoff`.

## Key Functions / Classes
- `Handoff` — subsystem class; three-dep ctor (`connect_fn`, `publish_event_fn`, `hybrid_wait_fn`), same shape as `Spawner` and `Messaging`.
- `Handoff.acknowledge_handoff(handoff_id, agent_id)` — ack; publishes `handoff.ack` only when the primitive reports `acknowledged`.
- `Handoff.complete_handoff(handoff_id)` — publishes `handoff.completed` on success.
- `Handoff.cancel_handoff(handoff_id)` — publishes `handoff.cancelled` on success.
- `Handoff.get_handoffs(status, from_agent_id, limit)` — filtered listing with count.
- `Handoff.wait_for_handoff(handoff_id, timeout_s, agent_id, mode)` — unified dispatch: `status | ack | complete | cancel | completion` (default); `status` reads the row directly and JSON-decodes `to_agents`.

## Design Notes
- T1.15 authz preserved: the `handoffs.acknowledge_handoff` primitive validates the supplied `agent_id` appears in the row's `to_agents` list, rejecting non-recipients with `reason='not_recipient'`.
- T1.19 no-phantom-event guarantee: events fire only when the primitive reports success (`acknowledged` / `completed` / `cancelled`).
- `wait_for_handoff(mode='completion')` has a fast-path DB check for already-completed handoffs before falling into `_hybrid_wait` on `handoff.completed`.
- The `ack`/`complete`/`cancel` branches of `wait_for_handoff` duplicate the dedicated methods' bodies (including event publishes) — keep them in sync when editing.
- Zero cross-mixin calls confirmed by the coupling audit; only the three engine infra callables are injected.

## Relationships
Imports: `handoffs` (handoff DB primitives)
Imported by: `core.py` (constructed by `CoordinationEngine`)
