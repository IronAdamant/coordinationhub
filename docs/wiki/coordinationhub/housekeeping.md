# coordinationhub/housekeeping.py

`HousekeepingScheduler` — background periodic pruners for long-running hubs: named zero-arg tasks on independent intervals inside a single daemon thread, with cooperative shutdown via a `threading.Event`.

## Key Functions / Classes
- `HousekeepingScheduler(name="coordhub-housekeeping")` — `add_task(name, interval_s, fn)`, `start()` (idempotent), `stop(timeout=5.0)` (idempotent), `run_once()` (fires every task synchronously; used by tests and on-demand operators).
- `build_default_scheduler(engine)` — wires the four standard prunes: `coordination_events` (via `engine.prune_notifications`, 600 s), `stale_agents` (reap + `prune_stopped_agents`, 3600 s), `assessment_results` (3600 s), `work_intents` (300 s). Returned stopped; caller starts it.
- `is_enabled_by_env()` — true when `COORDINATIONHUB_HOUSEKEEPING` is `1/true/yes/on`.
- `_Task` — per-task state (`next_run_at`, `last_result`, `last_error`); fires immediately on first tick to catch up on backlog.
- `_TICK_FLOOR_S = 1.0` — minimum scheduler wake interval; task intervals are clamped to it.

## Design Notes
- Resolves audit items T4.7 (unbounded `coordination_events` journal), T7.32 (`assessment_results.details_json` retention), and the T1.17 tail (stale-agent tombstone rows never deleted).
- Disabled by default so single-shot CLI invocations don't spin up an orphan thread; opt in via `CoordinationEngine(housekeeping=True)` or the env var.
- Task failures are logged and swallowed — one bad pruner cannot kill the worker thread.
- `add_task` after `start()` raises `RuntimeError` (keeps the worker loop simple).
- The next run is scheduled relative to *completion* time so a slow run doesn't pile up back-to-back retries; `stop()` cuts the inter-tick sleep short via `Event.wait`.
- All cadences/retentions are env-tunable (`COORDINATIONHUB_HOUSEKEEPING_*_INTERVAL_S`, `COORDINATIONHUB_*_MAX_AGE_S`, etc.) — read at import time.

## Relationships
Imports: none from the package (the engine is passed in as a duck-typed argument).
Imported by: `core.py` (`HousekeepingScheduler`, `build_default_scheduler`, `is_enabled_by_env`).
