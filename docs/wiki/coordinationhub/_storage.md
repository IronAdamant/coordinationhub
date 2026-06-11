# coordinationhub/_storage.py

Storage backend — `CoordinationStorage` owns the SQLite connection pool, storage-directory resolution, schema initialisation, read-only replica connections, and agent-ID generation.

## Key Functions / Classes
- `CoordinationStorage(storage_dir, project_root, namespace="hub")` — resolves the storage dir (explicit > `project_root/.coordinationhub` > `~/.coordinationhub`) and freezes `effective_worktree_root` at init.
- `start()` — mkdir the storage dir, open the `ConnectionPool` on `coordination.db`, call `db.set_pool`, run `init_schema`.
- `close()` — best-effort `PRAGMA wal_checkpoint(TRUNCATE)` then `db.clear_pool()`.
- `_connect()` — pool connection; raises `RuntimeError` if not started.
- `read_only_connection()` — direct `file:...?mode=ro` URI connection bypassing the writer pool; safe for concurrent WAL reads.
- `generate_agent_id(parent_id=None)` — `{namespace}.{PID}.{seq}` for roots, `{parent_id}.{seq}` for children; raises `ValueError` for an unknown parent.
- `effective_worktree_root` / `project_root` properties.

## Design Notes
- T6.16: cwd is resolved once at init; agents read `effective_worktree_root` instead of calling `os.getcwd()`, so a hub that chdirs mid-run hands out consistent roots.
- T7.25: `__init__` only resolves paths — `start()` owns the mkdir, so a never-started engine leaves no `.coordinationhub` breadcrumb on disk.
- T7.27: the WAL checkpoint must run outside a transaction and outside the `with pool.connect()` contextmanager (which could commit an implicit tx; the pragma only drains the WAL in auto-commit mode).
- T7.28: the read-only URI path is URL-encoded so directories containing `?`, `#`, or spaces don't confuse the URI parser.
- Sequence generation is numeric (`CAST(substr(...))`, GLOB digit guard) — a lexicographic MAX would order `...9 > ...10` and collide on the 11th registration. `_next_seq_atomic` keeps in-memory counters under `_seq_lock` so IDs are unique even before the agent row exists.
- No internal dependencies on any other coordinationhub sub-module except `db`.

## Relationships
Imports: `db` (as `_db`: `ConnectionPool`, `set_pool`, `clear_pool`, `init_schema`).
Imported by: `core.py` (the only in-package consumer; CLI entry points reach it through the engine).
