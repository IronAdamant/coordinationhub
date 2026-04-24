# CoordinationHub — Multi-Agent Swarm Coordination MCP

**Version:** <!-- GEN:version -->0.7.7<!-- /GEN -->
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
| `db_schemas.py` | (no deps — pure data) |
| `db_migrations.py` | `sqlite3`, `time` |
| `agent_registry.py` | `sqlite3`, `time`, `os` |
| `lock_ops.py` | `sqlite3`, `time` |
| `conflict_log.py` | `sqlite3`, `time`, `json` |
| `notifications.py` | `sqlite3`, `time` |
| `core.py` | `sqlite3`, `pathlib`, `os`, `time`, `json`, `threading` |
| `graphs.py` | `pathlib`, `json`, `time` (optional `ruamel.yaml`) |
| `scan.py` | `pathlib`, `time`, `json` |
| `agent_status.py` | `sqlite3`, `time`, `json` |
| `assessment.py` | `pathlib`, `time`, `json`, `sqlite3` |
| `schemas/` (14 modules) | (no deps — pure data) |
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

## SQLite Schema (v0.4.0)

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

## MCP Tools (<!-- GEN:tool-count -->50<!-- /GEN --> total)

The full auto-generated table with descriptions is in `COMPLETE_PROJECT_DOCUMENTATION.md`. Several tools are meta-tools that dispatch on an `action` parameter (`manage_messages`, `manage_dependencies`, `manage_work_intents`, `manage_leases`, `admin_locks`, `query_tasks`, `task_failures`) — see `tests/test_tool_count.py` (target ≤ 50).

### Identity & Registration

`register_agent`, `heartbeat`, `deregister_agent`, `list_agents`, `get_agent_relations`

### Document Locking

`acquire_lock`, `release_lock`, `refresh_lock`, `get_lock_status`, `list_locks`, `admin_locks`

### Coordination Actions

`broadcast`, `acknowledge_broadcast`, `wait_for_broadcast_acks`, `wait_for_locks`, `await_agent`, `wait_for_handoff`

### Messaging

`send_message`, `manage_messages`

### Change Awareness

`notify_change`, `get_notifications` (engine methods `prune_notifications` and `wait_for_notifications` are CLI-only)

### Audit

`get_conflicts`, `get_contention_hotspots`, `status`

### Graph & Visibility

`load_coordination_spec`, `scan_project`, `get_agent_status`, `get_file_agent_map`, `update_agent_status`, `run_assessment`, `get_agent_tree` (the legacy `validate_graph` remains a CLI/engine method)

### Tasks, Dependencies, Work Intent

`create_task`, `create_subtask`, `assign_task`, `update_task_status`, `query_tasks`, `wait_for_task`, `get_available_tasks`, `task_failures`, `manage_dependencies`, `manage_work_intents`

### HA Leases & Spawner

`acquire_coordinator_lease`, `manage_leases`, `spawn_subagent`, `report_subagent_spawned`, `get_pending_spawns`, `request_subagent_deregistration`, `await_subagent_registration`, `await_subagent_stopped`, `is_subagent_stop_requested`

---

## Project Layout

<!-- GEN:directory-tree -->
```
coordinationhub/
  __init__.py           — CoordinationHub — multi-agent swarm coordination MCP server (~14 LOC)
  __main__.py           — ``python -m coordinationhub`` entry point — delegates to :mod:`cli` (~10 LOC)
  _storage.py           — Storage backend for CoordinationHub — SQLite pool, path resolution, lifecycle (~143 LOC)
  agent_registry.py     — Agent lifecycle: register, heartbeat, deregister, lineage management (~495 LOC)
  agent_status.py       — Agent status and file-map query helpers for CoordinationHub (~288 LOC)
  broadcast_subsystem.py — Broadcast subsystem — sibling broadcasts, ack tracking, multi-lock wait (~246 LOC)
  broadcasts.py         — Broadcast acknowledgment primitives for CoordinationHub (~147 LOC)
  change_subsystem.py   — Change subsystem — change notifications, file ownership, conflict audit, status (~219 LOC)
  cli.py                — CoordinationHub CLI — command-line interface for all coordination tool methods (~102 LOC)
  cli_agents.py         — Agent identity and lifecycle CLI commands (~111 LOC)
  cli_commands.py       — CoordinationHub CLI command handlers (~98 LOC)
  cli_deps.py           — CLI commands for cross-agent dependency declarations (~64 LOC)
  cli_intent.py         — CLI commands for the work intent board (~35 LOC)
  cli_leases.py         — CLI commands for HA coordinator lease management (~96 LOC)
  cli_locks.py          — Document locking and coordination CLI commands (~272 LOC)
  cli_parser.py         — Argument parser for the CoordinationHub CLI (~379 LOC)
  cli_setup.py          — CLI commands for setup and diagnostics: ``init``, ``doctor``, ``watch`` (~313 LOC)
  cli_setup_doctor.py   — Diagnostic checks for ``coordinationhub doctor`` (~148 LOC)
  cli_spawner.py        — CLI commands for HA coordinator spawner — sub-agent registry management (~105 LOC)
  cli_sse.py            — CLI commands for SSE dashboard server (~56 LOC)
  cli_tasks.py          — CLI commands for the task registry (~163 LOC)
  cli_utils.py          — Shared CLI helper functions used by all cli_* sub-modules (~51 LOC)
  cli_vis.py            — Change awareness, audit, graph, and assessment CLI commands (~241 LOC)
  conflict_log.py       — Conflict recording and querying for CoordinationHub (~43 LOC)
  context.py            — Context bundle builder for CoordinationHub agent registration responses (~104 LOC)
  core.py               — CoordinationEngine — host class that composes twelve subsystems (~948 LOC)
  db.py                 — SQLite connection pool and public re-exports for CoordinationHub (~93 LOC)
  db_migrations.py      — Schema-version tracking, migration functions, and the ``init_schema`` driver (~399 LOC)
  db_schemas.py         — Canonical SQLite schema definitions for CoordinationHub (~301 LOC)
  dependencies.py       — Cross-agent dependency declaration and satisfaction tracking (~140 LOC)
  dependency_subsystem.py — Dependency subsystem — cross-agent dependency declarations and checks (~143 LOC)
  dispatch.py           — Tool dispatch for CoordinationHub (~107 LOC)
  event_bus.py          — Lightweight thread-safe in-memory pub-sub event bus for CoordinationHub (~127 LOC)
  handoff_subsystem.py  — Handoff subsystem — one-to-many handoff acknowledgment and lifecycle (~148 LOC)
  handoffs.py           — Handoff recording and acknowledgement primitives for CoordinationHub (~200 LOC)
  housekeeping.py       — HousekeepingScheduler — background periodic pruners for long-running hubs (~188 LOC)
  identity_subsystem.py — Identity subsystem — agent registration, heartbeat, lineage, and ID generation (~203 LOC)
  lease_subsystem.py    — Lease subsystem — HA coordinator lease management (~167 LOC)
  leases.py             — Zero-deps lease primitives for HA coordinator leadership (~220 LOC)
  limits.py             — String-length caps for user-supplied fields (T6.14) (~40 LOC)
  lock_cache.py         — In-memory lock cache for CoordinationHub (~180 LOC)
  lock_ops.py           — Shared lock primitives used by both local locks and coordination locks (~209 LOC)
  locking_subsystem.py  — Locking subsystem — document lock acquire/release/refresh/list/admin (~445 LOC)
  mcp_server.py         — HTTP REST admin / dashboard endpoint for CoordinationHub (~578 LOC)
  mcp_stdio.py          — Stdio-based MCP server for CoordinationHub using the ``mcp`` Python package (~197 LOC)
  messages.py           — Inter-agent messaging primitives for CoordinationHub (~105 LOC)
  messaging_subsystem.py — Messaging subsystem — inter-agent message passing and agent await (~186 LOC)
  notifications.py      — Change notification storage and retrieval for CoordinationHub (~154 LOC)
  paths.py              — Path normalization and project-root detection utilities (~39 LOC)
  pending_tasks.py      — Pending sub-agent task storage for CoordinationHub (~109 LOC)
  scan.py               — File ownership scan for CoordinationHub (~279 LOC)
  spawner.py            — Zero-deps spawner primitives for HA coordinator sub-agent registry (~395 LOC)
  spawner_subsystem.py  — Spawner subsystem — HA coordinator sub-agent spawn management (~233 LOC)
  task_failures.py      — Task failure tracking and dead letter queue for CoordinationHub (~149 LOC)
  task_subsystem.py     — Task subsystem — shared task registry with hierarchy + dead-letter queue (~241 LOC)
  tasks.py              — Task registry primitives for CoordinationHub (work board) (~517 LOC)
  validation.py         — Minimal stdlib jsonschema validator for MCP tool arguments (~160 LOC)
  visibility_subsystem.py — Visibility subsystem — coordination graph, project scan, agent status, assessment (~175 LOC)
  work_intent.py        — Work intent board primitives for CoordinationHub (~139 LOC)
  work_intent_subsystem.py — WorkIntent subsystem — cooperative work intent board (~92 LOC)
  hooks/
    __init__.py         — Hooks package — IDE integration via stdin/stdout event protocol (~1 LOC)
    base.py             — Base hook abstraction for CoordinationHub (~340 LOC)
    cursor.py           — CoordinationHub hook adapter for Cursor (~146 LOC)
    kimi_cli.py         — CoordinationHub hook adapter for Kimi CLI (~121 LOC)
    stdio_adapter.py    — CoordinationHub stdio event adapter (~353 LOC)
  plugins/
    __init__.py         — CoordinationHub plugin system (~8 LOC)
    registry.py         — Plugin registry for CoordinationHub (~59 LOC)
  plugins/assessment/
    __init__.py         — Assessment plugin for CoordinationHub (~28 LOC)
    assessment.py       — Assessment runner for CoordinationHub coordination test suites (~337 LOC)
    assessment_scorers.py — Assessment metric scorers for CoordinationHub (~266 LOC)
  plugins/dashboard/
    __init__.py         — Dashboard plugin for CoordinationHub (~15 LOC)
    dashboard.py        — Web dashboard for CoordinationHub — zero external dependencies (~141 LOC)
    dashboard_css.py    — CSS for the CoordinationHub dashboard (~91 LOC)
    dashboard_html.py   — Self-contained HTML for the CoordinationHub dashboard (~98 LOC)
    dashboard_js.py     — Client-side JavaScript for the CoordinationHub dashboard (~452 LOC)
  plugins/graph/
    __init__.py         — Graph plugin for CoordinationHub (~31 LOC)
    graphs.py           — Declarative coordination graph: loader, validator, in-memory representation (~327 LOC)
  schemas/
    __init__.py         — Tool schemas for CoordinationHub — all MCP tools (~58 LOC)
    audit.py            — Audit & Status tool schemas for CoordinationHub (~61 LOC)
    change.py           — Change Awareness tool schemas for CoordinationHub (~41 LOC)
    coordination.py     — Coordination Actions tool schemas for CoordinationHub (~145 LOC)
    deps.py             — Cross-Agent Dependencies tool schemas for CoordinationHub (~29 LOC)
    dlq.py              — Dead Letter Queue tool schemas for CoordinationHub (~23 LOC)
    handoffs.py         — Handoffs tool schemas for CoordinationHub (~23 LOC)
    identity.py         — Identity & Registration tool schemas for CoordinationHub (~112 LOC)
    intent.py           — Work Intent Board tool schemas for CoordinationHub (~58 LOC)
    leases.py           — HA Coordinator Leases tool schemas for CoordinationHub (~35 LOC)
    locking.py          — Document Locking tool schemas for CoordinationHub (~202 LOC)
    messaging.py        — Messaging tool schemas for CoordinationHub (~95 LOC)
    spawner.py          — Spawner tool schemas for CoordinationHub (~201 LOC)
    tasks.py            — Task Registry tool schemas for CoordinationHub (~230 LOC)
    visibility.py       — Graph & Visibility tool schemas for CoordinationHub (~159 LOC)
```
<!-- /GEN -->

`tests/` contains <!-- GEN:test-count -->738<!-- /GEN --> tests across 28 files plus `fixtures/claude_code_events/` (hook contract fixtures).

Top-level project files: `pyproject.toml`, `coordination_spec.yaml`/`.json` (example specs), `README.md`, `CLAUDE.md`, `COMPLETE_PROJECT_DOCUMENTATION.md`, `LLM_Development.md`, and `wiki-local/` (this spec, glossary, index).

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

### 0.3.5 — Ownership-aware locking & contention hotspots (2026-04-10)
- `acquire_lock` cross-checks `file_ownership`: warns when agent locks file owned by another, records `boundary_crossing` conflict + notification
- `get_contention_hotspots` tool: ranks files by conflict count, identifies coordination chokepoints
- `contention-hotspots` CLI command
- Rich `agent-tree`: each node shows current task, active locks (type + region), boundary warnings
- 30 MCP tools, 31 CLI commands, 256 tests

### Review Eleven — Multi-agent coordination validation (2026-04-10)
- 3-agent parallel refactor validated CoordinationHub's design without code changes
- Prompt-based boundaries proved fragile (agent crossed file ownership boundary undetected)
- Region locking, `wait_for_locks`, and `notify_change` confirmed as solutions to observed coordination problems
- No overwrites occurred due to careful pre-partitioning — CoordinationHub automates this safety
- Remaining: real integration test with CoordinationHub MCP server in a multi-agent workflow

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
