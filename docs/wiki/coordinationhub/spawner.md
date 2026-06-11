# coordinationhub/spawner.py

Zero-deps spawner primitives for the HA coordinator sub-agent registry: a parent records its intent to spawn a sub-agent before the external IDE spawns it; when the spawn is reported back, the pending record is marked `registered`. Rows live in the shared `pending_tasks` table.

## Key Functions / Classes
- `PendingSpawn` — NamedTuple describing a pending spawn row (status: pending | registered | expired).
- `stash_pending_spawn(connect, spawn_id=None, parent_agent_id=..., subagent_type=..., description, prompt, source="external")` — preferred entry point; when `spawn_id` is None the id `{parent}.{subagent_type}.{seq}` is generated atomically inside the same `BEGIN IMMEDIATE` as the INSERT (T1.9); expires stale rows and enforces `MAX_PENDING_SPAWNS` in the same transaction.
- `generate_spawn_id(conn, parent_agent_id, subagent_type)` — DEPRECATED (T1.9): not atomic with the INSERT; two concurrent callers can collide. Back-compat only.
- `consume_pending_spawn(connect, parent_agent_id, subagent_type=None)` — mark the oldest pending spawn registered; FIFO by created_at.
- `report_subagent_spawned(connect, parent_agent_id, subagent_type, child_agent_id, source)` — IDE-facing: consume the oldest pending spawn and return its description/prompt linked to the actual child id.
- `get_pending_spawns` / `prune_stale_spawns` / `cancel_spawn` — listing, housekeeping, and cancellation (T2.4: optional `caller_agent_id` must match the spawn's `scope_id`, else `caller_mismatch`).
- `request_deregistration(connect, child_agent_id, requested_by)` / `is_stop_requested(connect, agent_id)` — graceful-stop flag protocol via `agents.stop_requested_at`.

## Design Notes
- `_SPAWN_TTL_SECONDS` (default 600 s) overridable via `COORDINATIONHUB_SPAWN_TTL_SECONDS` (T6.15); `MAX_PENDING_SPAWNS` (default 1000) via `COORDINATIONHUB_MAX_PENDING_SPAWNS` (T6.9, anti-DoS ceiling).
- `subagent_type` must match `^[A-Za-z0-9_-]+$` (T7.3) — a dot would make the derived spawn id ambiguous to consumers that split on dots.
- `source` is validated against the closed vocabulary `{"external", "stdio_adapter", "cc"}` at the primitive boundary (the DB column stays unconstrained by design). Working-tree change: the `kimi_cli`, `kimi`, and `cursor` spawn sources were removed from `_VALID_SPAWN_SOURCES` (matching the deleted `hooks/kimi_cli.py` / `hooks/cursor.py`).
- `description`/`prompt` truncated to `MAX_DESCRIPTION`/`MAX_PROMPT` (T6.14).
- BEGIN IMMEDIATE uses the dual-shape pattern (skip own BEGIN/COMMIT if the connect wrapper already opened a transaction).
- The polling `await_agent_stopped` primitive was deleted (T6.5) in favour of `spawner_subsystem.Spawner.await_subagent_stopped` on the in-memory event bus.

## Relationships
Imports: `coordinationhub.db` (ConnectFn), `coordinationhub.limits` (MAX_DESCRIPTION, MAX_PROMPT, truncate)
Imported by: `spawner_subsystem.py`
