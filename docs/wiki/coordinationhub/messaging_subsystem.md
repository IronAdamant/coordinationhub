# coordinationhub/messaging_subsystem.py

Inter-agent message passing (send / get / mark_read) plus `await_agent` for waiting on an agent's deregistration. Fifth T6.22 extraction from `core_messaging.MessagingMixin`; exposed as `engine._messaging`.

## Key Functions / Classes
- `Messaging` — subsystem class; three-dep ctor (`connect_fn`, `publish_event_fn`, `hybrid_wait_fn`), same shape as `Spawner`.
- `Messaging.manage_messages(action, agent_id, ...)` — unified dispatch: `send | get | mark_read`, with optional `caller_agent_id` authz and `since_id` cursor.
- `Messaging.send_message(from_agent_id, to_agent_id, message_type, payload, caller_agent_id)` — send; publishes `message.received`.
- `Messaging.get_messages(agent_id, unread_only, limit, since_id)` — read-only fetch (no auto-ack).
- `Messaging.mark_messages_read(agent_id, message_ids)` — explicit read marking.
- `Messaging.await_agent(agent_id, timeout_s)` — fast-path DB check then `_hybrid_wait` on `agent.deregistered`.

## Design Notes
- T2.4 opt-in authz on both `send_message` and `manage_messages`: when `caller_agent_id` is supplied it must equal `from_agent_id` (send) or `agent_id` (get/mark_read), blocking forged senders and cross-agent inbox reads with `reason="caller_mismatch"`. Omitting it preserves the pre-T2.4 permissive trust model for internal callers.
- T7.23: `send_message` and `manage_messages(action='send')` are intentionally functionally equivalent dual paths — keep them in sync when editing.
- T6.24: reading messages no longer auto-acks broadcasts; acknowledgment must be an explicit `acknowledge_broadcast` call, so a crash between fetch and action can't ghost-ack.
- T6.25: `since_id` enables cursor-based incremental polling — pass the previous batch's highest id to fetch only newer messages.
- `await_agent` treats a missing agent row as already-complete (`status: "not_found"`, `awaited: True`).

## Relationships
Imports: `messages` (message DB primitives)
Imported by: `core.py` (constructed by `CoordinationEngine`)
