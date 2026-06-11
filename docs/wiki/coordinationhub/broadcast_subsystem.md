# coordinationhub/broadcast_subsystem.py

Sibling broadcast announcements with optional acknowledgment tracking, the creation side of multi-recipient handoffs, and multi-lock waiting. Eleventh T6.22 extraction from `core_broadcasts.BroadcastMixin`; exposed as `engine._broadcast`.

## Key Functions / Classes
- `Broadcast` — subsystem class; ctor takes `connect_fn`, `publish_event_fn`, `hybrid_wait_fn`, a `Locking` instance, and `project_root_getter`.
- `Broadcast.broadcast(agent_id, document_path, ttl, handoff_targets, require_ack, message)` — announce to live siblings; with `require_ack` records a trackable broadcast (T1.11 target snapshot) and sends `broadcast_ack_request` messages; with `handoff_targets` delegates to `_handoff`.
- `Broadcast.acknowledge_broadcast(broadcast_id, agent_id)` — ack receipt; publishes `broadcast.ack` only on real ack.
- `Broadcast.get_broadcast_status(broadcast_id)` — current ack status.
- `Broadcast.wait_for_broadcast_acks(broadcast_id, timeout_s)` — hybrid event/DB wait until all expected acks arrive.
- `Broadcast._handoff(agent_id, to_agents, document_path, handoff_type)` — writes the handoff row, messages each target, publishes `handoff.created`.
- `Broadcast.wait_for_locks(document_paths, agent_id, timeout_s)` — wait for `lock.released` events on a set of paths.

## Design Notes
- First extraction in the T6.22 series with a cross-subsystem dependency: `Locking` is injected and `wait_for_locks` calls `self._locking.get_lock_status(...)` directly, bypassing the engine MRO/facade.
- Owns only the *creation* side of handoffs; lifecycle (ack/complete/cancel/query/wait) lives in `handoff_subsystem.Handoff`. Pre-extraction the engine's `_handoff` attribute shadowed the mixin method — the extraction fixed this latent bug silently.
- `wait_for_broadcast_acks` always trusts the final DB status for counts; events may be missed under storm, the DB is authoritative (prevents under-counted acks and ghost reports).
- `wait_for_locks` re-checks remaining paths via direct lock status after the event loop, catching releases whose events were missed.
- T1.11: ack targets are snapshotted at broadcast time so `pending_acks` is computable and late-joiners are excluded.
- `project_root_getter` is a closure so a `read_only_engine` replica picks up its own storage root without a rebind.

## Relationships
Imports: `agent_registry`, `broadcasts`, `handoffs`, `messages`, `locking_subsystem` (Locking), `paths` (normalize_path)
Imported by: `core.py` (constructed by `CoordinationEngine`)
