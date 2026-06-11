# coordinationhub/event_bus.py

Lightweight thread-safe in-memory pub-sub bus for low-latency notification between coordination primitives. Events are ephemeral — persistence lives in the SQLite `coordination_events` journal, not here.

## Key Functions / Classes
- `EventBus` — `subscribe(topics, filter_fn)`, `subscribe_all(filter_fn)`, `unsubscribe(sub_id)`, `publish(topic, payload)`, `wait_for_event(topics, filter_fn, timeout)` (returns the event dict or None on timeout).
- `_Sub` — per-subscriber bounded queue with `put` (drop-oldest on overflow) and `get`; tracks a `dropped` counter and an `all_topics` flag.
- `_DEFAULT_QUEUE_MAX = 10_000` — per-subscriber queue cap.

## Design Notes
- T3.25: per-subscriber queues are bounded so a slow subscriber can't drive the publisher into OOM. On a full queue the oldest event is discarded and `dropped` incremented so consumers can detect loss; a rare double-full race drops the new event with a WARNING rather than blocking.
- T3.8: `subscribe_all` exists for SSE streams — rather than enumerating 24+ topic strings, an all-topics subscriber receives `{"topic": <name>, **payload}` so one receiver can route by event type. Topic-scoped subscribers receive the bare payload.
- `publish` snapshots the subscriber list under the lock, then delivers outside it; filter functions run against the delivered shape.
- `wait_for_event` is subscribe → blocking get → unsubscribe in a finally block; it's the fast path of the engine's `_hybrid_wait` (the SQLite journal poll is the cross-process fallback).
- Zero external dependencies; everything is stdlib `queue`/`threading`.

## Relationships
Imports: none from the package.
Imported by: `core.py` (constructs the engine's `_event_bus`; `mcp_server.py` reaches the instance via `engine._event_bus` for SSE without importing this module).
