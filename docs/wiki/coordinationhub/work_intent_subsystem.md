# coordinationhub/work_intent_subsystem.py

Cooperative work-intent board subsystem: agents declare intent to work on files before locking; peers get proximity warnings rather than denials. Second T6.22 extraction from `core_work_intent.WorkIntentMixin`; exposed as `engine._work_intent`.

## Key Functions / Classes
- `WorkIntent` — subsystem class; two-dep ctor (`connect_fn`, `project_root_getter`) — no event publishing or waiting at all.
- `WorkIntent.manage_work_intents(action, agent_id, document_path, intent, ttl)` — unified dispatch: `declare | get | clear`.
- `WorkIntent.declare_work_intent(agent_id, document_path, intent, ttl=60.0)` — normalize path and upsert intent.
- `WorkIntent.get_work_intents(agent_id)` — all live intents, optionally per-agent.
- `WorkIntent.clear_work_intent(agent_id, document_path)` — clear one intent (path supplied) or all of the agent's intents (path omitted).
- `WorkIntent.prune_work_intents()` — delete expired rows; engine-level wrapper so operators don't dig into the primitive.
- `WorkIntent._normalize_intent_path(document_path)` — routes paths through `normalize_path` with the engine's project root.

## Design Notes
- Coupling audit found zero cross-mixin calls, zero `_publish_event`, zero `_hybrid_wait` — the leanest subsystem in the series; only DB access and path normalization are injected.
- T1.16: paths are normalized so `./foo.py` and `foo.py` collapse to the same key, and declaring intent on a second file no longer erases the first (compound PK lives in the primitive).
- `project_root_getter` is a callable closure so a `read_only_engine` replica picks up its own storage root without a rebind (pattern established here, reused by Change/Visibility/Locking/Broadcast).
- Intent conflict *checking* is not exposed here — `locking_subsystem.Locking._check_work_intent_conflict` calls the `work_intent` primitive directly during lock acquisition.

## Relationships
Imports: `work_intent` (intent DB primitives), `paths` (normalize_path)
Imported by: `core.py` (constructed by `CoordinationEngine`)
