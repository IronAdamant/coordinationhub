# CoordinationHub

**Stop AI agents from overwriting each other's work.**

CoordinationHub is a lightweight MCP server that coordinates multiple AI coding agents working on the same codebase. It tracks which agent owns which files, prevents two agents from editing the same file at once, and gives you a live view of who is doing what.

Built for Claude Code, compatible with any MCP client. Zero third-party dependencies. Python stdlib only.

## The Problem

When you spawn multiple AI agents on the same project, they can silently overwrite each other's changes. There's no way to know which agent is editing which file, no protection against two agents touching the same code, and no visibility into what's happening across your swarm.

CoordinationHub fixes this by acting as a shared coordination layer — a single source of truth for file ownership, agent identity, and work status.

## What It Does

- **File locking** — Agents lock files before editing. If another agent tries to edit the same file, it gets blocked (or warned).
- **Boundary detection** — Warns when an agent crosses into another agent's assigned territory.
- **Agent tracking** — Every spawned agent gets an ID. See the full hierarchy, who's alive, who's stale.
- **Change notifications** — Agents report what they changed. Others poll to stay in sync.
- **Contention hotspots** — Identifies files that cause the most conflicts between agents.
- **Cascade cleanup** — When an agent dies, its children get re-parented and its locks get released. Nothing is orphaned.
- **Region locking** — Two agents can edit different sections of the same file simultaneously.
- **Dashboard** — Live CLI or JSON view of all agents, locks, and file assignments.

## Install

```bash
pip install coordinationhub
```

## Quick Start

```bash
# One-time setup: creates DB, configures Claude Code hooks
coordinationhub init

# Verify everything is working
coordinationhub doctor

# Start the coordination server
coordinationhub serve

# In another terminal — see what's happening
coordinationhub dashboard
coordinationhub agent-tree
coordinationhub watch              # live-refresh agent tree
coordinationhub contention-hotspots
```

### Claude Code Integration

CoordinationHub hooks into Claude Code automatically via project-level hooks. Once configured:

- Agents are registered on session start
- Files are locked before every write
- Changes are broadcast after every edit
- Subagents are tracked in the agent tree
- Everything is cleaned up on session end

See `coordinationhub/hooks/claude_code.py` and `.claude/settings.json` for the hook configuration.

### Coordination Graph (Optional)

Define your agent roles and handoff rules in a `coordination_spec.yaml` at your project root:

```yaml
agents:
  - id: planner
    role: decompose tasks
    responsibilities:
      - break down user stories
      - assign subtasks
  - id: executor
    role: implement
    responsibilities:
      - write code
      - run tests

handoffs:
  - from: planner
    to: executor
    condition: task_size < 500 && no_blockers
```

### Agent Tree View

Every agent in the swarm sees the same live hierarchy. Call `coordinationhub agent-tree` from any agent:

```
hub.cc.main [active] — "observing..."
├── hub.cc.main.0 [Agent A] — service consolidation
│   ├─ ◆ src/services/mcpProbes.js [exclusive]
│   └─ ◆ mcpChallengeRoutes.js [exclusive] ⚠ owned by hub.cc.main.1
├── hub.cc.main.1 [Agent B] — "route simplification"
│   ├─ ◆ routeLoader.js [exclusive L325-360]
│   └─ ◆ vcsRoutes.js [exclusive]
└── hub.cc.main.2 [Agent C] — data layer
    ├── hub.cc.main.2.0 [CA] — "working on fileStore.js"
    │   └─ ◆ fileStore.js [exclusive]
    └── hub.cc.main.2.1 [CB] — "working on BaseModel"
        ├─ ◆ BaseModel.js [exclusive]
        └─ ◆ baseModel.test.js [shared]
```

Each node shows: agent ID, role/task, active file locks with type and region, and boundary warnings when an agent locks a file owned by another.

---

## How It Works

```
Root Agent (project manager)
├── Agent A (team leader)
│   ├── Agent A.0 (writes code)
│   └── Agent A.1 (writes tests)
├── Agent B (team leader)
│   └── Agent B.0 (refactoring)
└── Agent C (documentation)
```

Every agent gets a unique ID. Files are locked by agent ID. The root agent (your main Claude Code session) acts as project manager — it spawns child agents and can see the full tree at any time.

Agents don't message each other directly. Instead they communicate through the shared database: lock a file, write to it, notify that it changed, release the lock. Other agents poll for notifications to see what happened.

## MCP Tools (<!-- GEN:tool-count -->31<!-- /GEN -->)

| Category | Tools |
|----------|-------|
| **Identity** | `register_agent`, `heartbeat`, `deregister_agent`, `list_agents`, `get_lineage`, `get_siblings` |
| **Locking** | `acquire_lock`, `release_lock`, `refresh_lock`, `get_lock_status`, `list_locks`, `release_agent_locks`, `reap_expired_locks`, `reap_stale_agents` |
| **Coordination** | `broadcast`, `wait_for_locks` |
| **Changes** | `notify_change`, `get_notifications`, `prune_notifications` |
| **Audit** | `get_conflicts`, `get_contention_hotspots`, `status` |
| **Visibility** | `load_coordination_spec`, `validate_graph`, `scan_project`, `get_agent_status`, `get_file_agent_map`, `update_agent_status`, `run_assessment`, `assess_current_session`, [`get_agent_tree`](#agent-tree-view) |

## CLI Commands (34)

```bash
# Setup & diagnostics
coordinationhub init                   # one-time: create DB, configure hooks
coordinationhub doctor                 # validate setup, detect venv issues

# Server
coordinationhub serve --port 9877
coordinationhub serve-mcp              # stdio mode (requires: pip install coordinationhub[mcp])

# See what's happening
coordinationhub status
coordinationhub dashboard              # full view (also: --json, --minimal)
coordinationhub agent-tree             # agent hierarchy
coordinationhub agent-status <id>      # single agent detail
coordinationhub contention-hotspots    # files with most conflicts
coordinationhub watch                  # live agent tree (Ctrl+C to stop)

# Agent lifecycle
coordinationhub register <id> [--parent-id <parent>]
coordinationhub heartbeat <id>
coordinationhub deregister <id>
coordinationhub list-agents
coordinationhub lineage <id>
coordinationhub siblings <id>

# File locking
coordinationhub acquire-lock <path> <id> [--region-start N --region-end N]
coordinationhub release-lock <path> <id>
coordinationhub refresh-lock <path> <id>
coordinationhub lock-status <path>
coordinationhub list-locks [--agent-id <id>]
coordinationhub release-agent-locks <id>
coordinationhub reap-expired-locks
coordinationhub reap-stale-agents

# Coordination & changes
coordinationhub broadcast <id> [--document-path <path>]
coordinationhub wait-for-locks <id> <paths...>
coordinationhub notify-change <path> <type> <id>
coordinationhub get-notifications
coordinationhub prune-notifications
coordinationhub get-conflicts

# Graph & assessment
coordinationhub load-spec
coordinationhub validate-spec
coordinationhub scan-project
coordinationhub assess --suite <file>          # score a hand-authored trace suite
coordinationhub assess-session                 # score the current live session (no suite file needed)
```

## Agent ID Format

```
hub.12345.0           — root agent (namespace.PID.sequence)
hub.12345.0.0         — child of root
hub.12345.0.1         — sibling
hub.12345.0.0.0       — grandchild
```

## Architecture

SQLite-backed, thread-safe, WAL mode. Each module is under 500 LOC with single responsibility. Zero internal cross-dependencies between sub-modules — they all receive a `connect` callable from the caller.

```
coordinationhub/
  core.py             — CoordinationEngine (identity, change, audit, graph/visibility)
  core_locking.py     — LockingMixin (all lock + coordination methods)
  _storage.py         — SQLite pool, path resolution, thread-safe ID generation
  db.py               — Schema, versioning, perf pragmas, ConnectionPool
  lock_ops.py         — Lock primitives + region overlap detection
  agent_registry.py   — Agent lifecycle (register, heartbeat, deregister, lineage)
  notifications.py    — Change notification storage
  conflict_log.py     — Conflict recording and querying
  scan.py             — File ownership scan
  agent_status.py     — Agent status, file map, agent tree
  graphs.py           — Coordination graph loading + validation
  assessment.py       — Assessment runner (5 metric scorers)
  mcp_server.py       — HTTP server (stdlib only)
  mcp_stdio.py        — Stdio server (optional mcp package)
  cli.py              — CLI parser + dispatch
  cli_setup.py        — doctor, init, watch commands
  hooks/claude_code.py — Claude Code session hooks
  tests/              — <!-- GEN:test-count -->341<!-- /GEN --> tests across 16 files
```

## Zero-Dependency Guarantee

Core uses **only the Python standard library**. The `mcp` package is optional (stdio transport only). Air-gapped install: `pip install coordinationhub --no-deps`.

## Port Allocation

| Server | Port |
|--------|------|
| Stele | 9876 |
| CoordinationHub | 9877 |
| Chisel | 8377 |
| Trammel | 8737 |
