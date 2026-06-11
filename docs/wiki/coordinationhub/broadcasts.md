# coordinationhub/broadcasts.py

Broadcast acknowledgment primitives: delivery confirmation for broadcasts without formal handoffs. Any sibling can ack a broadcast; the sender polls acknowledgment status.

## Key Functions / Classes
- `record_broadcast(connect, from_agent_id, document_path, message, ttl, expected_count, targets)` — insert a broadcast row with `expires_at = now + ttl`; when `targets` is given the exact recipient set is snapshotted into `broadcast_targets` and `expected_count` is derived from `len(targets)` (T1.11).
- `acknowledge_broadcast(connect, broadcast_id, agent_id)` — `INSERT OR IGNORE` an ack; rejects expired/unknown broadcasts; returns `already_acked: bool` so idempotent retries are observable (T6.26).
- `get_broadcast_status(connect, broadcast_id)` — status bundle with `acknowledged_by` and `pending_acks` computed as `targets - acks` (T1.11).
- `get_broadcasts(connect, from_agent_id=None, limit=50)` — list broadcasts, newest first, optionally filtered by sender.

## Design Notes
- Zero internal dependencies — receives `connect()` from the caller.
- Late-joining siblings (registered after the broadcast) are deliberately NOT added to `pending_acks`: they never received the ack_request message, so excluding them is correct (T1.11).
- For legacy broadcasts recorded before `broadcast_targets` existed, `pending_acks` is empty and callers fall back to the scalar `expected_count`.
- Acks on expired or missing broadcasts return `{"acknowledged": False, "reason": "expired_or_not_found"}` rather than raising.
- `get_broadcasts` builds its WHERE clause lazily instead of a vacuous `1=1` (T7.21).

## Relationships
Imports: `coordinationhub.db` (ConnectFn)
Imported by: `broadcast_subsystem.py`
