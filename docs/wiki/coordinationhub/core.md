# coordinationhub/core.py

`CoordinationEngine` — the host class that composes the twelve domain subsystems (Spawner, WorkIntent, Lease, Dependency, Messaging, Handoff, Change, Task, Visibility, Locking, Broadcast, Identity) and exposes one-liner facade methods preserving the pre-decomposition public API used by MCP dispatch, CLI, hooks, housekeeping, and tests.

## Key Functions / Classes
- `CoordinationEngine(storage_dir, project_root, namespace="hub", housekeeping=None)` — builds `CoordinationStorage`, `EventBus`, `LockCache`, and all twelve subsystems with injected infra callables.
- `start()` — starts storage, warms the lock cache from `document_locks`, loads the coordination graph, optionally starts the housekeeping scheduler.
- `close()` — stops housekeeping first (avoids racing a DB close with an in-flight prune), then closes storage / checkpoints WAL.
- `_publish_event(topic, payload)` — durable-journal-first event publish: SQLite `coordination_events` insert commits before the in-memory bus fires (T1.10 ordering fix). Sets `_last_event_journaled`.
- `_hybrid_wait(topics, filter_fn, timeout)` — waits on the in-memory bus (fast path), then polls the SQLite event journal so waits work cross-process (e.g. against `coordinationhub serve`).
- `read_only_engine()` — returns a replica using read-only WAL URI connections; rebinds every subsystem's `_connect` so no call punches through to the writer pool.
- Facade methods — ~70 one-liners (`register_agent`, `acquire_lock`, `create_task`, `broadcast`, `spawn_subagent`, `manage_leases`, `notify_change`, `scan_project`, ...) each delegating to the matching subsystem attribute.
- Class constants: `DEFAULT_PORT = 9877`, `HEARTBEAT_INTERVAL = 30`, `DEFAULT_TTL = 300.0`.

## Design Notes
- T6.22 complete: zero mixins in the MRO; subsystems hang off `self._spawner` ... `self._identity`. Facades must stay so external callers keep calling `engine.method(...)` verbatim.
- Subsystems get only the deps the coupling audit showed they need: `connect_fn`, `publish_event_fn`, `hybrid_wait_fn`, plus `project_root_getter` closures (so replicas pick up their own storage without rebinds). `Broadcast` and `Identity` take the `Locking` instance as an explicit cross-subsystem dep and must be constructed after `self._locking`.
- The `LockCache` stays owned by the engine (Identity's `deregister_agent` reaches locking via the facade; `start()` warms the cache directly).
- The loaded coordination graph lives in `plugins/graph/graphs.py` module-level state, not on the engine.
- Housekeeping is opt-in: `housekeeping=None` defers to the `COORDINATIONHUB_HOUSEKEEPING` env var so short-lived CLI runs stay thread-free.
- On journal-write failure `_publish_event` still fires the in-memory bus so same-process waiters don't hang; cross-process waiters will miss that event.
- Mutations through a `read_only_engine()` replica are not a supported flow — only `_connect` is rebound, not `_publish_event` / `_hybrid_wait`.

## Relationships
Imports: `_storage` (CoordinationStorage), `event_bus` (EventBus), `housekeeping` (HousekeepingScheduler, build_default_scheduler, is_enabled_by_env), `lock_cache` (LockCache), `paths` (detect_project_root), `plugins.graph.graphs`, and the twelve `*_subsystem` modules.
Imported by: `__init__.py`, `mcp_server.py`, `mcp_stdio.py`, `cli_utils.py`, `cli_setup.py` (lazy), `hooks/base.py` (lazy absolute import).
