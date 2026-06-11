# coordinationhub/change_subsystem.py

Change-awareness subsystem: change notifications (with long-poll), first-write file ownership claims, conflict-log queries, contention hotspots, and the overall `status()` snapshot. Seventh T6.22 extraction from `core_change.ChangeMixin`; exposed as `engine._change`.

## Key Functions / Classes
- `Change` — subsystem class; ctor takes `connect_fn`, `publish_event_fn`, `hybrid_wait_fn`, `project_root_getter`. `DEFAULT_TTL = 300.0`.
- `Change.notify_change(document_path, change_type, agent_id)` — record a change event; publishes `notification.created` carrying `notification_id` (T6.6 monotonic cursor).
- `Change.claim_file_ownership(document_path, agent_id)` — INSERT OR IGNORE into `file_ownership`; first writer wins, later writers don't overwrite.
- `Change.get_notifications(...)` — fetch notifications; `timeout_s > 0` long-polls via `_hybrid_wait`; optional inline pruning replaces the old prune tool.
- `Change.prune_notifications(max_age_seconds, max_entries)` — cleanup.
- `Change.wait_for_notifications(agent_id, timeout_s, ...)` — legacy long-poll wrapper.
- `Change.get_conflicts(document_path, agent_id, limit)` — query the conflict log.
- `Change.get_contention_hotspots(limit)` — rank files by `lock_conflicts` frequency (direct SQL).
- `Change.status()` — counts of agents/locks/notifications/conflicts/owned files, `graph_loaded`, and tool count from `TOOL_DISPATCH`.

## Design Notes
- T6.6: long-poll resume uses the monotonic `notification_id` cursor (`since_id = id - 1` so the triggering row is included) instead of a drift-prone 1-second-back timestamp; the timestamp path remains as a legacy fallback for pre-T6.6 events.
- T7.20: `created_at` is populated by the primitive, so the `or time.time()` fallback is unreachable belt-and-braces.
- `scan_project` may later reassign ownership claimed via `claim_file_ownership` based on graph roles.
- `project_root_getter` is a closure so a `read_only_engine` replica picks up its own storage root without rebind; paths are normalized before DB writes/queries.

## Relationships
Imports: `notifications`, `conflict_log`, `dispatch` (TOOL_DISPATCH), `paths` (normalize_path), `plugins.graph.graphs` (lazy, inside `status()`)
Imported by: `core.py` (constructed by `CoordinationEngine`)
