# coordinationhub/lock_ops.py

Shared lock primitives used by both local locks and coordination locks. Supports file-level and region-level locking with shared/exclusive semantics; the lock table name is parameterised on every call.

## Key Functions / Classes
- `_regions_overlap(a_start, a_end, b_start, b_end)` — region overlap check; `None` means whole-file (overlaps everything).
- `find_conflicting_locks(conn, table, path, agent_id, lock_type, region..., worktree_root)` — unexpired locks by *other* agents that overlap; two shared locks never conflict; optional worktree scoping (`worktree_root IS NULL OR = ?`).
- `find_own_lock(conn, table, path, agent_id, region..., worktree_root)` — the caller's own lock on the same region, *including expired-but-unreaped rows* so acquire UPDATEs instead of inserting duplicates (T1.4).
- `refresh_lock(conn, table, path, agent_id, ttl, ...)` — extend TTL; rejects non-owner (`not_owner`) and expired locks (`expired`, T1.4 — no silent resurrection past reap).
- `reap_expired_locks(conn, table, agent_grace_seconds=0)` — delete expired locks; with grace > 0, locks held by recently-heartbeating active agents are implicitly refreshed instead, and `lock_ttl` is raised to at least the grace so tiny-TTL locks aren't grace-refreshed every tick (immortal-lock fix, T3.27).
- `record_conflict(conn, table, ...)` / `query_conflicts(conn, table, ...)` — conflict-log insert/query primitives (wrapped by `conflict_log.py`).
- `release_agent_locks(conn, table, agent_id)` — DELETE all locks held by an agent; the old `delete=False` SET-NULL mode was removed (T6.34).

## Design Notes
- Zero internal dependencies — takes a raw `sqlite3.Connection`; callers own connection/transaction lifecycle.
- `region_end` is **exclusive**: `[1, 50]` and `[50, 100]` do NOT overlap (strict `<`). Callers with inclusive line numbers must pass `region_end` as the line *after* the last owned line (T7.17, locked in by `tests/test_locking.py::test_region_overlap`).
- Validity predicate everywhere is `locked_at + lock_ttl > now` — consistent with `lock_cache` and the locking subsystem.
- Grace-based reaping exists so the TTL is a fallback for crashed agents, not a hard deadline for live ones (model output between PreToolUse and PostToolUse can exceed the TTL).

## Relationships
Imports: `coordinationhub.db` (ConnectFn, type-only)
Imported by: `conflict_log.py`, `locking_subsystem.py`
