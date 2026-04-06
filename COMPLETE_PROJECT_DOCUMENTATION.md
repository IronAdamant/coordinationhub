# CoordinationHub — Complete Project Documentation

**Version:** 0.3.0
**Last updated:** 2026-04-06

## File Inventory

| Path | Purpose | Dependencies |
|------|---------|--------------|
| `coordinationhub/__init__.py` | Package init, exports `CoordinationEngine`, `CoordinationHubMCPServer` | core, mcp_server |
| `coordinationhub/core.py` | `CoordinationEngine` — all 27 MCP tool methods + helpers (~524 LOC) | db, agent_registry, lock_ops, conflict_log, notifications, graphs, visibility, assessment |
| `coordinationhub/schemas.py` | JSON Schema for all 27 tool parameters (~574 LOC) | (no internal deps) |
| `coordinationhub/dispatch.py` | Tool dispatch table: name → (method_name, allowed_kwargs) (~48 LOC) | (no internal deps) |
| `coordinationhub/graphs.py` | Coordination graph loader + validator + in-memory `CoordinationGraph` | (no internal deps; optional ruamel.yaml) |
| `coordinationhub/visibility.py` | File ownership scan, agent status, file map helpers | graphs |
| `coordinationhub/assessment.py` | Assessment runner, 4 metric scorers, Markdown report generator (~397 LOC) | graphs |
| `coordinationhub/mcp_server.py` | HTTP MCP server (`ThreadedHTTPServer`, stdlib only) | core, dispatch, schemas |
| `coordinationhub/mcp_stdio.py` | Stdio MCP server (requires optional `mcp` package) | core, mcp_server, schemas |
| `coordinationhub/cli.py` | argparse CLI argument parser + lazy dispatch (~229 LOC) | core |
| `coordinationhub/cli_commands.py` | All 26 CLI command handlers, imported on-demand (~671 LOC) | core |
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
| `tests/test_visibility.py` | Visibility tools, file scan, graph loading tests (14 tests) | conftest, graphs |
| `tests/test_graphs.py` | Graph validation and CoordinationGraph tests (14 tests) | graphs |
| `tests/test_assessment.py` | Assessment runner tests (9 tests) | assessment, graphs |
| `pyproject.toml` | Package config, dependencies, entry points | — |

**Total: 106 tests across 9 test files.**

---

## Architecture

```
coordinationhub/
  __init__.py         — Package init, exports CoordinationEngine, CoordinationHubMCPServer
  core.py             — CoordinationEngine: all 27 tool methods + helpers (~524 LOC)
  schemas.py          — JSON Schema for all 27 tool parameters (~574 LOC)
  dispatch.py        — Tool dispatch table: name → (method_name, allowed_kwargs) (~48 LOC)
  graphs.py          — Coordination graph loader + CoordinationGraph (~310 LOC)
  visibility.py       — File ownership scan, agent status, file map (~233 LOC)
  assessment.py       — Assessment runner (~397 LOC)
  mcp_server.py       — HTTP MCP server (ThreadedHTTPServer, stdlib only)
  mcp_stdio.py        — Stdio MCP server (requires optional mcp package)
  cli.py              — argparse CLI parser + lazy dispatch (~229 LOC)
  cli_commands.py    — All 26 command handlers, imported on-demand (~671 LOC)
  db.py               — SQLite schema + thread-local ConnectionPool
  agent_registry.py   — Agent lifecycle: register, heartbeat, deregister, lineage
  lock_ops.py         — Shared lock primitives
  conflict_log.py     — Conflict recording and querying
  notifications.py    — Change notification storage and retrieval
  tests/              — 106 tests across 9 test files
```

**Module design principles:**
- Zero internal deps in sub-modules: each receives `connect: ConnectFn` from the caller
- Thread-local connection pool: `db.py` gives each thread its own SQLite connection (WAL mode, 30s busy timeout)
- Dispatch separation: `schemas.py` (schemas) and `dispatch.py` (dispatch table) shared by HTTP + stdio servers
- `connect` callable pattern: `agent_registry.py`, `lock_ops.py`, `conflict_log.py`, `notifications.py`, `visibility.py` all receive `connect` rather than importing `_db.connect`

---

## Declarative Coordination Graph

### Schema

```json
{
  "agents": [
    {
      "id": "planner",
      "role": "decompose tasks",
      "model": "minimax-m2.7",
      "responsibilities": ["break down user stories", "assign subtasks"]
    },
    {
      "id": "executor",
      "role": "implement",
      "model": "minimax-m2.7",
      "responsibilities": ["write code", "run tests"]
    }
  ],
  "handoffs": [
    {
      "from": "planner",
      "to": "executor",
      "condition": "task_size < 500 && no_blockers"
    }
  ],
  "escalation": {
    "max_retries": 3,
    "fallback": "human_review"
  },
  "assessment": {
    "metrics": [
      "role_stability",
      "handoff_latency",
      "outcome_verifiability",
      "protocol_adherence"
    ]
  }
}
```

Supported file formats: `coordination_spec.yaml` (requires `ruamel.yaml`),
`coordination_spec.yml` (same), or `coordination_spec.json`.

### Graph Loading

- Auto-loaded on `engine.start()` — engine checks project root for spec file.
- `load_coordination_spec(path?)` — reload or load from specific path.
- `validate_graph()` — validate current graph, return errors.
- `CoordinationGraph` class provides `agent(id)`, `outgoing_handoffs(from_id)`,
  `handoff_targets(from_id)`, `is_valid()`, `validation_errors()`.

### Graph-to-Lineage Propagation

When `register_agent(agent_id, graph_agent_id="planner")` is called, the engine
looks up the agent definition in the loaded graph and stores `role`, `model`,
`responsibilities`, and `graph_agent_id` in `agent_responsibilities` table.
Spawned agents inherit via the existing lineage table.

---

## File Ownership & Project Scan

### `file_ownership` Table

```sql
CREATE TABLE file_ownership (
    document_path     TEXT PRIMARY KEY,
    assigned_agent_id TEXT NOT NULL,
    assigned_at      REAL NOT NULL,
    last_claimed_by  TEXT,
    task_description TEXT
);
```

### Scan Behaviour

`scan_project(worktree_root?, extensions?)` performs a recursive scan of the
worktree. Files are matched against `DEFAULT_SCAN_EXTENSIONS = [".py", ".md",
".json", ".yaml", ".yml", ".txt", ".toml"]`.

Ownership assignment priority:
1. Exact path match in existing `file_ownership` table (preserves prior assignment).
2. Nearest ancestor directory with an assigned owner.
3. First-registered active agent (root agent fallback).

Excluded paths: any path component starting with `.`, or any part named
`__pycache__`, `.pytest_cache`, `node_modules`, `.coordinationhub`.

Scan results are **upserted** (insert or update on conflict).

---

## SQLite Schema

### `agents` Table (unchanged)

```sql
CREATE TABLE agents (
    agent_id      TEXT PRIMARY KEY,
    parent_id    TEXT,
    worktree_root TEXT NOT NULL,
    pid          INTEGER,
    started_at   REAL NOT NULL,
    last_heartbeat REAL NOT NULL,
    status       TEXT DEFAULT 'active'
)
```

### `lineage` Table (unchanged)

```sql
CREATE TABLE lineage (
    parent_id  TEXT NOT NULL,
    child_id  TEXT NOT NULL,
    spawned_at REAL NOT NULL,
    PRIMARY KEY (parent_id, child_id)
)
```

### `document_locks` Table (unchanged)

```sql
CREATE TABLE document_locks (
    document_path TEXT PRIMARY KEY,
    locked_by    TEXT NOT NULL,
    locked_at    REAL NOT NULL,
    lock_ttl     REAL DEFAULT 300.0,
    lock_type    TEXT DEFAULT 'exclusive',
    worktree_root TEXT
)
```

### `lock_conflicts` Table (unchanged)

```sql
CREATE TABLE lock_conflicts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    document_path TEXT NOT NULL,
    agent_a     TEXT NOT NULL,
    agent_b     TEXT NOT NULL,
    conflict_type TEXT NOT NULL,
    resolution  TEXT DEFAULT 'rejected',
    details_json TEXT,
    created_at   REAL NOT NULL
)
```

### `change_notifications` Table (unchanged)

```sql
CREATE TABLE change_notifications (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    document_path TEXT NOT NULL,
    change_type  TEXT NOT NULL,
    agent_id     TEXT NOT NULL,
    worktree_root TEXT,
    created_at   REAL NOT NULL
)
```

### `agent_responsibilities` Table (NEW in 0.3.0)

```sql
CREATE TABLE agent_responsibilities (
    agent_id        TEXT PRIMARY KEY,
    graph_agent_id  TEXT,
    role            TEXT,
    model           TEXT,
    responsibilities TEXT,
    current_task    TEXT,
    updated_at      REAL NOT NULL
)
```

`responsibilities` is stored as a JSON-encoded list.

### `file_ownership` Table (NEW in 0.3.0)

```sql
CREATE TABLE file_ownership (
    document_path     TEXT PRIMARY KEY,
    assigned_agent_id TEXT NOT NULL,
    assigned_at      REAL NOT NULL,
    last_claimed_by  TEXT,
    task_description TEXT
)
```

### `assessment_results` Table (NEW in 0.3.0)

```sql
CREATE TABLE assessment_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    suite_name  TEXT NOT NULL,
    metric      TEXT NOT NULL,
    score       REAL NOT NULL,
    details_json TEXT,
    run_at      REAL NOT NULL
)
```

---

## MCP Tools (27 total)

### Identity & Registration

| Tool | New in 0.3.0 |
|------|-------------|
| `register_agent` | `graph_agent_id` param; returns `responsibilities`, `role`, `owned_files` from graph |
| `heartbeat` | — |
| `deregister_agent` | — |
| `list_agents` | — |
| `get_lineage` | — |
| `get_siblings` | — |

### Document Locking (unchanged)

`acquire_lock`, `release_lock`, `refresh_lock`, `get_lock_status`,
`release_agent_locks`, `reap_expired_locks`, `reap_stale_agents`.

### Coordination Actions

`broadcast` — `message`/`action` params removed (were never stored or forwarded).

`wait_for_locks` — unchanged.

### Change Awareness (unchanged)

`notify_change`, `get_notifications`, `prune_notifications`.

### Audit (unchanged)

`get_conflicts`.

### Status

`status` — now returns `graph_loaded: bool` and `owned_files: int` in addition
to previous fields.

### Graph & Visibility (7 NEW tools in 0.3.0)

| Tool | Description |
|------|-------------|
| `load_coordination_spec` | Reload spec from disk, returns agent list |
| `validate_graph` | Validate current graph, return error list |
| `scan_project` | Perform file ownership scan, return counts |
| `get_agent_status` | Full agent info: task, responsibilities, owned files, locks, lineage |
| `get_file_agent_map` | Full file→agent map with roles |
| `update_agent_status` | Set `current_task` for an agent |
| `run_assessment` | Run assessment suite, output report + JSON scores |

---

## Assessment Runner

### Suite Format

```json
{
  "name": "my_minimax_tests",
  "traces": [
    {
      "trace_id": "trace_001",
      "events": [
        {"type": "register", "agent_id": "hub.1.0", "graph_id": "planner"},
        {"type": "handoff", "from": "planner", "to": "executor", "condition": "task_size < 500"},
        {"type": "lock", "path": "src/app.py", "agent_id": "hub.1.0"},
        {"type": "modified", "path": "src/app.py", "agent_id": "hub.1.0"},
        {"type": "unlock", "path": "src/app.py", "agent_id": "hub.1.0"}
      ]
    }
  ]
}
```

### Metrics (all with real implementations)

- **role_stability** (0–1): Maps event types to declared responsibilities in the graph. Penalizes events outside the agent's declared scope.
- **handoff_latency** (0–1): Validates handoff from/to pairs against graph definitions. Partial credit for correct pairs, full credit when condition is present.
- **outcome_verifiability** (0–1): Evaluates lock-write-unlock patterns per file. Scores verified (lock precedes modify) vs unverified (modify without lock).
- **protocol_adherence** (0–1): Checks agents act within declared responsibilities. Violations reduce score proportionally.

Overall score = mean of all metric averages.

### CLI

```bash
coordinationhub assess --suite my_minimax_tests.json
coordinationhub assess --suite my_minimax_tests.json --format json --output report.md
```

Output: Markdown report to stdout (or `--output` file), JSON stored in SQLite
`assessment_results` table.

---

## CLI Subcommands (26 total)

### Server
`serve`, `serve-mcp`

### Status & Visibility (0.3.0)
`status`, `dashboard` (human + JSON), `agent-status`

### Graph (0.3.0)
`load-spec`, `validate-spec`

### File Ownership (0.3.0)
`scan-project`

### Assessment (0.3.0)
`assess`

### Agent Lifecycle
`register`, `heartbeat`, `deregister`, `list-agents`, `lineage`, `siblings`

### Locking
`acquire-lock`, `release-lock`, `refresh-lock`, `lock-status`, `release-agent-locks`,
`reap-expired-locks`, `reap-stale-agents`

### Coordination
`broadcast`, `wait-for-locks`

### Change Awareness
`notify-change`, `get-notifications`, `prune-notifications`

### Audit
`get-conflicts`

---

## Transport Layer

### HTTP Transport (Primary)

- `mcp_server.py` defines `ThreadedHTTPServer(ThreadingMixIn, HTTPServer)`
- Endpoints: `GET /tools`, `GET /health`, `POST /call`
- Request: `{"tool": "<name>", "arguments": {<kwargs>}}`
- Response: `{"result": <result>}`

### Stdio Transport (Optional)

- `mcp_stdio.py` requires `mcp>=1.0.0` package
- Environment vars: `COORDINATIONHUB_STORAGE_DIR`, `COORDINATIONHUB_PROJECT_ROOT`,
  `COORDINATIONHUB_NAMESPACE`

---

## Entry Points

- `coordinationhub` (console script) → `cli.main()`
- `python -m coordinationhub.mcp_stdio` → stdio MCP server

---

## Zero-Dependency Guarantee

Core modules use **only Python standard library** (`sqlite3`, `pathlib`, `json`,
`os`, `time`, `threading`, `http.server`, `socketserver`, `argparse`).

The `mcp` package is **optional** — only needed for `mcp_stdio.py`.
The `ruamel.yaml` package is **optional** — only needed for YAML spec files.
Air-gapped install: `pip install coordinationhub --no-deps`.

---

## Test Suite

```bash
python -m pytest tests/ -v
# 106 tests across 9 test files
```

---

## Migration from 0.2.0

- All existing tables (`agents`, `lineage`, `document_locks`, `lock_conflicts`,
  `change_notifications`) are preserved unchanged.
- Existing `.coordinationhub/coordination.db` files work without migration.
- `broadcast` no longer accepts `message` or `action` params (removed from schema and CLI).
- `status()` now returns `graph_loaded: bool` and `owned_files: int` as additional fields.
- `register_agent()` now accepts an optional `graph_agent_id` parameter.
- New tables added: `agent_responsibilities`, `file_ownership`, `assessment_results`.
