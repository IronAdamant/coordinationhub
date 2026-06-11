# coordinationhub/handoffs.py

Handoff recording and acknowledgement primitives: one-to-many handoffs where an agent transfers scope to multiple recipients via `handoff_targets`, and each target must acknowledge.

## Key Functions / Classes
- `_LEGAL_TRANSITIONS` — state machine (T1.15): `pending` → `partially_acknowledged`/`acknowledged`/`cancelled`; `acknowledged` → `completed`/`cancelled`; `completed` and `cancelled` are terminal.
- `record_handoff(connect, from_agent_id, to_agents, document_path=None, handoff_type="scope_transfer")` — insert a handoff with `to_agents` stored as a JSON array; returns `handoff_id`.
- `acknowledge_handoff(connect, handoff_id, agent_id)` — only agents listed in `to_agents` may ack (`not_recipient` otherwise); aggregate status becomes `acknowledged` only when ALL recipients have acked, else `partially_acknowledged` (T1.15).
- `complete_handoff(connect, handoff_id)` / `cancel_handoff(connect, handoff_id)` — verify existence and state-machine legality before transitioning; return `{"completed"/"cancelled": False, "reason": ...}` instead of phantom-success (T1.15 + T1.19).
- `get_handoffs(connect, status=None, from_agent_id=None, limit=50)` — list with filters; `to_agents` JSON decoded defensively (falls back to `[]`).

## Design Notes
- Zero internal dependencies — receives `connect()` from the caller.
- Acks are `INSERT OR IGNORE` into `handoff_acks`; the aggregate is recomputed from `COUNT(DISTINCT agent_id)` after insert, so duplicate acks are idempotent.
- Pre-T1.15 bug: a single ack flipped the aggregate to `acknowledged` even with outstanding recipients.
- Acking a `completed`/`cancelled` handoff returns `illegal_transition_from_<status>`.
- A `NULL` status column is treated as `pending` throughout.

## Relationships
Imports: `coordinationhub.db` (ConnectFn)
Imported by: `handoff_subsystem.py`, `broadcast_subsystem.py`
