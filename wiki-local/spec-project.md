# CoordinationHub — Multi-Agent Swarm Coordination MCP

**Version:** 0.3.4
**Language:** Python 3.10+ (stdlib-only core — **zero third-party dependencies**, `mcp` optional for stdio only)
**Transports:** stdio + HTTP (both, like Stele/Chisel/Trammel)

## Purpose

CoordinationHub externalizes the coordination bottleneck for multi-agent coding swarms. It tracks agent identity and lineage, enforces document locking, detects lock conflicts, propagates coordination context to spawned sub-agents, and provides a shared ground truth for "who is doing what" across all LLMs and IDEs.

Works standalone or alongside Stele, Chisel, and Trammel. Configure other MCP server URLs via their own environment variables.

Each works standalone. When co-installed, they cooperate through each LLM's MCP tool layer.

---

## Non-Goals

- Not a task queue or job scheduler — agents retain full autonomy
- Not a message bus — agents communicate by convention, not by message passing
- Not a code review system — lock coordination does not imply approval
- Not dependent on any specific LLM or IDE — pure MCP server
- **Zero third-party dependencies in core** — supply chain security is non-negotiable

---

## Zero-Dependency Guarantee

The **core** module (all `.py` files except `mcp_stdio.py`) uses **only the Python standard library**:

| Module | Stdlib dependencies used |
|--------|--------------------------|
| `db.py` | `sqlite3`, `threading`, `pathlib` |
| `agent_registry.py` | `sqlite3`, `time`, `os` |
| `lock_ops.py` | `sqlite3`, `time` |
| `conflict_log.py` | `sqlite3`, `time`, `json` |
| `notifications.py` | `sqlite3`, `time` |
| `core.py` | `sqlite3`, `pathlib`, `os`, `time`, `json`, `threading` |
| `graphs.py` | `pathlib`, `json` (optional `ruamel.yaml`) |
| `visibility.py` | `pathlib`, `time`, `json` |
| `assessment.py` | `pathlib`, `time`, `json`, `sqlite3` |
| `schemas.py` | `pathlib`, `json` |
| `dispatch.py` | (no deps) |
| `mcp_server.py` | `http.server`, `socketserver`, `json`, `threading` |
| `cli.py` | `argparse`, `pathlib` |
| `cli_commands.py` | `argparse`, `pathlib`, `json` |

**No third-party packages in core.** No `requests`, no `httpx`, no `aiohttp`, no external HTTP libraries. The HTTP server is built entirely on `http.server` + `socketserver.ThreadingMixIn`.

The `mcp` package (from the official MCP SDK) is **optional** — only needed for the stdio transport shim (`mcp_stdio.py`). The HTTP transport works without it.

**Air-gapped install:** `pip install -e . --no-deps` installs everything needed for HTTP transport. Stdin/stdout transport requires `pip install -e '.[mcp]'` only if stdio is needed.

---

## Core Concepts

### Agent Identity

Every agent has a **globally unique ID** of the form:

```
${PREFIX}.${WORKTREE_PID}.${AGENT_SEQ}
```

- `PREFIX`: configurable namespace (default: `hub`)
- `WORKTREE_PID`: process ID of the worktree root's hosting process
- `AGENT_SEQ`: monotonically increasing sequence number per worktree

Example: `hub.12345.0`, `hub.12345.1`, `hub.12345.1.0` (child of `hub.12345.1`)

### Agent Lineage

When agent A spawns agent B:
1. A is the **parent**, B is the **child**
2. B receives a sequence number under A's namespace branch
3. The lineage is recorded in the DB as `(parent_id, child_id, spawned_at)`
4. B's ID encodes the full path: `hub.PID.parent_seq.child_seq`

### Coordination Context Bundle

When an agent registers (or when a parent spawns a child), the bundle returned is:

```json
{
  "agent_id": "hub.12345.1.0",
  "parent_id": "hub.12345.1",
  "worktree_root": "/home/aron/Documents/coding_projects/myproject",
  "registered_agents": [...],
  "active_locks": [...],
  "pending_notifications": [...],
  "coordination_url": "http://localhost:9877"
}
```

### Document Locking

Files are locked before modification, released after. Locks have:
- **TTL** (default 300s): auto-expire if agent dies
- **Owner**: only the agent that acquired it may release it
- **Force-steal**: override with conflict recording
- **Shared locks**: for reads; **exclusive locks**: for writes
- **Region locking**: optional `region_start`/`region_end` for sub-file granularity. Multiple non-overlapping locks per file. Shared locks on overlapping regions are permitted; exclusive locks block all others.

### Declarative Coordination Graph

Agents, handoffs, escalation rules, and assessment criteria defined in
`coordination_spec.yaml` (or `.json`) at project root. The graph is loaded
automatically on engine startup.

```yaml
agents:
  - id: planner
    role: decompose tasks
    responsibilities: [break down user stories, assign subtasks]
  - id: executor
    role: implement
    responsibilities: [write code, run tests]

handoffs:
  - from: planner
    to: executor
    condition: task_size < 500

assessment:
  metrics: [role_stability, handoff_latency, outcome_verifiability, protocol_adherence, spawn_propagation]
```

### File Ownership

`scan_project(worktree_root?, extensions?)` recursively scans the worktree
and upserts every tracked file into `file_ownership`. Ownership is assigned
by nearest-ancestor directory rule, with fallback to the first-registered
active agent.

### Assessment Runner

`run_assessment(suite_path, format?, graph_agent_id?)` loads a JSON trace suite, scores each
trace against 5 metric scorers, and outputs a Markdown report. Metric scorers:
- **role_stability**: events mapped to declared responsibilities in graph
- **handoff_latency**: handoff from/to pairs validated against graph
- **outcome_verifiability**: lock-write-unlock patterns per file
- **protocol_adherence**: agents act within declared responsibilities
- **spawn_propagation**: child agents inherit and act within parent's declared scope

---

## SQLite Schema (v0.3.4)

### Tables

#### `agents`

| Column | Type | Description |
|--------|------|-------------|
| `agent_id` | TEXT PK | Global unique ID |
| `parent_id` | TEXT | Parent agent ID (NULL for root) |
| `worktree_root` | TEXT NOT NULL | Project root for this agent |
| `pid` | INTEGER | OS process ID |
| `started_at` | REAL NOT NULL | Unix timestamp |
| `last_heartbeat` | REAL NOT NULL | Unix timestamp |
| `status` | TEXT DEFAULT 'active' | 'active' or 'stopped' |

#### `lineage`

| Column | Type | Description |
|--------|------|-------------|
| `parent_id` | TEXT PK (composite) | Parent agent ID |
| `child_id` | TEXT PK (composite) | Child agent ID |
| `spawned_at` | REAL NOT NULL | Unix timestamp |

#### `document_locks`

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment |
| `document_path` | TEXT NOT NULL | Project-relative path |
| `locked_by` | TEXT NOT NULL | Agent ID |
| `locked_at` | REAL NOT NULL | Unix timestamp |
| `lock_ttl` | REAL DEFAULT 300.0 | Seconds until expiry |
| `lock_type` | TEXT DEFAULT 'exclusive' | 'shared' or 'exclusive' |
| `worktree_root` | TEXT | Which worktree |
| `region_start` | INTEGER | Start of locked region (NULL = whole file) |
| `region_end` | INTEGER | End of locked region (NULL = whole file) |

Multiple locks per file are allowed for non-overlapping regions. Shared locks on overlapping regions are permitted; exclusive locks block all others on the same region.

#### `lock_conflicts`

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment |
| `document_path` | TEXT NOT NULL | Path |
| `agent_a` | TEXT NOT NULL | First agent |
| `agent_b` | TEXT NOT NULL | Second agent |
| `conflict_type` | TEXT NOT NULL | 'lock_denied', 'lock_stolen', 'write_conflict' |
| `resolution` | TEXT DEFAULT 'rejected' | 'rejected', 'force_overwritten', 'waited_retry', 'aborted' |
| `details_json` | TEXT | Arbitrary metadata |
| `created_at` | REAL NOT NULL | Unix timestamp |

#### `change_notifications`

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment |
| `document_path` | TEXT NOT NULL | Path |
| `change_type` | TEXT NOT NULL | 'created', 'modified', 'deleted', 'locked' |
| `agent_id` | TEXT NOT NULL | Who triggered it |
| `worktree_root` | TEXT | Worktree |
| `created_at` | REAL NOT NULL | Unix timestamp |

#### `agent_responsibilities` (NEW in 0.3.0)

| Column | Type | Description |
|--------|------|-------------|
| `agent_id` | TEXT PK | Agent ID |
| `graph_agent_id` | TEXT | ID in the coordination graph |
| `role` | TEXT | Role string |
| `model` | TEXT | Model name |
| `responsibilities` | TEXT | JSON-encoded list |
| `current_task` | TEXT | Human-readable current task |
| `updated_at` | REAL NOT NULL | Unix timestamp |

#### `file_ownership` (NEW in 0.3.0)

| Column | Type | Description |
|--------|------|-------------|
| `document_path` | TEXT PK | Project-relative path |
| `assigned_agent_id` | TEXT NOT NULL | Agent who owns this file |
| `assigned_at` | REAL NOT NULL | Unix timestamp |
| `last_claimed_by` | TEXT | Agent who last claimed ownership |
| `task_description` | TEXT | Description of work on this file |

#### `assessment_results` (NEW in 0.3.0)

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment |
| `suite_name` | TEXT NOT NULL | Test suite name |
| `metric` | TEXT NOT NULL | Metric name |
| `score` | REAL NOT NULL | Score (0–1) |
| `details_json` | TEXT | Additional details |
| `run_at` | REAL NOT NULL | Unix timestamp |

---

## MCP Tools (29 total — v0.3.4)

### Identity & Registration

`register_agent`, `heartbeat`, `deregister_agent`, `list_agents`, `get_lineage`, `get_siblings`

### Document Locking

`acquire_lock`, `release_lock`, `refresh_lock`, `get_lock_status`, `list_locks`,
`release_agent_locks`, `reap_expired_locks`, `reap_stale_agents`

### Coordination Actions

`broadcast` — checks lock state only, no message forwarding
`wait_for_locks`

### Change Awareness

`notify_change`, `get_notifications`, `prune_notifications`

### Audit

`get_conflicts`, `status`

### Graph & Visibility (8 tools in 0.3.1)

`load_coordination_spec`, `validate_graph`, `scan_project`,
`get_agent_status`, `get_file_agent_map`, `update_agent_status`, `run_assessment`, `get_agent_tree`

---

## Project Layout (v0.3.4)

```
coordinationhub/
  __init__.py          -- __version__, public API
  core.py              -- CoordinationEngine: identity, change, audit, graph/visibility (~260 LOC)
  core_locking.py      -- LockingMixin: all locking + coordination methods (~230 LOC)
  paths.py             -- Project-root detection and path normalization (~47 LOC)
  context.py           -- Context bundle builder for register_agent responses (~98 LOC)
  schemas.py           -- Schema aggregator, re-exports TOOL_SCHEMAS (~31 LOC)
  schemas_identity.py  -- Identity & Registration schemas (~123 LOC)
  schemas_locking.py   -- Document Locking schemas (~165 LOC)
  schemas_coordination.py -- Coordination Action schemas (~59 LOC)
  schemas_change.py    -- Change Awareness schemas (~77 LOC)
  schemas_audit.py     -- Audit & Status schemas (~43 LOC)
  schemas_visibility.py -- Graph & Visibility schemas (8 tools, ~156 LOC)
  dispatch.py          -- Tool dispatch table (~48 LOC)
  graphs.py            -- Thin aggregator + graph auto-mapping (~145 LOC)
  graph_validate.py    -- Pure validation functions (~131 LOC)
  graph_loader.py      -- File loading (YAML/JSON) and spec auto-detection (~49 LOC)
  graph.py             -- CoordinationGraph in-memory object (~66 LOC)
  visibility.py        -- Thin re-export aggregator (~20 LOC)
  scan.py              -- File ownership scan, graph-role-aware (~165 LOC)
  agent_status.py      -- Agent status query, file map, and agent tree helpers (~225 LOC)
  responsibilities.py   -- Agent role/responsibilities storage (~35 LOC)
  agent_registry.py    -- Agent lifecycle (registry_ops + registry_query)
  registry_ops.py      -- Agent lifecycle ops (~107 LOC)
  registry_query.py    -- Agent registry queries (~142 LOC)
  assessment_scorers.py -- 5 metric scorers + event_matches_responsibility (~315 LOC)
  assessment.py        -- Suite loading, run_assessment, report, storage (~241 LOC)
  mcp_server.py        -- HTTP MCP server (ThreadedHTTPServer, stdlib only)
  mcp_stdio.py         -- Stdio MCP server (requires optional mcp package)
  cli.py               -- argparse CLI parser + lazy dispatch (~235 LOC)
  cli_commands.py      -- Re-exports all CLI handlers (~44 LOC)
  cli_agents.py        -- Agent identity & lifecycle CLI commands (~205 LOC)
  cli_locks.py         -- Document locking & coordination CLI (~214 LOC)
  cli_vis.py           -- Change awareness, audit, graph & assessment CLI + agent-tree (~346 LOC)
  db.py                -- SQLite schema, schema versioning, perf pragmas, thread-local ConnectionPool (~280 LOC)
  lock_ops.py          -- Shared lock primitives + region overlap (~175 LOC)
  conflict_log.py      -- Conflict recording (~53 LOC)
  notifications.py     -- Change notification storage (~115 LOC)
tests/
  conftest.py
  test_agent_lifecycle.py
  test_locking.py
  test_notifications.py
  test_conflicts.py
  test_coordination.py
  test_visibility.py
  test_graphs.py
  test_assessment.py
  test_integration.py
  test_core.py
pyproject.toml
coordination_spec.yaml   -- Example spec (YAML)
coordination_spec.json    -- Example spec (JSON)
README.md
CLAUDE.md
COMPLETE_PROJECT_DOCUMENTATION.md
LLM_Development.md
wiki-local/
  spec-project.md
  glossary.md
  index.md
```

---

## Transport Layer

### stdio (`mcp_stdio.py`)

```bash
coordinationhub serve-mcp
```

### HTTP (`mcp_server.py`)

```bash
coordinationhub serve --port 9877
```

Default port: `9877`

---

## Port Allocation

| Server | Default Port |
|--------|-------------|
| CoordinationHub | 9877 |
| Stele | 9876 |
| Chisel | 8377 |
| Trammel | 8737 |

---

## Version History

### 0.3.4 — Core split, assessment synonyms, SQLite perf (2026-04-10)
- `core.py` split: locking/coordination methods extracted to `core_locking.py` (~230 LOC) as `LockingMixin`
- `core.py` reduced from ~495 to ~260 LOC; `CoordinationEngine` inherits `LockingMixin`
- `_EVENT_RESPONSIBILITY_MAP` expanded with ~20 synonyms + token-overlap fallback
- SQLite perf: `cache_size=-8000`, `mmap_size=67108864`, composite `idx_locks_expiry` index

### 0.3.3 — Region locking & CI (2026-04-10)
- CI test workflow: `.github/workflows/test.yml` runs pytest on push/PR across Python 3.10-3.12
- DB schema versioning: `schema_version` table, `_CURRENT_SCHEMA_VERSION = 2`, auto-migration
- Region locking: `document_locks` restructured with `region_start`/`region_end` columns, shared lock enforcement
- `acquire_lock` uses `BEGIN IMMEDIATE` for thread-safe concurrent locking
- Hook unit tests: 23 tests in `test_hooks.py`
- 246 tests across 15 files (up from 206 across 14)

### 0.3.2 — Review Ten fixes (2026-04-10)
- `list_locks` tool + CLI command
- Hook TTL reduced from 600s to 120s, pre-acquire reaping
- 206 tests across 14 files

### 0.3.1 — Polish pass (2026-04-07)
- `spawn_propagation` metric: child agents scored against inherited parent responsibilities
- Graph-role-aware file scan: `.py` → implement, `.md/.yaml` → document, `.json/.toml` → config
- `run_assessment --graph-agent-id` filter for trace-level scoring
- Full trace JSON and suggested graph refinements stored in `assessment_results.details_json`
- Dashboard JSON output includes full `file_map` with `graph_agent_id`, `role`, `task_description`
- `get_agent_status` returns `owned_files_with_tasks` with file→task mapping
- `get_file_agent_map` includes `graph_agent_id` per entry
- Graph auto-mapping: `load_coordination_spec` populates `agent_responsibilities` for matching registered agents
- Input validation: clear errors for missing spec path and empty extensions list
- 165 tests (up from 150)
- Example files `coordination_spec.yaml` and `coordination_spec.json` added to repo root

### 0.1.0 — Initial design
- Agent identity and lineage tracking
- Document locking with TTL and force-steal
- Conflict logging
- Change notifications (poll-based)
- Broadcast to siblings
- Heartbeat with stale detection and cascade orphaning
- stdio and HTTP transports
- 17 MCP tools

### 0.2.0 — Audit fixes
- `lineage` table composite PK fix
- `generate_agent_id` double-dot collision fix
- `record_conflict` bind count fix
- `refresh_lock` expiry arithmetic fix
- `broadcast` message/action params removed
- 20 MCP tools

### 0.3.0 — Strategic redesign
- Declarative coordination graphs (YAML/JSON)
- File ownership tracking via worktree scan
- Visibility layer: `get_agent_status`, `get_file_agent_map`, `dashboard`
- Assessment runner with 5 real metric scorers
- 29 MCP tools
- `schemas.py` split into `schemas.py` + `dispatch.py`
- `cli.py` split into `cli.py` + `cli_commands.py`
- New modules: `visibility.py`, `dispatch.py`, `cli_commands.py`
