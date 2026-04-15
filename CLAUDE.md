# CLAUDE.md — CoordinationHub

**Audience:** Autonomous coding agents (MCP, CLI invoked by agents), not a standalone dashboard.

## Project Overview

CoordinationHub externalizes the coordination bottleneck for multi-agent coding swarms. It tracks agent identity and lineage, enforces document locking, detects lock conflicts, propagates coordination context to spawned sub-agents, and provides a shared ground truth for "who is doing what" across all LLMs and IDEs.

Zero third-party dependencies in core. Works standalone or alongside Stele, Chisel, and Trammel.

## Architecture

<!-- GEN:directory-tree -->
```
coordinationhub/
  __init__.py           — CoordinationHub — multi-agent swarm coordination MCP server (~14 LOC)
  _storage.py           — Storage backend for CoordinationHub — SQLite pool, path resolution, lifecycle (~113 LOC)
  agent_registry.py     — Agent lifecycle: register, heartbeat, deregister, lineage management (~292 LOC)
  agent_status.py       — Agent status and file-map query helpers for CoordinationHub (~277 LOC)
  broadcasts.py         — Broadcast acknowledgment primitives for CoordinationHub (~106 LOC)
  cli.py                — CoordinationHub CLI — command-line interface for all coordination tool methods (~398 LOC)
  cli_agents.py         — Agent identity and lifecycle CLI commands (~121 LOC)
  cli_commands.py       — CoordinationHub CLI command handlers (~97 LOC)
  cli_deps.py           — CLI commands for cross-agent dependency declarations (~77 LOC)
  cli_intent.py         — CLI commands for the work intent board (~45 LOC)
  cli_leases.py         — CLI commands for HA coordinator lease management (~150 LOC)
  cli_locks.py          — Document locking and coordination CLI commands (~323 LOC)
  cli_setup.py          — CLI commands for setup and diagnostics: doctor, init, watch (~287 LOC)
  cli_spawner.py        — CLI commands for HA coordinator spawner — sub-agent registry management (~115 LOC)
  cli_sse.py            — CLI commands for SSE dashboard server (~29 LOC)
  cli_tasks.py          — CLI commands for the task registry (~239 LOC)
  cli_utils.py          — Shared CLI helper functions used by all cli_* sub-modules (~31 LOC)
  cli_vis.py            — Change awareness, audit, graph, and assessment CLI commands (~292 LOC)
  conflict_log.py       — Conflict recording and querying for CoordinationHub (~44 LOC)
  context.py            — Context bundle builder for CoordinationHub agent registration responses (~93 LOC)
  core.py               — CoordinationEngine — thin host class that inherits all mixins (~162 LOC)
  core_change.py        — ChangeMixin — change notifications, file ownership, conflict audit, status (~182 LOC)
  core_dependencies.py  — DependencyMixin — cross-agent dependency declarations and checks (~120 LOC)
  core_handoffs.py      — HandoffMixin — one-to-many handoff acknowledgment and lifecycle (~117 LOC)
  core_identity.py      — IdentityMixin — agent lifecycle and lineage management (~95 LOC)
  core_leases.py        — LeaseMixin — HA coordinator lease management (~146 LOC)
  core_locking.py       — Locking and coordination methods for CoordinationEngine (~496 LOC)
  core_messaging.py     — MessagingMixin — inter-agent messages and await (~121 LOC)
  core_spawner.py       — SpawnerMixin — HA coordinator sub-agent spawn management (~193 LOC)
  core_tasks.py         — TaskMixin — shared task registry with hierarchy support (~193 LOC)
  core_visibility.py    — VisibilityMixin — coordination graph, project scan, agent status, assessment (~127 LOC)
  core_work_intent.py   — WorkIntentMixin — cooperative work intent board (~45 LOC)
  db.py                 — SQLite connection pool and public re-exports for CoordinationHub (~93 LOC)
  db_migrations.py      — Schema-version tracking, migration functions, and the ``init_schema`` driver (~222 LOC)
  db_schemas.py         — Canonical SQLite schema definitions for CoordinationHub (~287 LOC)
  dependencies.py       — Cross-agent dependency declaration and satisfaction tracking (~140 LOC)
  dispatch.py           — Tool dispatch table for CoordinationHub (~57 LOC)
  event_bus.py          — Lightweight thread-safe in-memory pub-sub event bus for CoordinationHub (~73 LOC)
  handoffs.py           — Handoff recording and acknowledgement primitives for CoordinationHub (~96 LOC)
  leases.py             — Zero-deps lease primitives for HA coordinator leadership (~197 LOC)
  lock_cache.py         — In-memory lock cache for CoordinationHub (~180 LOC)
  lock_ops.py           — Shared lock primitives used by both local locks and coordination locks (~191 LOC)
  mcp_server.py         — HTTP-based MCP server for CoordinationHub — zero external dependencies (~252 LOC)
  mcp_stdio.py          — Stdio-based MCP server for CoordinationHub using the ``mcp`` Python package (~142 LOC)
  messages.py           — Inter-agent messaging primitives for CoordinationHub (~90 LOC)
  notifications.py      — Change notification storage and retrieval for CoordinationHub (~136 LOC)
  paths.py              — Path normalization and project-root detection utilities (~38 LOC)
  pending_tasks.py      — Pending sub-agent task storage for CoordinationHub (~106 LOC)
  scan.py               — File ownership scan for CoordinationHub (~198 LOC)
  spawner.py            — Zero-deps spawner primitives for HA coordinator sub-agent registry (~318 LOC)
  task_failures.py      — Task failure tracking and dead letter queue for CoordinationHub (~95 LOC)
  tasks.py              — Task registry primitives for CoordinationHub (work board) (~289 LOC)
  work_intent.py        — Work intent board primitives for CoordinationHub (~77 LOC)
  hooks/
    __init__.py         — Hooks package — Claude Code integration via stdin/stdout event protocol (~1 LOC)
    base.py             — Base hook abstraction for CoordinationHub (~238 LOC)
    claude_code.py      — CoordinationHub hook for Claude Code (~270 LOC)
    cursor.py           — CoordinationHub hook adapter for Cursor (~99 LOC)
    kimi_cli.py         — CoordinationHub hook adapter for Kimi CLI (~100 LOC)
  plugins/
    __init__.py         — CoordinationHub plugin system (~8 LOC)
    registry.py         — Plugin registry for CoordinationHub (~41 LOC)
  plugins/assessment/
    __init__.py         — Assessment plugin for CoordinationHub (~27 LOC)
    assessment.py       — Assessment runner for CoordinationHub coordination test suites (~322 LOC)
    assessment_scorers.py — Assessment metric scorers for CoordinationHub (~258 LOC)
  plugins/dashboard/
    __init__.py         — Dashboard plugin for CoordinationHub (~15 LOC)
    dashboard.py        — Web dashboard for CoordinationHub — zero external dependencies (~481 LOC)
  plugins/graph/
    __init__.py         — Graph plugin for CoordinationHub (~31 LOC)
    graphs.py           — Declarative coordination graph: loader, validator, in-memory representation (~309 LOC)
  schemas/
    __init__.py         — Tool schemas for CoordinationHub — all MCP tools (~56 LOC)
    audit.py            — Audit & Status tool schemas for CoordinationHub (~61 LOC)
    change.py           — Change Awareness tool schemas for CoordinationHub (~41 LOC)
    coordination.py     — Coordination Actions tool schemas for CoordinationHub (~145 LOC)
    deps.py             — Cross-Agent Dependencies tool schemas for CoordinationHub (~29 LOC)
    dlq.py              — Dead Letter Queue tool schemas for CoordinationHub (~23 LOC)
    handoffs.py         — Handoffs tool schemas for CoordinationHub (~23 LOC)
    identity.py         — Identity & Registration tool schemas for CoordinationHub (~112 LOC)
    intent.py           — Work Intent Board tool schemas for CoordinationHub (~20 LOC)
    leases.py           — HA Coordinator Leases tool schemas for CoordinationHub (~35 LOC)
    locking.py          — Document Locking tool schemas for CoordinationHub (~193 LOC)
    messaging.py        — Messaging tool schemas for CoordinationHub (~41 LOC)
    spawner.py          — Spawner tool schemas for CoordinationHub (~193 LOC)
    tasks.py            — Task Registry tool schemas for CoordinationHub (~220 LOC)
    visibility.py       — Graph & Visibility tool schemas for CoordinationHub (~159 LOC)
```
<!-- /GEN -->

The `tests/` directory contains the pytest suite (<!-- GEN:test-count -->393<!-- /GEN --> tests across 23 files), including `tests/fixtures/claude_code_events/` contract fixtures.

## Module Design

- **Zero internal deps in sub-modules**: `agent_registry.py`, `lock_ops.py`, `conflict_log.py`, `notifications.py`, `scan.py`, `agent_status.py` all receive `connect: ConnectFn` from the caller. They have no internal imports from each other.
- **LockingMixin in `core_locking.py`**: All locking and coordination methods (`acquire_lock`, `release_lock`, `refresh_lock`, `get_lock_status`, `list_locks`, `release_agent_locks`, `reap_expired_locks`, `reap_stale_agents`, `broadcast`, `wait_for_locks`) live in `LockingMixin`. `CoordinationEngine` inherits from `LockingMixin`, keeping `core.py` focused on identity, change awareness, audit, and graph/visibility.
- **Storage layer isolated in `_storage.py`**: `CoordinationStorage` owns the SQLite pool, path resolution, and schema init. Both `core.py` and CLI entry points depend on it.
- **Canonical schemas in `db_schemas.py` only**: All table definitions and indexes live in `db_schemas._SCHEMAS` and `db_schemas._INDEXES` (re-exported from `db` for back-compat). Migration functions and the `init_schema()` driver live in `db_migrations.py`. Sub-modules do not define their own schemas — `init_schema()` creates everything.
- **Thread-local connection pool**: `db.py` provides a `ConnectionPool` that gives each thread its own reused SQLite connection. WAL mode enabled, 30s busy timeout.
- **Thread-safe ID generation**: `_storage.py` uses `threading.Lock` + in-memory sequence counters to guarantee unique agent IDs even under concurrent `generate_agent_id()` calls.
- **CLI helpers consolidated**: `cli_utils.py` provides `print_json`, `engine_from_args`, and `close` shared by all `cli_*.py` modules.
- **Dispatch separation**: the `schemas/` package (all <!-- GEN:tool-count -->50<!-- /GEN --> tool schemas as pure data, one module per functional group) and `dispatch.py` (dispatch table) are separate modules shared by both HTTP and stdio servers.
- **Project root detection**: `detect_project_root()` in `paths.py` walks up from CWD looking for `.git`. Used by `CoordinationEngine.__init__`.

## Key Design Decisions

- **Agent ID format**: `{namespace}.{PID}.{sequence}` for root agents, `{parent_id}.{sequence}` for children. PID encoded to distinguish agents from different processes. Sequence numbers derived via `_next_seq_atomic()` with in-memory counters seeded from DB, serialized by `_seq_lock`.
- **Concurrent lock safety**: `acquire_lock` uses `BEGIN IMMEDIATE` to serialize concurrent lock attempts. Two threads racing for the same file are sequenced at the transaction level rather than catching `IntegrityError` after the fact.
- **TTL-based locks**: All locks expire unless refreshed. Default 300s. `heartbeat()` does NOT reap expired locks — call `reap_expired_locks()` explicitly.
- **Assessment keyword matching**: Shared `event_matches_responsibility()` in `plugins/assessment/assessment_scorers.py` maps event types to responsibility keywords via `_EVENT_RESPONSIBILITY_MAP` dict. Extensible — add new event-type groups to the map to support custom vocabularies. Non-standard terms that don't contain any mapped keyword will reduce scores.
- **Ownership-aware locking**: `acquire_lock` cross-checks `file_ownership` after acquiring. When an agent locks a file owned by another agent, the response includes `ownership_warning` and a `boundary_crossing` conflict + notification are recorded. Self-lock refreshes skip this check.
- **Force steal with conflict log**: `acquire_lock(force=True)` records the steal in `lock_conflicts` before overwriting, so conflicts are auditable.
- **Cascade orphaning**: When an agent dies, children are re-parented to the grandparent (or become root if no grandparent). The stale `lineage` rows referencing the dead agent as parent are deleted so the responsibility-inheritance scan always joins on a live spawning parent. No agent is permanently orphaned.
- **No message passing**: CoordinationHub is a shared database, not a message bus. Agents communicate by convention (lock acquisition, change notifications) and polling.
- **Coordination URL in context bundle**: Parent agents embed `coordination_url` string. Override via `COORDINATIONHUB_COORDINATION_URL` environment variable.
- **SQLite WAL mode**: `PRAGMA wal_checkpoint(TRUNCATE)` on engine close ensures no unbounded WAL growth.
- **Region locking**: `document_locks` uses `id INTEGER PRIMARY KEY AUTOINCREMENT` with `region_start INTEGER` and `region_end INTEGER` columns, allowing multiple locks per file on non-overlapping regions. Shared locks (multiple readers) are enforced — multiple shared locks on the same region are allowed, but an exclusive lock blocks all others. `_regions_overlap()`, `find_conflicting_locks()`, and `find_own_lock()` in `lock_ops.py` handle overlap detection. `acquire_lock` uses `BEGIN IMMEDIATE` for thread-safe concurrent locking.
- **DB schema versioning**: `db.py` tracks a `schema_version` table; `_CURRENT_SCHEMA_VERSION` is kept in sync with the latest `_migrate_*` function (currently 20). `init_schema()` auto-migrates forward. The full chain covers the document_locks restructure (v2), the `claude_agent_id` column (v3), task hierarchy and priority columns (v11–v12), the dead-letter queue (v13), HA leases and spawner tables (v14–v15), stop-request tracking (v16), scoped responsibilities (v17), broadcast journal (v18), expected-count tracking (v19), and the spawner/subagent table merge (v20). Migration runner preserves existing data. **Every call runs every migration in order** — each one is idempotent via `PRAGMA table_info` checks, so DBs stamped with a version number by buggy earlier init_schema code paths still get their tables repaired. Indexes are created after migrations so they always reference the latest column set. This is load-bearing: an earlier bug stamped a version on DBs where the tables had not actually been migrated, causing every hook call to crash silently for hours on Review Fourteen's test project.
- **CLI auto-reap**: `cmd_list_agents` and `cmd_dashboard` both call `reap_stale_agents(timeout=...)` before querying so their output converges on the same state — Review Fourteen found them drifting when one reaped and the other did not.
- **Claude Code agent ID mapping**: `agents.claude_agent_id` stores the raw hex ID that Claude Code assigns to spawned sub-agents. During SubagentStart, the hook stores this mapping. During PreToolUse/PostToolUse, `_resolve_agent_id` looks up the mapping to return the `hub.cc.*` child ID instead of the raw hex — preventing ghost agent duplication and hierarchy disconnection.
- **SubagentStop resolves via claude_agent_id**: `handle_subagent_stop` uses `_resolve_agent_id` (not `_subagent_id`) to find the correct `hub.cc.*` child ID from the raw Claude hex ID. This ensures `deregister_agent` sets `status='stopped'` on the correct agent record. Falls back to `_subagent_id` derivation if no mapping exists.
- **Background agent dedup**: `handle_subagent_start` checks `find_agent_by_claude_id` before generating a new child ID. If an agent with the same `claude_agent_id` already exists (e.g., `run_in_background` agents that fire SubagentStart twice), the existing agent is heartbeated instead of creating a duplicate.
- **Smart lock reap**: `reap_expired_locks(agent_grace_seconds=N)` implicitly refreshes expired locks held by agents with a recent heartbeat — the TTL is a fallback for crashed agents, not a hard deadline. The hook passes `agent_grace_seconds=120.0` before every acquire, preventing locks from expiring mid-operation when the model takes longer than the TTL between PreToolUse and PostToolUse.
- **First-write-wins file ownership**: `handle_post_write` calls `engine.claim_file_ownership(path, agent_id)` using `INSERT OR IGNORE` — the first agent to write a file becomes its owner. The `scan_project` tool remains as a bulk-reassign mechanism for graph-role-based ownership.
- **Contract test fixtures**: `tests/fixtures/claude_code_events/*.json` capture the minimum event shape each hook handler depends on. The hook's `COORDINATIONHUB_CAPTURE_EVENTS=1` env var saves real events to `~/.coordinationhub/event_snapshots/` for updating fixtures. **Never write fixtures without live capture** — v0.4.6 and earlier carried a fabricated `SubagentStart` fixture (`subagent_id` + `tool_input.subagent_type` + `tool_input.description`) that silently broke sub-agent `current_task` tracking for months. Real events use `agent_id` and `agent_type` at the top level with no `tool_input` at all.
- **Sub-agent task correlation (PreToolUse[Agent] → SubagentStart)**: Claude Code's `SubagentStart` event carries only `agent_id` (raw hex), `agent_type`, `session_id`, and `cwd` — no description, no `tool_use_id`. The description lives only in the preceding `PreToolUse` event with `tool_name == "Agent"`. `handle_pre_agent` stashes `(tool_use_id, session_id, subagent_type, description, prompt)` in `pending_tasks`; the following `handle_subagent_start` pops the oldest unconsumed row for `(session_id, subagent_type)` and applies the description as `current_task`. FIFO correlation works because Claude Code fires the two events in order. Bucketing by `subagent_type` means parallel spawns of different types (Explore + Plan) don't collide. Stale rows are reaped automatically after 10 minutes.
- **`broadcast` message/action params removed**: The `message` and `action` positional params were removed (they were never stored). The `document_path` optional param remains — when provided, it is used to check for lock conflicts among acknowledged siblings and is not persisted.

## <!-- GEN:tool-count -->50<!-- /GEN --> MCP Tools + 3 Setup Commands

Identity: `register_agent`, `heartbeat`, `deregister_agent`, `list_agents`, `get_agent_relations`
Locking: `acquire_lock`, `release_lock`, `refresh_lock`, `get_lock_status`, `list_locks`, `admin_locks`
Coordination: `broadcast`, `wait_for_locks`, `await_agent`
Broadcast: `acknowledge_broadcast`, `wait_for_broadcast_acks`
Messaging: `send_message`, `manage_messages`
Change: `notify_change`, `get_notifications`
Audit: `get_conflicts`, `get_contention_hotspots`, `status`
Graph & Visibility: `load_coordination_spec`, `scan_project`, `get_agent_status`, `get_file_agent_map`, `update_agent_status`, `get_agent_tree`, `run_assessment`
Tasks: `create_task`, `create_subtask`, `assign_task`, `update_task_status`, `query_tasks`, `wait_for_task`, `get_available_tasks`, `task_failures`
Dependencies: `manage_dependencies`
Work Intent: `manage_work_intents`
Handoffs: `wait_for_handoff`
HA Leases: `acquire_coordinator_lease`, `manage_leases`
Spawner: `spawn_subagent`, `report_subagent_spawned`, `get_pending_spawns`, `request_subagent_deregistration`, `await_subagent_registration`, `await_subagent_stopped`, `is_subagent_stop_requested`

Setup commands (CLI-only): `init`, `doctor`, `watch`.

Several tools are meta-tools that dispatch on an `action` argument (`manage_messages`, `manage_dependencies`, `manage_work_intents`, `manage_leases`, `admin_locks`, `query_tasks`, `task_failures`). This keeps the MCP surface small (see `tests/test_tool_count.py` — target ≤ 50) while preserving fine-grained operations.

**Tool count is dynamic** — `status()` returns `len(TOOL_DISPATCH)`, not a hardcoded number. See `COMPLETE_PROJECT_DOCUMENTATION.md` for the full auto-generated tool table with descriptions.

## Dev Commands

```bash
# Setup & diagnostics
coordinationhub init              # configure hooks, create DB
coordinationhub doctor            # validate setup

# HTTP server (stdlib only)
coordinationhub serve --port 9877

# Stdio MCP (requires mcp package)
pip install coordinationhub[mcp]
coordinationhub serve-mcp

# CLI tools
coordinationhub status
coordinationhub register <agent_id> --parent-id <parent>
coordinationhub acquire-lock <path> <agent_id>
coordinationhub get-conflicts
coordinationhub watch             # live agent tree refresh
```

## Integration Notes

- When spawning a sub-agent, call `register_agent(agent_id=<new_id>, parent_id=<parent_id>)` first
- Pass the returned context bundle to the sub-agent as its initial coordination state
- Call `heartbeat(agent_id)` at least every 30 seconds — it only updates the timestamp, no lock reaping
- Call `notify_change(path, 'modified', agent_id)` after writing a shared document
- Use `broadcast(agent_id, document_path=<path>)` before taking a significant action that affects siblings
- Lock files before writing shared documents: `acquire_lock(path, agent_id, force=False)`
- Use `get_agent_tree()` as a shared situational reference — every agent sees the same live hierarchy with current tasks, active locks, and boundary warnings

## Claude Code Integration

Project-level hooks in `.claude/settings.json` wire CoordinationHub into Claude Code sessions automatically:

- **SessionStart**: Registers a root agent (`hub.cc.{session_id}`)
- **UserPromptSubmit**: Stamps the root agent's `current_task` with the user's prompt (truncated to 120 chars, whitespace collapsed). Without this hook, `coordinationhub watch` and `get_agent_tree` show the root agent as task-less even while it holds locks.
- **PreToolUse Write/Edit**: Acquires a file lock before writes; denies if another agent holds it
- **PreToolUse Agent**: Stashes the sub-agent's `description`, `prompt`, and `subagent_type` in `pending_tasks` keyed by `tool_use_id`. The following `SubagentStart` consumes it. See the "Sub-agent task correlation" design note below.
- **PostToolUse Write/Edit**: Fires `notify_change` after successful writes; releases the lock immediately so other agents can acquire the file without waiting for TTL expiry
- **SubagentStart/SubagentStop**: Registers/deregisters child agents for spawned subagents. SubagentStart consumes the pending task stashed by the preceding `PreToolUse[Agent]` and applies the description as the child's `current_task`. Symmetric with the root agent, whose `current_task` is populated from `UserPromptSubmit`.
- **SessionEnd**: Releases all locks and deregisters the session agent

**Stele bridge**: PostToolUse on `mcp__stele-context__index` fires `notify_change` with type `"indexed"`.
**Trammel bridge**: PostToolUse on `mcp__trammel__claim_step` calls `update_agent_status` with the step/plan ID.

The hook script is at `coordinationhub/hooks/claude_code.py`. It reads JSON from stdin, creates a lightweight engine per call (~5ms), and fails open on any error.

**Hooks are global** — configured in `~/.claude/settings.json` using `python3 -m coordinationhub.hooks.claude_code` so they fire across all projects. If coordinationhub is not installed in a project's environment, the hook silently no-ops.

To disable hooks temporarily, add `"disableAllHooks": true` to `~/.claude/settings.json` or a project's `.claude/settings.json`.

## Known Issues

- **Existing DBs**: The `lineage` table uses a composite primary key `(parent_id, child_id)`. Existing `.coordinationhub/coordination.db` files created before the orphan lineage cleanup change may have stale lineage rows for re-parented children. A manual migration or fresh start is recommended.

## Test Suite

```bash
python -m pytest tests/ -v
# <!-- GEN:test-count -->393<!-- /GEN --> tests across 23 test files:
#   test_agent_lifecycle.py  — 26 tests
#   test_locking.py          — 46 tests (includes smart reap)
#   test_notifications.py    — 8 tests
#   test_conflicts.py        — 16 tests
#   test_coordination.py     — 7 tests
#   test_visibility.py       — 31 tests
#   test_event_bus.py        — 5 tests
#   test_lock_cache.py       — 9 tests
#   test_graphs.py           — 22 tests
#   test_assessment.py       — 33 tests (includes DB→trace converter tests)
#   test_integration.py      — 15 tests (HTTP transport)
#   test_core.py             — 28 tests (graph delegation, path utils, agent ID)
#   test_cli.py              — 14 tests (parser, list-agents/dashboard consistency)
#   test_concurrent.py       — 8 tests (threading: locks, registration, notifications)
#   test_scenario.py         — 13 tests (end-to-end multi-agent + live session assessment)
#   test_hooks.py            — 66 tests (hook handlers, agent ID mapping, file ownership, event contract, UserPromptSubmit, PreToolUse[Agent] correlation)
#   test_hooks_base.py       — 8 tests (BaseHook lifecycle, Kimi/Claude adapters)
#   test_setup.py            — 8 tests (doctor, init, hook merge)
#   test_db_migration.py     — 9 tests (legacy DB, stuck-version recovery, fresh install)
#   test_db_safety.py        — 14 tests (connection hardening for standalone modules)
#   test_multiprocess_sync.py — 1 test (cross-process event journal)
#   test_spawner.py          — 5 tests (HA coordinator spawn registry)
#   test_tool_count.py       — 1 test (asserts MCP surface ≤ 50)
#   load_test.py             — Load/stress test (100 agents × 50 files, not pytest-collected)
```

Always run the test suite before and after changes.
