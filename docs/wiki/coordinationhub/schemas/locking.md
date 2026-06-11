# coordinationhub/schemas/locking.py

Document Locking MCP tool schemas for CoordinationHub. Pure data declarations (no logic) for exclusive/shared, whole-file and region-level lock operations.

## Key Functions / Classes
- `_REGION_PROPS` — shared property fragment (`region_start`, `region_end` line numbers; omit both for whole-file locks) spread into acquire/release/refresh schemas.
- `TOOL_SCHEMAS_LOCKING: dict[str, dict]` — declares six tools:
  - `acquire_lock` — exclusive (default) or shared lock on a path or region; `ttl` default 300 s; `force=True` steals a held lock and records a conflict; `retry=True` enables exponential backoff (`max_retries` 5, `backoff_ms` 100, `timeout_ms` 5000)
  - `release_lock` — owner-only release; region params select a region lock
  - `refresh_lock` — owner-only TTL extension without release/re-acquire (`ttl` default 300)
  - `get_lock_status` — all active locks on a path (including region locks); also cleans up expired locks on read
  - `list_locks` — all active locks, optional `agent_id` filter; `force_refresh=True` re-warms the in-memory cache from SQLite (recovery from suspected cache desync)
  - `admin_locks` — `action='release_by_agent'` (needs `agent_id`) | `'reap_expired'` (`grace_seconds`) | `'reap_stale'` (`timeout` default 600 s; marks stale agents stopped and cleans their locks)

## Design Notes
- Pure data declarations — no logic; re-exported by `coordinationhub.schemas` (module docstring).
- Lock semantics stated in descriptions: shared locks allow concurrent access; exclusive locks block all other locks on overlapping regions; non-forced acquire on a live foreign lock returns conflict info instead of blocking.
- Only the lock owner may release or refresh; description instructs acquiring before writing any shared file.
- The `force_refresh` / cache-desync wording reflects the dual-store design (in-memory cache backed by SQLite).

## Relationships
Imports: none within the package (only `from __future__ import annotations`).
Imported by: `coordinationhub/schemas/__init__.py` (merged into the aggregated `TOOL_SCHEMAS` and re-exported in `__all__`).
