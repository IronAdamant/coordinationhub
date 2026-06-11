# coordinationhub/db.py

SQLite connection pooling and the package's public DB re-export surface. Schema DDL lives in `db_schemas` (pure data) and migrations + `init_schema` in `db_migrations`; this module owns the thread-local pool and re-exports what the rest of the package and tests depend on.

## Key Functions / Classes
- `ConnectionPool(db_path)` — thread-local pool: exactly one reused connection per thread (`connect()`, `close_all()`); eliminates ~70 opens per typical workload.
- `_create_connection(db_path)` — WAL-mode connection with pragmas: `synchronous=NORMAL`, `busy_timeout=30000`, `foreign_keys=ON`, 8 MB cache, 64 MB mmap, `sqlite3.Row` factory.
- `set_pool(pool)` / `clear_pool()` / `connect()` — module-level active-pool helpers; `connect()` raises `RuntimeError` if no pool is active.
- `ConnectFn` — type alias `Callable[[], sqlite3.Connection]` used across the primitive modules.
- `_db_path(storage_dir)` — returns `storage_dir / "coordination.db"`.
- Re-exports: `init_schema`, `_SCHEMAS`, `_INDEXES`, `_CURRENT_SCHEMA_VERSION`, `_MIGRATIONS`, `_get_schema_version`.

## Design Notes
- T7.24: the cached connection's liveness probe (`SELECT 1`) catches the full `sqlite3.DatabaseError` hierarchy so corruption indicators drop the cached connection instead of propagating; the narrower ProgrammingError/OperationalError pair missed corruption shapes.
- The module-level `_pool` is set by the storage backend's init and cleared on close; primitive sub-modules receive a `connect()` callable from their caller rather than importing this module's global directly.
- `connect()` must only be called within an active storage-backend lifecycle.
- Zero internal dependencies beyond its two sibling modules.

## Relationships
Imports: `db_schemas` (`_SCHEMAS`, `_INDEXES`), `db_migrations` (`_CURRENT_SCHEMA_VERSION`, `_MIGRATIONS`, `_get_schema_version`, `init_schema`).
Imported by: `_storage.py`, and the primitive modules `agent_registry.py`, `broadcasts.py`, `conflict_log.py`, `dependencies.py`, `handoffs.py`, `lock_ops.py`, `messages.py`, `notifications.py`, `pending_tasks.py`, `spawner.py`, `task_failures.py`, `tasks.py`, `work_intent.py`, plus `cli_setup_doctor.py` and `plugins/assessment/assessment.py`.
