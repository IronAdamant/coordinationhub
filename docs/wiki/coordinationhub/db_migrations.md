# coordinationhub/db_migrations.py

Schema-version tracking, the numbered migration functions (v2 through v27), and the `init_schema` driver that brings any DB — fresh or legacy — to the current shape.

## Key Functions / Classes
- `_CURRENT_SCHEMA_VERSION = 27` — bump when adding a migration.
- `init_schema(conn)` — ensures `schema_version` exists, runs `CREATE TABLE IF NOT EXISTS` for every `_SCHEMAS` entry, unconditionally runs every migration in version order, creates all `_INDEXES`, records the current version.
- `_get_schema_version(conn)` — current recorded version, 0 if untracked.
- `_MIGRATIONS` — dict `{version: migration_fn}`; several entries are a shared `_noop_migration` (T7.30) for tables that were added via `CREATE TABLE IF NOT EXISTS`.
- Notable migrations: v2 multi-lock/region rebuild of `document_locks`; v20 merge into `pending_tasks`; v21 `claude_agent_id` → `raw_ide_id` rename; v23 compound work_intent PK `(agent_id, document_path)`; v24 `ide_vendor` + partial UNIQUE `(raw_ide_id, ide_vendor)`; v25 `tasks.error`; v26 status-enum CHECK triggers on `tasks`/`pending_tasks`; v27 cleanup bundle (drop vestigial column, defence-in-depth UNIQUE indexes, drop redundant `idx_locks_path`).

## Design Notes
- The recorded `schema_version` is advisory only: each migration re-checks actual table shape via `PRAGMA table_info` / `sqlite_master` and no-ops if already applied. This tolerates DBs stamped by buggy older `init_schema` implementations.
- Every migration must therefore be idempotent — `init_schema` reruns the whole list on every process start.
- T4.9: each migration runs inside an explicit `BEGIN`/`COMMIT` with rollback-on-exception so a partial rebuild (e.g. a stranded `_document_locks_v1`) can't be left behind; failures are logged and re-raised.
- v26 uses separate `execute` calls (not `executescript`, which implicitly commits and would break the driver's transaction boundary).
- v27's UNIQUE-index steps are guarded on index existence to avoid a redundant DELETE under `BEGIN IMMEDIATE` on every startup ("database is locked" under contention); the `COALESCE(region_*, -1)` is needed because SQLite treats NULL as distinct in UNIQUE constraints.
- v3 must skip when `raw_ide_id` already exists, or it would re-introduce the vestigial `claude_agent_id` column that v21 leaves orphaned.

## Relationships
Imports: `db_schemas` (`_SCHEMAS`, `_INDEXES`).
Imported by: `db.py` (which re-exports `init_schema` and the private symbols for tests).
