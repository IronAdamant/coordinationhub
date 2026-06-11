# coordinationhub/lock_cache.py

Thread-safe in-memory mirror of the `document_locks` table that eliminates SQLite reads for `get_lock_status` / `list_locks`. Writes still go to SQLite for durability; the cache is updated after every successful mutation.

## Key Functions / Classes
- `LockCache` — the only public symbol; an RLock-guarded `dict[path, list[lock-entry]]`.
- `warm(rows)` — clear and repopulate the cache from a query result.
- `add_lock(entry)` / `remove_lock(path, agent_id, region_start, region_end)` / `remove_by_agent(agent_id)` — mutations mirroring SQLite writes.
- `refresh_lock(path, agent_id, locked_at, lock_ttl, lock_type, region...)` — update TTL/locked_at on an existing entry.
- `get_status(path, now)` — lock status in the same format as the SQLite query; single holder is flattened, multiple holders returned under `holders`.
- `list_active(now, agent_id=None)` — all active locks, optionally agent-filtered.
- `list_conflicting_locks(path, agent_id, lock_type, region...)` — conflict check for an acquire attempt, mirroring `lock_ops.find_conflicting_locks` semantics.

## Design Notes
- Zero external dependencies (stdlib `threading` only) — the only file in this set with no package imports at all.
- Entry identity key is the tuple `(locked_by, region_start, region_end)` — tuples, not a `:`-joined string, so a colon in an agent id can't collide with shifted region bounds (T7.16).
- Canonical validity predicate is `now < locked_at + lock_ttl` (strict); a lock expiring exactly at `now` is treated as gone — same convention as `lock_ops`, `locking_subsystem`, `agent_status`, and `context.get_context_bundle` (T1.4).
- Readers lazily evict expired entries (and empty path buckets) as they scan.
- Conflict semantics: exclusive conflicts with any overlapping lock; shared conflicts only with overlapping exclusive locks.

## Relationships
Imports: (none within the package)
Imported by: `locking_subsystem.py`, `core.py`
