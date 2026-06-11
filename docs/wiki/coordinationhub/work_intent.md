# coordinationhub/work_intent.py

Work-intent board DB primitives: declare/upsert, list, clear, conflict-check, and prune rows in the `work_intent` table. A lightweight cooperative signal — agents declare intent before locking; conflicting peers receive a proximity_warning, not a denial.

## Key Functions / Classes
- `upsert_intent(connect, agent_id, document_path, intent, ttl=60.0)` — INSERT ... ON CONFLICT(agent_id, document_path) DO UPDATE; intent truncated to `MAX_INTENT` (T6.14).
- `get_live_intents(connect, agent_id=None)` — non-expired rows (`declared_at + ttl > now`), optionally per-agent.
- `clear_intent(connect, agent_id, document_path=None)` — delete one intent or all of the agent's intents; returns `rows_cleared`.
- `check_intent_conflict(connect, document_path, exclude_agent_id, requesting_intent)` — live conflicting intents for a path with read/write semantics.
- `prune_expired_intents(connect)` — delete rows where `declared_at + ttl <= now`.
- `_READ_INTENTS = {"read", "review", "watch", "observe"}` / `_is_read_intent(intent)` — read-class intent classification.

## Design Notes
- Zero internal dependencies by design — `connect()` is received from the caller (only type/limits imports).
- T1.16: the primary key is compound `(agent_id, document_path)`, so one agent can hold intents on multiple files; a second declare inserts rather than clobbers.
- Read/write semantics: two readers don't conflict; read vs write and write vs write do. Unknown intent strings are treated as write-class to preserve cooperative pessimism. Legacy callers omitting `requesting_intent` get every live intent back (strictest behaviour).
- No query-time deletion of expired rows — the table grows unbounded unless engine callers run `prune_expired_intents` periodically (at start-up and on a timer).
- `document_path` is expected to be pre-normalized by callers (`work_intent_subsystem` / `locking_subsystem` route through `normalize_path`).

## Relationships
Imports: `db` (ConnectFn type), `limits` (MAX_INTENT, truncate)
Imported by: `work_intent_subsystem.py` (intent CRUD facade), `locking_subsystem.py` (`check_intent_conflict` during lock acquisition)
