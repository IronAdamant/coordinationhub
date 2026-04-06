# CoordinationHub — Multi-Agent Swarm Coordination MCP

**Version:** 0.1.0  
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
| `mcp_server.py` | `http.server`, `socketserver`, `json`, `threading`, `subprocess` |
| `cli.py` | `argparse`, `pathlib` |

**No third-party packages in core.** No `requests`, no `httpx`, no `aiohttp`, no external HTTP libraries. The HTTP server is built entirely on `http.server` + `socketserver.ThreadingMixIn`.

The `mcp` package (from the official MCP SDK) is **optional** — only needed for the stdio transport shim (`mcp_stdio.py`). The HTTP transport works without it.

**Air-gapped install:** `pip install -e . --no-deps` installs everything needed for HTTP transport. Stdin/stdout transport requires `pip install -e '.[mcp]'` only if stdio is needed.

All four MCPs in this suite are designed so a complete air-gapped install works with just `pip install -e .`.

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

The hierarchy is flat at the storage level (sequence numbers only) but the ID encodes lineage via dot-separated segments.

### Agent Lineage

When agent A spawns agent B:
1. A is the **parent**, B is the **child**
2. B receives a sequence number under A's namespace branch
3. The lineage is recorded in the DB as `(parent_id, child_id, spawned_at)`
4. B's ID encodes the full path: `hub.PID.parent_seq.child_seq`

This allows any agent to:
- Derive its **lineage** from its own ID
- Find all **descendants** by prefix-matching
- Find all **siblings** (same parent) via lineage lookup
- Find the **parent** by stripping the last segment

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

This bundle is the **single source of truth** an agent uses to understand its environment. Passed explicitly to spawned sub-agents.

### Document Locking

Files are locked before modification, released after. Locks have:
- **TTL** (default 300s): auto-expire if agent dies
- **Owner**: only the agent that acquired it may release it
- **Force-steal**: override with conflict recording
- **Shared locks**: for reads; **exclusive locks**: for writes

### Conflict Detection

When lock acquisition fails because another agent holds the lock:
- Return `{acquired: false, locked_by: "...", locked_at: ..., expires_at: ...}`
- Agent chooses: wait, retry, or abort
- The **coordinator** (parent agent) mediates if needed

### Heartbeat Protocol

Agents send heartbeats every 30s. Missing 2+ heartbeats = stale:
- Stale agents' locks are released
- Stale agents' children become orphaned (inherit grandparent lineage)

---

## SQLite Schema

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
| `parent_id` | TEXT PK | Parent agent ID |
| `child_id` | TEXT PK | Child agent ID |
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

#### `coordination_context`

| Column | Type | Description |
|--------|------|-------------|
| `key` | TEXT PK | Context key |
| `value` | TEXT | JSON-encoded value |
| `updated_at` | REAL NOT NULL | Unix timestamp |

---

## MCP Tools (17)

### Identity & Registration

#### `register_agent`

Register this agent and receive coordination context bundle.

**Arguments:**
```json
{
  "agent_id": "hub.12345.1.0",
  "parent_id": "hub.12345.1",
  "worktree_root": "/absolute/path/to/project"
}
```

**Response:**
```json
{
  "agent_id": "hub.12345.1.0",
  "parent_id": "hub.12345.1",
  "worktree_root": "/absolute/path/to/project",
  "registered_agents": [
    {"agent_id": "hub.12345.0", "status": "active", "last_heartbeat": 1234567890.123}
  ],
  "active_locks": [
    {"document_path": "src/auth.py", "locked_by": "hub.12345.0", "expires_at": 1234568190.123}
  ],
  "pending_notifications": [],
  "coordination_urls": {
    "coordinationhub": "http://localhost:9877",
    "stele": "http://localhost:9876",
    "chisel": "http://localhost:8377",
    "trammel": "http://localhost:8737"
  }
}
```

**Behavior:**
- If `agent_id` already registered and heartbeat is fresh: return context (no-op)
- If `agent_id` already registered but stale: update heartbeat, return context
- If new: insert, set parent-child lineage, return full context

#### `heartbeat`

Update last-seen timestamp and reap stale locks if eligible.

**Arguments:** `{}` (agent_id inferred from context)

**Response:**
```json
{
  "updated": true,
  "stale_released": 2,
  "next_heartbeat_in": 30
}
```

#### `deregister_agent`

Mark agent as stopped and release all its locks.

**Arguments:** `{}`

**Response:**
```json
{
  "deregistered": true,
  "locks_released": 3,
  "children_orphaned": 2
}
```

**Behavior:**
- Children are **orphaned** — their `parent_id` is set to their **grandparent** (or NULL if root)
- This prevents cascade failures from killing an entire subtree

#### `list_agents`

List all registered agents.

**Arguments:**
```json
{
  "active_only": true,
  "stale_timeout": 600.0
}
```

**Response:**
```json
{
  "agents": [
    {
      "agent_id": "hub.12345.0",
      "parent_id": null,
      "worktree_root": "/home/aron/project",
      "status": "active",
      "last_heartbeat": 1234567890.123,
      "stale": false
    }
  ]
}
```

#### `get_lineage`

Get agent's full ancestry and descendants.

**Arguments:**
```json
{
  "agent_id": "hub.12345.1.0"
}
```

**Response:**
```json
{
  "ancestors": [
    {"agent_id": "hub.12345.0", "parent_id": null},
    {"agent_id": "hub.12345.1", "parent_id": "hub.12345.0"}
  ],
  "descendants": [
    {"agent_id": "hub.12345.1.0", "parent_id": "hub.12345.1"}
  ]
}
```

#### `get_siblings`

Get agents with the same parent.

**Arguments:** `{}`

**Response:**
```json
{
  "siblings": [
    {"agent_id": "hub.12345.1.1", "status": "active", "last_heartbeat": 1234567890.123}
  ]
}
```

---

### Document Locking

#### `acquire_lock`

Acquire a lock on a document path.

**Arguments:**
```json
{
  "document_path": "src/auth.py",
  "lock_type": "exclusive",
  "ttl": 300.0,
  "force": false
}
```

**Response (success):**
```json
{
  "acquired": true,
  "document_path": "src/auth.py",
  "locked_by": "hub.12345.1.0",
  "expires_at": 1234568190.123
}
```

**Response (failure — contested):**
```json
{
  "acquired": false,
  "locked_by": "hub.12345.1.1",
  "locked_at": 1234567890.123,
  "expires_at": 1234568190.123,
  "worktree": "/home/aron/project"
}
```

**Behavior:**
- Shared locks (`lock_type: "shared"`): allow concurrent shared locks, block exclusive
- Exclusive locks: block all other locks
- `force: true`: steal lock, record conflict in `lock_conflicts`
- Paths are **project-relative** (normalized internally)

#### `release_lock`

Release a held lock.

**Arguments:**
```json
{
  "document_path": "src/auth.py"
}
```

**Response:**
```json
{
  "released": true
}
```

**Behavior:**
- Only the lock owner can release
- Returns `{released: false, reason: "not_locked"}` if not locked
- Returns `{released: false, reason: "not_owner"}` if wrong agent

#### `refresh_lock`

Extend a lock's TTL without releasing it.

**Arguments:**
```json
{
  "document_path": "src/auth.py",
  "ttl": 300.0
}
```

**Response:**
```json
{
  "refreshed": true,
  "expires_at": 1234568490.123
}
```

#### `get_lock_status`

Check if a document is currently locked.

**Arguments:**
```json
{
  "document_path": "src/auth.py"
}
```

**Response:**
```json
{
  "locked": true,
  "locked_by": "hub.12345.1.0",
  "locked_at": 1234567890.123,
  "expires_at": 1234568190.123,
  "worktree": "/home/aron/project"
}
```

#### `release_agent_locks`

Release all locks held by a given agent (for cleanup after agent dies).

**Arguments:**
```json
{
  "agent_id": "hub.12345.1.0"
}
```

**Response:**
```json
{
  "released": 5
}
```

---

### Coordination Actions

#### `broadcast`

Announce an intention to siblings before taking an action.

**Arguments:**
```json
{
  "message": "about_to_modify",
  "document_path": "src/auth.py",
  "action": "refactor",
  "ttl": 30
}
```

**Response:**
```json
{
  "acknowledged_by": ["hub.12345.1.1"],
  "conflicts": []
}
```

**Behavior:**
- Sends to all siblings (same parent)
- Agents that are busy doing other work may not ack
- Returns list of agents that acknowledged and any lock conflicts detected

#### `wait_for_locks`

Block until specified locks are released or timeout.

**Arguments:**
```json
{
  "document_paths": ["src/auth.py", "src/config.py"],
  "timeout_s": 60
}
```

**Response:**
```json
{
  "released": ["src/auth.py"],
  "timed_out": ["src/config.py"]
}
```

#### `reap_expired_locks`

Clear all expired locks (called automatically by heartbeat, can be called manually).

**Arguments:** `{}`

**Response:**
```json
{
  "reaped": 2
}
```

#### `reap_stale_agents`

Mark stale agents as stopped and release their locks.

**Arguments:**
```json
{
  "timeout": 600.0
}
```

**Response:**
```json
{
  "reaped": 3,
  "orphaned_children": 5
}
```

**Behavior:**
- Reaps agents with no heartbeat for `timeout` seconds
- For each reaped agent, orphans its children (grandparent becomes parent)
- Cascade: recursively reap orphaned children's children if they have no heartbeat

---

### Change Awareness

#### `notify_change`

Record a change event for other agents to poll.

**Arguments:**
```json
{
  "document_path": "src/auth.py",
  "change_type": "modified",
  "agent_id": "hub.12345.1.0"
}
```

**Response:** `{"recorded": true}`

#### `get_notifications`

Poll for changes since a timestamp.

**Arguments:**
```json
{
  "since": 1234567890.123,
  "exclude_agent": "hub.12345.1.0",
  "limit": 100
}
```

**Response:**
```json
{
  "notifications": [
    {
      "document_path": "src/auth.py",
      "change_type": "modified",
      "agent_id": "hub.12345.1.1",
      "created_at": 1234567891.234
    }
  ]
}
```

#### `prune_notifications`

Clean up old notifications.

**Arguments:**
```json
{
  "max_age_seconds": 3600,
  "max_entries": 1000
}
```

**Response:**
```json
{
  "pruned": 42
}
```

---

### Conflict Audit

#### `get_conflicts`

Query the conflict log.

**Arguments:**
```json
{
  "document_path": "src/auth.py",
  "agent_id": null,
  "limit": 20
}
```

**Response:**
```json
{
  "conflicts": [
    {
      "document_path": "src/auth.py",
      "agent_a": "hub.12345.1.0",
      "agent_b": "hub.12345.1.1",
      "conflict_type": "lock_stolen",
      "resolution": "force_overwritten",
      "created_at": 1234567891.234
    }
  ]
}
```

---

### Status

#### `status`

Get a summary of the coordination system state.

**Arguments:** `{}`

**Response:**
```json
{
  "registered_agents": 5,
  "active_agents": 4,
  "active_locks": 3,
  "pending_notifications": 12,
  "recent_conflicts": 1,
  "tools": 17
}
```

---

## Transport Layer

Two transports, like Stele/Chisel/Trammel:

### stdio (`mcp_stdio.py`)

Entry point: `coordinationhub-mcp`
```bash
coordinationhub-mcp  # starts stdio MCP server
```

### HTTP (`mcp_server.py`)

Entry point: `coordinationhub serve --port 9877`
```bash
coordinationhub serve --port 9877  # starts HTTP MCP server
```

Default port: `9877`

---

## Project Layout

```
coordinationhub/
  __init__.py          -- __version__, public API
  core.py              -- CoordinationEngine: all business logic
  db.py                -- SQLite schema, migrations, connection pool
  agent_registry.py    -- Agent lifecycle: register, heartbeat, deregister, lineage
  lock_ops.py          -- Lock primitives: acquire, release, refresh, reap
  conflict_log.py     -- Conflict recording and querying
  notifications.py    -- Change notification storage and retrieval
  transport_agnostic.py -- Shared dispatch and schema (used by both transports)
  mcp_server.py        -- HTTP MCP server + request handler
  mcp_stdio.py         -- stdio MCP server entry point
  cli.py               -- argparse CLI entry point
tests/
  test_agent_registry.py
  test_lock_ops.py
  test_notifications.py
  test_coordination.py
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

## Concurrency Model

- **Single SQLite DB**: `<worktree_root>/.coordinationhub/coordination.db`
- **WAL mode + busy timeout**: 30s, exponential backoff retry
- **Thread-local connections**: connection pool (max 1 per thread, reused)
- **Process-level file lock**: `fcntl.flock` on `.lock` sidecar (Unix; Windows: `msvcrt`)
- **No cross-process shared memory**: all coordination via SQLite + file locks

---

## Path Normalization

- All `document_path` values are **project-relative**
- Project root is determined by `_detect_project_root()` (same logic as Stele/Chisel — walks up from CWD looking for `.git`)
- Paths outside the project root are stored as absolute paths
- Path separator normalization: `\` → `/` on all platforms

---

## Port Allocation

| Server | Default Port |
|--------|-------------|
| CoordinationHub | 9877 |
| Stele | 9876 |
| Chisel | 8377 |
| Trammel | 8737 |

---

## Integration with Stele/Chisel/Trammel

When all four MCP servers are running, a coordinator agent can:

1. Call `coordinationhub.register_agent` → receives context bundle
2. Call `coordinationhub.acquire_lock("src/auth.py")` → locks before writing
3. Call `stele.index_documents(["src/auth.py"])` → re-index after changes
4. Call `coordinationhub.notify_change("src/auth.py", "modified")` → notify peers
5. Call `coordinationhub.release_lock("src/auth.py")` → release lock

Siblings can poll `coordinationhub.get_notifications(since=...)` to see what changed.

---

## LLM Orchestration Guide

### Spawning Contract

When an LLM spawns a sub-agent, the sub-agent **must** receive this coordination context in its system prompt:

```
You are part of a multi-agent swarm coordinated via CoordinationHub.
Your agent ID: {agent_id}
Coordination server: http://localhost:9877
Your parent: {parent_id}
Your siblings: {siblings}

RULES:
1. On startup: call register_agent() with your agent_id and parent_id
2. Before writing any file: call acquire_lock(document_path="path/to/file")
3. After writing: call release_lock(document_path="path/to/file")
4. Every 30s: call heartbeat()
5. Before major actions: call broadcast(message="about_to_modify", document_path=...)
6. On completion: call deregister_agent()

If you spawn a sub-agent, pass this full coordination context bundle in its system prompt.
```

### Multi-Agent Workflow Example

```
1. Coordinator (root agent) receives task: "refactor auth module"
2. Coordinator calls decompose(trammel) → gets strategy steps
3. Coordinator calls coordinationhub.register_agent() → gets agent_id "hub.12345.0"
4. For parallel branch A (src/auth.py): 
   - Coordinator spawns sub-agent with agent_id="hub.12345.1", parent_id="hub.12345.0"
   - Sub-agent calls register_agent(), acquires locks, does work, releases, deregisters
5. For parallel branch B (src/users.py):
   - Coordinator spawns sub-agent with agent_id="hub.12345.2", parent_id="hub.12345.0"
   - Sub-agent calls register_agent(), acquires locks, does work, releases, deregisters
6. Coordinator calls trammel.complete_plan() → saves recipe
```

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
