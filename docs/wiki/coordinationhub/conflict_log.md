# coordinationhub/conflict_log.py

Thin connection-managing wrapper around the shared conflict primitives in `lock_ops`, hard-wired to the `lock_conflicts` table.

## Key Functions / Classes
- `record_conflict(connect, document_path, agent_a, agent_b, conflict_type, resolution="rejected", details=None)` — opens a connection and delegates to `lock_ops.record_conflict` against `lock_conflicts`; returns the inserted row id.
- `query_conflicts(connect, document_path=None, agent_id=None, limit=20)` — opens a connection and delegates to `lock_ops.query_conflicts` against `lock_conflicts`.

## Design Notes
- Zero internal dependencies beyond the `lock_ops` primitives — receives `connect()` from the caller.
- The table name (`lock_conflicts`) is fixed here; `lock_ops` keeps the table parameterised so local-lock and coordination-lock tables can share the same primitives.
- This module owns connection lifecycle (`with connect()`); the underlying `lock_ops` functions take a raw `sqlite3.Connection`.
- `details` dicts are JSON-serialised by the underlying primitive.

## Relationships
Imports: `coordinationhub.db` (ConnectFn), `coordinationhub.lock_ops` (record_conflict, query_conflicts)
Imported by: `change_subsystem.py`, `locking_subsystem.py`
