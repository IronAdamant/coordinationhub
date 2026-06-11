# coordinationhub/messages.py

Inter-agent direct messaging primitives: send, fetch, mark-read, and count unread messages with typed JSON payloads.

## Key Functions / Classes
- `send_message(connect, from_agent_id, to_agent_id, message_type, payload=None)` — insert a message; the JSON-serialised payload is truncated to `MAX_MESSAGE` (clipped + annotated, not rejected — recipient still sees type and sender, T6.14).
- `get_messages(connect, agent_id, unread_only=False, limit=50, since_id=None)` — fetch messages for an agent, newest first; `payload_json` is decoded into `payload` and the raw column dropped.
- `mark_messages_read(connect, agent_id, message_ids=None)` — set `read_at`; `None` marks all unread for the agent.
- `count_unread(connect, agent_id)` — count of unread messages.

## Design Notes
- Zero internal dependencies — receives `connect()` from the caller.
- `since_id` is a monotonic cursor (T6.25): pass the highest `id` from the previous batch; the filter is strictly-greater-than. Display ordering stays `created_at DESC, id DESC`, but pollers should rely on the id cursor, not timestamp tie-breaks.
- Truncation happens at the write boundary, before the row hits the DB.
- `mark_messages_read` with explicit ids builds an `IN (...)` placeholder list; ids are always scoped to `to_agent_id` so an agent can't mark another agent's messages read.

## Relationships
Imports: `coordinationhub.db` (ConnectFn), `coordinationhub.limits` (MAX_MESSAGE, truncate)
Imported by: `broadcast_subsystem.py`, `messaging_subsystem.py`
