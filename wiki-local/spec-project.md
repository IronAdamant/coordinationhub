# CoordinationHub — Multi-Agent Swarm Coordination MCP

**Version:** 0.3.0
**Language:** Python 3.10+ (stdlib-only core — **zero third-party dependencies**, `mcp` optional for stdio only)
**Transports:** stdio + HTTP (both, like Stele/Chisel/Trammel)

## Purpose

CoordinationHub externalizes the coordination bottleneck for multi-agent coding swarms. It tracks agent identity and lineage, enforces document locking, detects lock conflicts, propagates coordination context to spawned sub-agents, and provides a shared ground truth for "who is doing what" across all LLMs and IDEs.

Part of the **Stele + Chisel + Trammel + CoordinationHub** quartet:
- **Stele**: Persistent context retrieval and semantic indexing
- **Chisel**: Code analysis, churn, coupling, risk mapping
- **Trammel**: Planning discipline, verification, failure learning, recipe memory
- **CoordinationHub**: Multi-agent identity, lineage, locking, and conflict prevention

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
  "coordination_urls": {
    "coordinationhub": "http://localhost:9877",
    "stele": "http://localhost:9876",
    "chisel": "http://localhost:8377",
    "trammel": "http://localhost:8737"
  }
}
```

### Document Locking

Files are locked before modification, released after. Locks have:
- **TTL** (default 300s): auto-expire if agent dies
- **Owner**: only the agent that acquired it may release it
- **Force-steal**: override with conflict recording
- **Shared locks**: for reads; **exclusive locks**: for writes

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
  metrics: [role_stability, handoff_latency, outcome_verifiability, protocol_adherence]
```

### File Ownership

`scan_project(worktree_root?, extensions?)` recursively scans the worktree
and upserts every tracked file into `file_ownership`. Ownership is assigned
by nearest-ancestor directory rule, with fallback to the first-registered
active agent.

### Assessment Runner

`run_assessment(suite_path, format?)` loads a JSON trace suite, scores each
trace against 4 metric scorers, and outputs a Markdown report. Metric scorers:
- **role_stability**: events mapped to declared responsibilities in graph
- **handoff_latency**: handoff from/to pairs validated against graph
- **outcome_verifiability**: lock-write-unlock patterns per file
- **protocol_adherence**: agents act within declared responsibilities

---

## SQLite Schema (v0.3.0)

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
| `document_path` | TEXT PK | Project-relative path |
| `locked_by` | TEXT NOT NULL | Agent ID |
| `locked_at` | REAL NOT NULL | Unix timestamp |
| `lock_ttl` | REAL DEFAULT 300.0 | Seconds until expiry |
| `lock_type` | TEXT DEFAULT 'exclusive' | 'shared' or 'exclusive' |
| `worktree_root` | TEXT | Which worktree |

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

## MCP Tools (27 total — v0.3.0)

### Identity & Registration

`register_agent`, `heartbeat`, `deregister_agent`, `list_agents`, `get_lineage`, `get_siblings`

### Document Locking

`acquire_lock`, `release_lock`, `refresh_lock`, `get_lock_status`,
`release_agent_locks`, `reap_expired_locks`, `reap_stale_agents`

### Coordination Actions

`broadcast` — checks lock state only, no message forwarding
`wait_for_locks`

### Change Awareness

`notify_change`, `get_notifications`, `prune_notifications`

### Audit

`get_conflicts`, `status`

### Graph & Visibility (7 NEW in 0.3.0)

`load_coordination_spec`, `validate_graph`, `scan_project`,
`get_agent_status`, `get_file_agent_map`, `update_agent_status`, `run_assessment`

---

## Project Layout (v0.3.0)

```
coordinationhub/
  __init__.py          -- __version__, public API
  core.py              -- CoordinationEngine: all business logic (~524 LOC)
  schemas.py           -- JSON Schema for all 27 tools (~574 LOC)
  dispatch.py          -- Tool dispatch table (~48 LOC)
  graphs.py            -- Graph loader + CoordinationGraph (~310 LOC)
  visibility.py        -- File ownership scan, agent status (~233 LOC)
  assessment.py        -- Assessment runner (~397 LOC)
  mcp_server.py        -- HTTP MCP server + request handler
  mcp_stdio.py         -- stdio MCP server entry point
  cli.py               -- argparse CLI parser + lazy dispatch (~229 LOC)
  cli_commands.py      -- All 26 command handlers (~671 LOC)
  db.py                -- SQLite schema, connection pool
  agent_registry.py    -- Agent lifecycle
  lock_ops.py          -- Lock primitives
  conflict_log.py      -- Conflict recording
  notifications.py    -- Change notifications
tests/
  conftest.py
  test_agent_lifecycle.py
  test_lock_ops.py
  test_notifications.py
  test_conflicts.py
  test_coordination.py
  test_visibility.py
  test_graphs.py
  test_assessment.py
pyproject.toml
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
- Assessment runner with 4 real metric scorers
- 27 MCP tools
- `schemas.py` split into `schemas.py` + `dispatch.py`
- `cli.py` split into `cli.py` + `cli_commands.py`
- New modules: `visibility.py`, `dispatch.py`, `cli_commands.py`
