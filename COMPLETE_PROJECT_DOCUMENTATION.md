# CoordinationHub — Complete Project Documentation

**Version:** 0.2.0
**Last updated:** 2026-04-06

## File Inventory

| Path | Purpose | Dependencies |
|------|---------|--------------|
| `coordinationhub/__init__.py` | Package init, exports `CoordinationEngine`, `CoordinationHubMCPServer` | core, mcp_server |
| `coordinationhub/core.py` | `CoordinationEngine` class — all 20 MCP tool methods + internal helpers | db, agent_registry, lock_ops, conflict_log, notifications |
| `coordinationhub/schemas.py` | JSON Schema for all 20 tools + dispatch table | (no internal deps) |
| `coordinationhub/mcp_server.py` | HTTP MCP server (`ThreadedHTTPServer`, stdlib only) | core, schemas |
| `coordinationhub/mcp_stdio.py` | Stdio MCP server (requires optional `mcp` package) | core, mcp_server, schemas |
| `coordinationhub/cli.py` | argparse CLI (22 subcommands) | core |
| `coordinationhub/db.py` | SQLite schema + thread-local `ConnectionPool` | (no internal deps) |
| `coordinationhub/agent_registry.py` | Agent lifecycle: register, heartbeat, deregister, lineage | db |
| `coordinationhub/lock_ops.py` | Shared lock primitives (refresh, reap, record conflict, query) | db |
| `coordinationhub/conflict_log.py` | Conflict recording and querying | lock_ops |
| `coordinationhub/notifications.py` | Change notification storage and retrieval | db |
| `tests/conftest.py` | pytest fixtures: `engine`, `registered_agent`, `two_agents` | core |
| `tests/test_agent_lifecycle.py` | Agent lifecycle tests (16 tests) | conftest |
| `tests/test_locking.py` | Lock acquisition, release, refresh, status, reap (16 tests) | conftest |
| `tests/test_notifications.py` | Change notification tests (7 tests) | conftest |
| `tests/test_conflicts.py` | Conflict logging and lineage table tests (6 tests) | conftest |
| `tests/test_coordination.py` | Broadcast and wait_for_locks tests (7 tests) | conftest |
| `pyproject.toml` | Package config, dependencies, entry points | — |
| `wiki-local/spec-project.md` | Architecture, constraints, SQLite schema, MCP tool specs | — |
| `wiki-local/index.md` | Wiki navigation | — |
| `wiki-local/glossary.md` | Named concepts | — |

## Data Flow

### Agent Registration Flow

```
CLI/MCP → register_agent(agent_id, parent_id, worktree_root)
        → agent_registry.register_agent(connect, agent_id, worktree_root, parent_id)
        → INSERT INTO agents (ON CONFLICT UPDATE)
        → INSERT INTO lineage(parent_id, child_id) if parent_id set
        → _context_bundle(agent_id, parent_id) → returns coordination context JSON
```

### Lock Acquisition Flow

```
CLI/MCP → acquire_lock(document_path, agent_id, lock_type, ttl, force)
        → _normalize_path(document_path, project_root)
        → SELECT FROM document_locks WHERE document_path = ?
        → [row exists]
            → if owner == agent_id: _try_refresh_lock() → UPDATE TTL (self-renewal)
            → elif not expired and not force: _handle_contested_lock() → return conflict info
            → elif force: _steal_lock() → record_conflict + UPDATE (steal)
        → [row absent]
            → _insert_new_lock() → INSERT new lock
        → return {acquired, document_path, locked_by, expires_at}
```

### Heartbeat Flow

```
CLI/MCP → heartbeat(agent_id)
        → agent_registry.heartbeat(connect, agent_id) → UPDATE agents
        → return {updated, next_heartbeat_in}
```

Note: Lock reaping is NOT done in `heartbeat()`. Call `reap_expired_locks()` explicitly if needed.

### Stale Agent Reaping (Cascade Orphans)

```
CLI/MCP → reap_stale_agents(timeout=600)
        → SELECT agents WHERE status='active' AND last_heartbeat < (now - timeout)
        → For each stale agent:
            → Children: UPDATE parent_id = grandparent (orphaning)
            → UPDATE agents SET status = 'stopped'
        → Batch DELETE FROM document_locks WHERE locked_by IN (stale_agent_ids)
        → return {reaped, orphaned_children, locks_released}
```

### HTTP Server Lifecycle

```
CLI: coordinationhub serve
    → CoordinationHubMCPServer.start(blocking=True)
    → engine.start() → init_schema + init tables
    → generate_agent_id() → register server agent
    → ThreadedHTTPServer((host, port), MCPRequestHandler, engine)
    → heartbeat thread starts (every 30s)
    → serve_forever()
    → Ctrl-C → stop() → deregister → engine.close() → PRAGMA wal_checkpoint
```

## SQLite Schema

### `agents` Table

```sql
CREATE TABLE agents (
    agent_id      TEXT PRIMARY KEY,
    parent_id     TEXT,
    worktree_root TEXT NOT NULL,
    pid           INTEGER,
    started_at    REAL NOT NULL,
    last_heartbeat REAL NOT NULL,
    status        TEXT DEFAULT 'active'
)
```

### `lineage` Table

```sql
CREATE TABLE lineage (
    parent_id  TEXT NOT NULL,
    child_id    TEXT NOT NULL,
    spawned_at REAL NOT NULL,
    PRIMARY KEY (parent_id, child_id)
)
```

Index: `idx_lineage_parent ON lineage(parent_id)` for efficient lineage walks.

### `document_locks` Table

```sql
CREATE TABLE document_locks (
    document_path TEXT PRIMARY KEY,
    locked_by     TEXT NOT NULL,
    locked_at     REAL NOT NULL,
    lock_ttl      REAL DEFAULT 300.0,
    lock_type     TEXT DEFAULT 'exclusive',
    worktree_root TEXT
)
```

### `lock_conflicts` Table

```sql
CREATE TABLE lock_conflicts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    document_path TEXT NOT NULL,
    agent_a       TEXT NOT NULL,
    agent_b       TEXT NOT NULL,
    conflict_type TEXT NOT NULL,
    resolution    TEXT DEFAULT 'rejected',
    details_json  TEXT,
    created_at    REAL NOT NULL
)
```

### `change_notifications` Table

```sql
CREATE TABLE change_notifications (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    document_path TEXT NOT NULL,
    change_type   TEXT NOT NULL,
    agent_id      TEXT NOT NULL,
    worktree_root TEXT,
    created_at    REAL NOT NULL
)
```

## MCP Tool Schemas

All 20 tools are defined in `schemas.py` with JSON Schema `parameters` objects. The dispatch table maps each tool name to `(method_name, allowed_kwargs)`. Tool count is dynamic — `status()` returns `len(TOOL_DISPATCH)` (currently 20), not a hardcoded number.

## Transport Layer

### HTTP Transport (Primary)

- `mcp_server.py` defines `ThreadedHTTPServer(ThreadingMixIn, HTTPServer)`
- Endpoints: `GET /tools`, `GET /health`, `POST /call`
- Request: `{"tool": "<name>", "arguments": {<kwargs>}}`
- Response: `{"result": <result>}`
- Heartbeat runs in background thread every 30s

### Stdio Transport (Optional)

- `mcp_stdio.py` requires `mcp>=1.0.0` package
- Uses official MCP SDK `Server` + `stdio_server`
- Environment vars: `COORDINATIONHUB_STORAGE_DIR`, `COORDINATIONHUB_PROJECT_ROOT`, `COORDINATIONHUB_NAMESPACE`

## Entry Points

- `coordinationhub` (console script from `pyproject.toml`) → `cli.main()`
- `python -m coordinationhub.mcp_stdio` → stdio MCP server
- `python -m coordinationhub.mcp_server` → not exposed (use `coordinationhub serve`)

## Integration with Stele/Chisel/Trammel

CoordinationHub's `_context_bundle()` returns `coordination_urls` configurable via environment variables:

```python
"coordination_urls": {
    "coordinationhub": os.environ.get("COORDINATIONHUB_COORDINATION_URL", f"http://localhost:{self.DEFAULT_PORT}"),
    "stele": os.environ.get("COORDINATIONHUB_STELE_URL", "http://localhost:9876"),
    "chisel": os.environ.get("COORDINATIONHUB_CHISEL_URL", "http://localhost:8377"),
    "trammel": os.environ.get("COORDINATIONHUB_TRAMMEL_URL", "http://localhost:8737"),
}
```

Parent agents pass this bundle to spawned sub-agents so they know where all coordination services are reachable.

## Test Suite

```bash
python -m pytest tests/ -v
# 56 tests across 5 test files
```

All tests use in-memory temporary directories — no external DB or network required.
