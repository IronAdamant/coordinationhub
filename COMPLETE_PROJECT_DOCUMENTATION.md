# CoordinationHub — Complete Project Documentation

**Version:** 0.3.1
**Last updated:** 2026-04-06

## v0.3.1 Changelog

### Added
- **`spawn_propagation` assessment metric** — verifies child agents correctly inherit responsibilities from their parent via lineage.
- **`--graph-agent-id` CLI filter** for `assess` — filter assessment traces to a specific graph agent role.
- **Graph-role-aware file scan** — `.py` files assigned to `implement` roles, `.md/.yaml` to `document` roles, etc. when a coordination graph is loaded.
- **Spawned-agent file inheritance** — agents with a `parent_id` inherit their parent's graph role slice for file ownership.
- **Dashboard JSON mode** (`dashboard --json`) now includes full `file_map` with `graph_agent_id`, `role`, `responsibilities`, and `task_description` per file.
- **`get_agent_status` / `get_file_agent_map`** include `graph_agent_id` and `owned_files_with_tasks` (file→task mapping) for every agent.
- **Graph auto-mapping on load** — when a coordination spec is loaded, any registered agent whose `agent_id` matches a graph agent id automatically gets `agent_responsibilities` populated.
- Example files [`coordination_spec.yaml`](coordination_spec.yaml) and [`coordination_spec.json`](coordination_spec.json) added to repo root.

### Changed
- `run_assessment` now stores the full trace JSON and suggested graph refinements in `assessment_results.details_json`.
- `scan_project` returns a clear error for empty `extensions` list; gracefully skips with no graph loaded.
- `load_coordination_spec` returns a clear error if the specified path does not exist.

### Fixed
- Dashboard `--minimal` mode respects `--agent-id` filter.
- `load_coordination_spec` validates path existence before attempting disk I/O.

### Security
- Zero third-party dependencies in core unchanged (stdlib + SQLite only).

---

## File Inventory

| Path | Purpose | Dependencies |
|------|---------|--------------|
| `coordinationhub/__init__.py` | Package init, exports `CoordinationEngine`, `CoordinationHubMCPServer` | core, mcp_server |
| `coordinationhub/core.py` | `CoordinationEngine` — all 27 MCP tool methods + helpers (~465 LOC) | db, agent_registry, lock_ops, conflict_log, notifications, graphs, visibility, assessment, paths, context |
| `coordinationhub/paths.py` | Project-root detection and path normalization (~47 LOC) | (no internal deps) |
| `coordinationhub/context.py` | Context bundle builder for `register_agent` responses (~98 LOC) | (no internal deps) |
| `coordinationhub/schemas.py` | Schema aggregator — imports all groups, re-exports `TOOL_SCHEMAS` (~31 LOC) | (no internal deps) |
| `coordinationhub/schemas_identity.py` | Identity & Registration schemas (6 tools, ~123 LOC) | (no internal deps) |
| `coordinationhub/schemas_locking.py` | Document Locking schemas (7 tools, ~145 LOC) | (no internal deps) |
| `coordinationhub/schemas_coordination.py` | Coordination Action schemas (2 tools, ~59 LOC) | (no internal deps) |
| `coordinationhub/schemas_change.py` | Change Awareness schemas (3 tools, ~77 LOC) | (no internal deps) |
| `coordinationhub/schemas_audit.py` | Audit & Status schemas (2 tools, ~43 LOC) | (no internal deps) |
| `coordinationhub/schemas_visibility.py` | Graph & Visibility schemas (7 tools, ~137 LOC) | (no internal deps) |
| `coordinationhub/dispatch.py` | Tool dispatch table: name → (method_name, allowed_kwargs) (~48 LOC) | (no internal deps) |
| `coordinationhub/graphs.py` | Coordination graph — thin aggregator + auto-mapping on load (~145 LOC) | graph_validate, graph_loader, graph |
| `coordinationhub/graph_validate.py` | Pure validation functions: agents, handoffs, escalation, assessment (~131 LOC) | (no internal deps) |
| `coordinationhub/graph_loader.py` | File loading (YAML/JSON) and spec auto-detection (~49 LOC) | (no internal deps; optional ruamel.yaml) |
| `coordinationhub/graph.py` | CoordinationGraph in-memory object with lookup helpers (~66 LOC) | graph_validate |
| `coordinationhub/visibility.py` | Thin re-export aggregator for scan/agent_status/responsibilities (~20 LOC) | scan, agent_status, responsibilities |
| `coordinationhub/scan.py` | File ownership scan, graph-role-aware assignment, spawned-agent inheritance (~165 LOC) | (no internal deps) |
| `coordinationhub/agent_status.py` | Agent status query, file map, owned_files_with_tasks (~125 LOC) | (no internal deps) |
| `coordinationhub/responsibilities.py` | Agent role/responsibilities storage from graph (~35 LOC) | (no internal deps) |
| `coordinationhub/agent_registry.py` | Thin re-export aggregator for registry_ops/registry_query (~23 LOC) | registry_ops, registry_query |
| `coordinationhub/registry_ops.py` | Agent lifecycle ops: register, heartbeat, deregister (~107 LOC) | db |
| `coordinationhub/registry_query.py` | Agent registry queries: list, lineage, siblings, reaping (~142 LOC) | db |
| `coordinationhub/assessment.py` | Assessment runner, 5 metric scorers (incl. spawn_propagation), Markdown report (~510 LOC) | graphs |
| `coordinationhub/mcp_server.py` | HTTP MCP server (`ThreadedHTTPServer`, stdlib only) | core, dispatch, schemas |
| `coordinationhub/mcp_stdio.py` | Stdio MCP server (requires optional `mcp` package) | core, mcp_server, schemas |
| `coordinationhub/cli.py` | argparse CLI argument parser + lazy dispatch (~235 LOC) | core |
| `coordinationhub/cli_commands.py` | Re-exports all CLI handlers from domain sub-modules (~43 LOC) | cli_agents, cli_locks, cli_vis |
| `coordinationhub/cli_agents.py` | Agent identity & lifecycle CLI commands (~205 LOC) | core |
| `coordinationhub/cli_locks.py` | Document locking & coordination CLI commands (~214 LOC) | core |
| `coordinationhub/cli_vis.py` | Change awareness, audit, graph, assessment, dashboard CLI (~320 LOC) | core |
| `coordinationhub/db.py` | SQLite schema + thread-local `ConnectionPool` (~215 LOC) | (no internal deps) |
| `coordinationhub/lock_ops.py` | Shared lock primitives: acquire, release, refresh, reap (~119 LOC) | db |
| `coordinationhub/conflict_log.py` | Conflict recording and querying (~53 LOC) | lock_ops |
| `coordinationhub/notifications.py` | Change notification storage and retrieval (~115 LOC) | db |
| `coordinationhub/paths.py` | Project-root detection (~47 LOC) | (no internal deps) |
| `tests/conftest.py` | pytest fixtures: `engine`, `registered_agent`, `two_agents` | core |
| `tests/test_agent_lifecycle.py` | Agent lifecycle tests (19 tests) | conftest |
| `tests/test_locking.py` | Lock acquisition, release, refresh, status, reap (16 tests) | conftest |
| `tests/test_notifications.py` | Change notification tests (8 tests) | conftest |
| `tests/test_conflicts.py` | Conflict logging and lineage table tests (6 tests) | conftest |
| `tests/test_coordination.py` | Broadcast and wait_for_locks tests (7 tests) | conftest |
| `tests/test_visibility.py` | Visibility tools, file scan, graph loading tests (17 tests) | conftest, graphs |
| `tests/test_graphs.py` | Graph validation and CoordinationGraph tests (22 tests) | graphs |
| `tests/test_assessment.py` | Assessment runner tests (15 tests) | assessment, graphs |
| `tests/test_integration.py` | HTTP transport integration tests (15 tests) | conftest, core |
| `tests/test_core.py` | Core engine tests: graph delegation, path utils, agent ID generation (25 tests) | conftest |
| `pyproject.toml` | Package config, dependencies, entry points | — |

**Total: 150 tests across 11 test files.**

---

## Architecture

```
coordinationhub/
  __init__.py         — Package init, exports CoordinationEngine, CoordinationHubMCPServer
  core.py             — CoordinationEngine: all 27 tool methods + helpers (~465 LOC)
  paths.py            — Project-root detection and path normalization (~47 LOC)
  context.py          — Context bundle builder for register_agent responses (~98 LOC)
  schemas.py          — Schema aggregator, re-exports TOOL_SCHEMAS (~31 LOC)
  schemas_identity.py — Identity & Registration schemas (~123 LOC)
  schemas_locking.py   — Document Locking schemas (~145 LOC)
  schemas_coordination.py — Coordination Action schemas (~59 LOC)
  schemas_change.py    — Change Awareness schemas (~77 LOC)
  schemas_audit.py    — Audit & Status schemas (~43 LOC)
  schemas_visibility.py — Graph & Visibility schemas (~137 LOC)
  dispatch.py         — Tool dispatch table (~48 LOC)
  graphs.py           — Thin aggregator re-exporting + auto-mapping on load (~145 LOC)
  graph_validate.py   — Pure validation functions (~131 LOC)
  graph_loader.py     — File loading (YAML/JSON) and spec auto-detection (~49 LOC)
  graph.py            — CoordinationGraph in-memory object (~66 LOC)
  visibility.py       — Thin re-export aggregator (~20 LOC)
  scan.py             — File ownership scan, graph-role-aware (~165 LOC)
  agent_status.py     — Agent status query and file map helpers (~125 LOC)
  responsibilities.py — Agent role/responsibilities storage (~35 LOC)
  agent_registry.py   — Thin re-export aggregator (~23 LOC)
  registry_ops.py     — Agent lifecycle ops (~107 LOC)
  registry_query.py   — Agent registry queries (~142 LOC)
  assessment.py       — Assessment runner, 5 metric scorers (~510 LOC)
  mcp_server.py       — HTTP MCP server (ThreadedHTTPServer, stdlib only)
  mcp_stdio.py        — Stdio MCP server (requires optional mcp package)
  cli.py              — argparse CLI parser + lazy dispatch (~235 LOC)
  cli_commands.py     — Re-exports all CLI handlers (~43 LOC)
  cli_agents.py       — Agent identity & lifecycle CLI commands (~205 LOC)
  cli_locks.py        — Document locking & coordination CLI commands (~214 LOC)
  cli_vis.py          — Change awareness, audit, graph & assessment CLI (~320 LOC)
  db.py               — SQLite schema + thread-local ConnectionPool (~215 LOC)
  lock_ops.py         — Shared lock primitives (~119 LOC)
  conflict_log.py     — Conflict recording and querying (~53 LOC)
  notifications.py    — Change notification storage and retrieval (~115 LOC)
  tests/              — 150 tests across 11 test files
```

**Module design principles:**
- Zero internal deps in sub-modules: each receives `connect: ConnectFn` from the caller.
- Thread-local connection pool: `db.py` gives each thread its own SQLite connection (WAL mode, 30s busy timeout).
- Dispatch separation: `schemas.py` (schemas) and `dispatch.py` (dispatch table) shared by HTTP + stdio servers.
- `connect` callable pattern: `agent_registry.py`, `lock_ops.py`, `conflict_log.py`, `notifications.py`, `visibility.py` all receive `connect` rather than importing `_db.connect`.

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
      "protocol_adherence",
      "spawn_propagation"
    ]
  }
}
```

Supported file formats: `coordination_spec.yaml` (requires `ruamel.yaml`),
`coordination_spec.yml` (same), or `coordination_spec.json`.
Example files at repo root: `coordination_spec.yaml`, `coordination_spec.json`.

### Graph Loading

- Auto-loaded on `engine.start()` — engine checks project root for spec file.
- `load_coordination_spec(path?)` — reload or load from specific path. Returns error if path not found.
- `validate_graph()` — validate current graph, return errors.
- `CoordinationGraph` class provides `agent(id)`, `outgoing_handoffs(from_id)`,
  `handoff_targets(from_id)`, `is_valid()`, `validation_errors()`.

### Graph-to-Lineage Propagation

When `register_agent(agent_id, graph_agent_id="planner")` is called, the engine
looks up the agent definition in the loaded graph and stores `role`, `model`,
`responsibilities`, and `graph_agent_id` in `agent_responsibilities` table.
Spawned agents inherit via the existing lineage table.

When a coordination spec is loaded via `load_coordination_spec`, any registered
agent whose `agent_id` exactly matches a graph agent id automatically gets
`agent_responsibilities` populated (graph auto-mapping).

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

`scan_project(worktree_root?, extensions?, graph?)` performs a recursive scan of the
worktree. Files are matched against `DEFAULT_SCAN_EXTENSIONS = [".py", ".md",
".json", ".yaml", ".yml", ".txt", ".toml"]`.

Ownership assignment priority:
1. Exact path match in existing `file_ownership` table (preserves prior assignment).
2. Nearest ancestor directory with an assigned owner.
3. Coordination graph role (if loaded): `.py` → `implement` role, `.md/.yaml` → `document` role, `.json/.toml` → `config` role.
4. Spawned agent inherits parent's graph role slice (via lineage).
5. First-registered active agent (root agent fallback).

Excluded path components: `.git`, `__pycache__`, `.pytest_cache`, `node_modules`,
`.coordinationhub`, `.venv`, `venv`, `.env`, `.eggs`, `*.egg-info`,
`.mypy_cache`, `.tox`, `.ruff_cache`.

Scan results are **upserted** (insert or update on conflict).

---

## SQLite Schema

### `agents` Table

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

### `lineage` Table

```sql
CREATE TABLE lineage (
    parent_id  TEXT NOT NULL,
    child_id  TEXT NOT NULL,
    spawned_at REAL NOT NULL,
    PRIMARY KEY (parent_id, child_id)
)
```

### `document_locks` Table

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

### `lock_conflicts` Table

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

### `change_notifications` Table

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

### `agent_responsibilities` Table

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

### `file_ownership` Table

```sql
CREATE TABLE file_ownership (
    document_path     TEXT PRIMARY KEY,
    assigned_agent_id TEXT NOT NULL,
    assigned_at      REAL NOT NULL,
    last_claimed_by  TEXT,
    task_description TEXT
)
```

### `assessment_results` Table

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

`details_json` stores `{overall, trace_best, full_trace_json, suggested_refinements, graph_agent_id_filter}`.

---

## MCP Tools (27 total)

### Identity & Registration

| Tool | Description |
|------|-------------|
| `register_agent` | `graph_agent_id` param; returns `responsibilities`, `role`, `owned_files` from graph |
| `heartbeat` | Keep agent alive (updates timestamp only) |
| `deregister_agent` | Remove agent, orphan children, release locks |
| `list_agents` | List registered agents with staleness |
| `get_lineage` | Get ancestors and descendants of an agent |
| `get_siblings` | Get agents sharing the same parent |

### Document Locking

`acquire_lock`, `release_lock`, `refresh_lock`, `get_lock_status`,
`release_agent_locks`, `reap_expired_locks`, `reap_stale_agents`.

### Coordination Actions

`broadcast` — `message`/`action` params removed (were never stored or forwarded).

`wait_for_locks` — unchanged.

### Change Awareness

`notify_change`, `get_notifications`, `prune_notifications`.

### Audit

`get_conflicts`.

### Status

`status` — returns `graph_loaded: bool` and `owned_files: int`.

### Graph & Visibility (7 tools)

| Tool | Description |
|------|-------------|
| `load_coordination_spec` | Reload spec from disk, auto-maps registered agents to graph roles |
| `validate_graph` | Validate current graph, return error list |
| `scan_project` | File ownership scan with graph-role-aware assignment |
| `get_agent_status` | Full agent info: task, responsibilities, owned files (with task mapping), locks, lineage |
| `get_file_agent_map` | Full file→agent→role→responsibilities→task map |
| `update_agent_status` | Set `current_task` for an agent |
| `run_assessment` | Run suite, output report, store results incl. full traces + refinements |

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
        {"type": "register", "agent_id": "hub.1.0", "graph_id": "planner", "parent_id": ""},
        {"type": "register", "agent_id": "hub.1.0.0", "graph_id": "executor", "parent_id": "hub.1.0"},
        {"type": "handoff", "from": "planner", "to": "executor", "condition": "task_size < 500"},
        {"type": "lock", "path": "src/app.py", "agent_id": "hub.1.0.0"},
        {"type": "modified", "path": "src/app.py", "agent_id": "hub.1.0.0"},
        {"type": "unlock", "path": "src/app.py", "agent_id": "hub.1.0.0"}
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
- **spawn_propagation** (0–1): Verifies child agents correctly inherit responsibilities from their parent via lineage. Child events are checked against parent's declared scope.

Overall score = mean of all metric averages.

### CLI

```bash
coordinationhub assess --suite my_minimax_tests.json
coordinationhub assess --suite my_minimax_tests.json --format json --output report.md
coordinationhub assess --suite my_minimax_tests.json --graph-agent-id planner
```

Output: Markdown report to stdout (or `--output` file), JSON stored in SQLite
`assessment_results` table (includes full trace JSON and suggested graph refinements).

### Suggested Graph Refinements

After running an assessment, `suggested_refinements` lists:
- Missing handoff edges (used in traces but not in graph)
- Missing agent roles (registered in traces but not defined in graph)

---

## CLI Subcommands (27 total)

### Server
`serve`, `serve-mcp`

### Status & Visibility
`status`, `dashboard` (human + JSON with full file_map), `agent-status`

### Graph
`load-spec`, `validate-spec`

### File Ownership
`scan-project`

### Assessment
`assess` (supports `--graph-agent-id` filter)

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
# 150 tests across 11 test files
```

---

## Migration from 0.2.0 / 0.3.0

- All existing tables (`agents`, `lineage`, `document_locks`, `lock_conflicts`,
  `change_notifications`) are preserved unchanged.
- Existing `.coordinationhub/coordination.db` files work without migration.
- `broadcast` no longer accepts `message` or `action` params (removed from schema and CLI).
- `status()` now returns `graph_loaded: bool` and `owned_files: int` as additional fields.
- `register_agent()` now accepts an optional `graph_agent_id` parameter.
- New tables added: `agent_responsibilities`, `file_ownership`, `assessment_results`.
- `spawn_propagation` metric added in 0.3.1 (scored even if not listed in graph assessment.metrics).
- `run_assessment` now stores full trace JSON and suggested graph refinements in `details_json`.
