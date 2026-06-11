# coordinationhub/db_schemas.py

Canonical SQLite schema definitions — pure data: the `CREATE TABLE IF NOT EXISTS` statements for all 22 tables and the matching `CREATE INDEX IF NOT EXISTS` list.

## Key Functions / Classes
- `_SCHEMAS` — dict `{table_name: DDL}` covering `agents`, `lineage`, `document_locks`, `lock_conflicts`, `change_notifications`, `agent_responsibilities`, `pending_tasks`, `file_ownership`, `assessment_results`, `descendant_registry`, `messages`, `tasks`, `work_intent`, `handoffs`, `handoff_acks`, `task_failures`, `coordinator_leases`, `agent_dependencies`, `pending_spawner_tasks`, `broadcasts`, `broadcast_acks`, `broadcast_targets`, `coordination_events`.
- `_INDEXES` — list of ~40 index DDL strings, including the v27 defence-in-depth UNIQUE indexes.

## Design Notes
- Pure data module — no functions, no logic. Fresh installs get the latest table shape directly from `_SCHEMAS`; legacy DBs are reshaped by `db_migrations`.
- `idx_document_locks_unique` is a partial-expression UNIQUE on `(document_path, locked_by, COALESCE(region_start,-1), COALESCE(region_end,-1))` — the COALESCE collapses file-level (region-NULL) duplicates that SQLite's NULL-distinct UNIQUE semantics would otherwise allow (T1.4).
- `idx_task_failures_unique` on `(task_id, attempt)` backs up the `BEGIN IMMEDIATE` serialization in `record_task_failure` (T1.7).
- `idx_locks_path` was dropped at v27 (T4.6) — redundant with `idx_locks_locked_by` plus the partial UNIQUE index; don't re-add it here.
- `pending_spawner_tasks` DDL is retained even though v20 merged its data into `pending_tasks` — migration code references the schema during rebuilds.
- `work_intent` PK is compound `(agent_id, document_path)` (v23): an agent may hold multiple live intents.

## Relationships
Imports: none (stdlib-free pure data).
Imported by: `db.py` (fresh-install path in `init_schema`) and `db_migrations.py` (schema re-use inside migration steps).
