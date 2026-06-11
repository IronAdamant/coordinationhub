# coordinationhub/locking_subsystem.py

Document locking: acquire (with retry/backoff, force-steal, region locks, scope/ownership/intent checks), release, refresh, status, listing, and admin reaping. Tenth T6.22 extraction from `core_locking.LockingMixin`; exposed as `engine._locking`.

## Key Functions / Classes
- `Locking` — subsystem class; ctor takes `connect_fn`, `publish_event_fn`, a shared `LockCache` instance, and `project_root_getter`. `DEFAULT_LOCK_TTL = 300.0` (legacy `DEFAULT_TTL` alias, renamed in T6.23 to disambiguate from the 10s lease TTL).
- `Locking.acquire_lock(document_path, agent_id, lock_type, ttl, force, region_start, region_end, retry, max_retries, backoff_ms, timeout_ms)` — BEGIN IMMEDIATE acquire; publishes `lock.acquired`; returns `ownership_warning` / `proximity_warning` extras.
- `Locking.release_lock(...)` — whole-file or region release; publishes `lock.released`; T8.1 returns `reason="region_required"` plus the agent's region locks when a whole-file release misses but region locks exist.
- `Locking.refresh_lock(...)` / `Locking.get_lock_status(path)` — TTL refresh; status served from the `LockCache`.
- `Locking.list_locks(agent_id, force_refresh)` — cache-backed listing; T6.33 `force_refresh=True` re-warms the cache from SQLite under BEGIN IMMEDIATE.
- `Locking.admin_locks(action, ...)` — `release_by_agent | reap_expired | reap_stale`; back-compat aliases `release_agent_locks`, `reap_expired_locks`, `reap_stale_agents`.
- `_check_scope_violation` (denies, rolls back), `_check_ownership_boundary` (warning only, logs boundary_crossing), `_check_work_intent_conflict` (proximity warning via `work_intent.check_intent_conflict`).

## Design Notes
- Shared `LockCache` ownership: the instance lives on the engine (created in `__init__`, warmed in `start()`) and is passed in as a shared reference — every DB mutation here must also update the cache.
- T1.1 / T6.31: conflict-log writes use the `lock_ops` primitive directly so they join the outer BEGIN IMMEDIATE transaction; `conflict_log.record_conflict`'s nested `with connect()` would commit mid-flight, opening a duplicate-lock race during force-steals. T6.31 logs every *denied* acquire too, not just steals.
- T1.3: `reap_expired` wraps DELETE + SELECT + `warm()` in one BEGIN IMMEDIATE so a concurrent `acquire_lock`'s `add_lock` cannot be wiped by a stale warm snapshot.
- T3.26: retry backoff gets 0.5x–1.5x random jitter so contending agents don't retry in synchronized waves. T3.23: scope checks compare path components (trailing-slash boundary), not raw `startswith`, so `docs/sec` no longer matches `docs/security`.
- Scope check runs before COMMIT so a violation can ROLLBACK the inserted lock row.

## Relationships
Imports: `conflict_log`, `lock_ops`, `agent_registry`, `work_intent`, `notifications`, `lock_cache` (LockCache type), `paths` (normalize_path)
Imported by: `core.py` (constructed by `CoordinationEngine`), `broadcast_subsystem.py` (injected dep for `get_lock_status`), `identity_subsystem.py` (injected dep for `release_agent_locks`)
