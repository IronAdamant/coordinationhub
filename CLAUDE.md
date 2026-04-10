# CLAUDE.md — CoordinationHub

**Audience:** Autonomous coding agents (MCP, CLI invoked by agents), not a standalone dashboard.

## Project Overview

CoordinationHub externalizes the coordination bottleneck for multi-agent coding swarms. It tracks agent identity and lineage, enforces document locking, detects lock conflicts, propagates coordination context to spawned sub-agents, and provides a shared ground truth for "who is doing what" across all LLMs and IDEs.

Zero third-party dependencies in core. Works standalone or alongside Stele, Chisel, and Trammel.

## Architecture

```
coordinationhub/
  __init__.py         — Package init, exports CoordinationEngine, CoordinationHubMCPServer
  core.py             — CoordinationEngine: identity, change, audit, graph/visibility methods (~260 LOC)
  core_locking.py     — LockingMixin: all locking + coordination methods (~230 LOC)
  _storage.py        — CoordinationStorage: SQLite pool, path resolution, lifecycle (~131 LOC)
  paths.py            — Project-root detection and path normalization (~47 LOC)
  context.py          — Context bundle builder for register_agent responses (~97 LOC)
  schemas.py          — Schema aggregator, re-exports TOOL_SCHEMAS (~31 LOC)
  schemas_identity.py  — Identity & Registration schemas (~123 LOC)
  schemas_locking.py    — Document Locking schemas (~165 LOC)
  schemas_coordination.py — Coordination Action schemas (~59 LOC)
  schemas_change.py     — Change Awareness schemas (~77 LOC)
  schemas_audit.py     — Audit & Status schemas (~43 LOC)
  schemas_visibility.py — Graph & Visibility schemas (8 tools, ~156 LOC)
  dispatch.py         — Tool dispatch table (~49 LOC)
  graphs.py           — Graph aggregator: singleton + disk loading + validation (~146 LOC)
  graph_validate.py   — Pure validation functions (~131 LOC)
  graph_loader.py     — File loading (YAML/JSON) and spec auto-detection (~49 LOC)
  graph.py            — CoordinationGraph in-memory object (~66 LOC)
  visibility.py       — Thin re-export aggregator for scan/agent_status/responsibilities (~15 LOC)
  scan.py             — File ownership scan, nearest-ancestor assignment (~207 LOC)
  agent_status.py     — Agent status query, file map, and agent tree helpers (~225 LOC)
  responsibilities.py  — Agent role/responsibilities storage from graph (~35 LOC)
  assessment_scorers.py — 5 metric scorers + shared event_matches_responsibility helper (~315 LOC)
  assessment.py       — Suite loading, run_assessment, Markdown report, SQLite storage (~241 LOC)
  mcp_server.py       — HTTP MCP server (ThreadedHTTPServer, stdlib only, ~275 LOC)
  mcp_stdio.py        — Stdio MCP server (optional mcp package required, ~175 LOC)
  cli.py              — argparse CLI parser + lazy dispatch (~237 LOC)
  cli_commands.py     — Re-exports all CLI handlers from domain sub-modules (~44 LOC)
  cli_utils.py        — Shared CLI helpers: print_json, engine_from_args, close (~30 LOC)
  cli_agents.py       — Agent identity & lifecycle CLI commands (~180 LOC)
  cli_locks.py        — Document locking & coordination CLI commands (~210 LOC)
  cli_vis.py          — Change awareness, audit, graph, assessment CLI + agent-tree (~323 LOC)
  db.py               — SQLite schema (canonical) + schema versioning + thread-local ConnectionPool (~280 LOC)
  agent_registry.py   — Thin re-export aggregator for registry_ops/registry_query (~23 LOC)
  registry_ops.py     — Agent lifecycle ops: register, heartbeat, deregister (~106 LOC)
  registry_query.py   — Agent registry queries: list, lineage, siblings, reaping (~152 LOC)
  lock_ops.py         — Shared lock primitives: acquire, release, refresh, region overlap (~175 LOC)
  conflict_log.py     — Conflict recording and querying (~52 LOC)
  notifications.py     — Change notification storage and retrieval (~94 LOC)
  hooks/
    __init__.py
    claude_code.py    — Claude Code hook: auto-locking, notifications, Stele/Trammel bridge (~310 LOC)
  tests/              — pytest suite (246 tests, 15 test files)
```

## Module Design

- **Zero internal deps in sub-modules**: `agent_registry.py`, `lock_ops.py`, `conflict_log.py`, `notifications.py`, `visibility.py` all receive `connect: ConnectFn` from the caller. They have no internal imports from each other.
- **LockingMixin in `core_locking.py`**: All locking and coordination methods (`acquire_lock`, `release_lock`, `refresh_lock`, `get_lock_status`, `list_locks`, `release_agent_locks`, `reap_expired_locks`, `reap_stale_agents`, `broadcast`, `wait_for_locks`) live in `LockingMixin`. `CoordinationEngine` inherits from `LockingMixin`, keeping `core.py` focused on identity, change awareness, audit, and graph/visibility.
- **Storage layer isolated in `_storage.py`**: `CoordinationStorage` owns the SQLite pool, path resolution, and schema init. Both `core.py` and CLI entry points depend on it.
- **Canonical schemas in `db.py` only**: All table definitions and indexes live in `db.py._SCHEMAS` and `db.py._INDEXES`. Sub-modules do not define their own schemas — `init_schema()` creates everything.
- **Thread-local connection pool**: `db.py` provides a `ConnectionPool` that gives each thread its own reused SQLite connection. WAL mode enabled, 30s busy timeout.
- **Thread-safe ID generation**: `_storage.py` uses `threading.Lock` + in-memory sequence counters to guarantee unique agent IDs even under concurrent `generate_agent_id()` calls.
- **CLI helpers consolidated**: `cli_utils.py` provides `print_json`, `engine_from_args`, and `close` shared by all `cli_*.py` modules.
- **Dispatch separation**: `schemas.py` (schemas only) and `dispatch.py` (dispatch table) are separate modules shared by both HTTP and stdio servers.
- **Project root detection**: `detect_project_root()` in `paths.py` walks up from CWD looking for `.git`. Used by `CoordinationEngine.__init__`.

## Key Design Decisions

- **Agent ID format**: `{namespace}.{PID}.{sequence}` for root agents, `{parent_id}.{sequence}` for children. PID encoded to distinguish agents from different processes. Sequence numbers derived via `_next_seq_atomic()` with in-memory counters seeded from DB, serialized by `_seq_lock`.
- **Concurrent lock safety**: `acquire_lock` uses `BEGIN IMMEDIATE` to serialize concurrent lock attempts. Two threads racing for the same file are sequenced at the transaction level rather than catching `IntegrityError` after the fact.
- **TTL-based locks**: All locks expire unless refreshed. Default 300s. `heartbeat()` does NOT reap expired locks — call `reap_expired_locks()` explicitly.
- **Assessment keyword matching**: Shared `event_matches_responsibility()` in `assessment_scorers.py` maps event types to responsibility keywords via `_EVENT_RESPONSIBILITY_MAP` dict. Extensible — add new event-type groups to the map to support custom vocabularies. Non-standard terms that don't contain any mapped keyword will reduce scores.
- **Force steal with conflict log**: `acquire_lock(force=True)` records the steal in `lock_conflicts` before overwriting, so conflicts are auditable.
- **Cascade orphaning**: When an agent dies, children are re-parented to the grandparent (or become root if no grandparent). The stale `lineage` rows referencing the dead agent as parent are deleted so the responsibility-inheritance scan always joins on a live spawning parent. No agent is permanently orphaned.
- **No message passing**: CoordinationHub is a shared database, not a message bus. Agents communicate by convention (lock acquisition, change notifications) and polling.
- **Coordination URL in context bundle**: Parent agents embed `coordination_url` string. Override via `COORDINATIONHUB_COORDINATION_URL` environment variable.
- **SQLite WAL mode**: `PRAGMA wal_checkpoint(TRUNCATE)` on engine close ensures no unbounded WAL growth.
- **Region locking**: `document_locks` uses `id INTEGER PRIMARY KEY AUTOINCREMENT` with `region_start INTEGER` and `region_end INTEGER` columns, allowing multiple locks per file on non-overlapping regions. Shared locks (multiple readers) are enforced — multiple shared locks on the same region are allowed, but an exclusive lock blocks all others. `_regions_overlap()`, `find_conflicting_locks()`, and `find_own_lock()` in `lock_ops.py` handle overlap detection. `acquire_lock` uses `BEGIN IMMEDIATE` for thread-safe concurrent locking.
- **DB schema versioning**: `db.py` tracks `schema_version` table with `_CURRENT_SCHEMA_VERSION = 2`. `init_schema()` auto-migrates from v1 to v2 (document_locks table restructure for region locking). Migration runner `_migrate_v1_to_v2` preserves existing lock data.
- **`broadcast` message/action params removed**: The `message` and `action` positional params were removed (they were never stored). The `document_path` optional param remains — when provided, it is used to check for lock conflicts among acknowledged siblings and is not persisted.

## 29 MCP Tools

Identity: `register_agent`, `heartbeat`, `deregister_agent`, `list_agents`, `get_lineage`, `get_siblings`
Locking: `acquire_lock`, `release_lock`, `refresh_lock`, `get_lock_status`, `list_locks`, `release_agent_locks`, `reap_expired_locks`, `reap_stale_agents`
Coordination: `broadcast`, `wait_for_locks`
Change: `notify_change`, `get_notifications`, `prune_notifications`
Audit: `get_conflicts`, `status`
Graph & Visibility (0.3.1): `load_coordination_spec`, `validate_graph`, `scan_project`, `get_agent_status`, `get_file_agent_map`, `update_agent_status`, `run_assessment`, `get_agent_tree`

**Tool count is dynamic** — `status()` returns `len(TOOL_DISPATCH)` (currently 29), not a hardcoded number.

## Dev Commands

```bash
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
```

## Integration Notes

- When spawning a sub-agent, call `register_agent(agent_id=<new_id>, parent_id=<parent_id>)` first
- Pass the returned context bundle to the sub-agent as its initial coordination state
- Call `heartbeat(agent_id)` at least every 30 seconds — it only updates the timestamp, no lock reaping
- Call `notify_change(path, 'modified', agent_id)` after writing a shared document
- Use `broadcast(agent_id, document_path=<path>)` before taking a significant action that affects siblings
- Lock files before writing shared documents: `acquire_lock(path, agent_id, force=False)`

## Claude Code Integration

Project-level hooks in `.claude/settings.json` wire CoordinationHub into Claude Code sessions automatically:

- **SessionStart**: Registers a root agent (`hub.cc.{session_id}`)
- **PreToolUse Write/Edit**: Acquires a file lock before writes; denies if another agent holds it
- **PostToolUse Write/Edit**: Fires `notify_change` after successful writes
- **SubagentStart/SubagentStop**: Registers/deregisters child agents for spawned subagents
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
# 246 tests across 15 test files:
#   test_agent_lifecycle.py  — 21 tests
#   test_locking.py          — 38 tests
#   test_notifications.py    — 8 tests
#   test_conflicts.py        — 6 tests
#   test_coordination.py     — 7 tests
#   test_visibility.py       — 30 tests
#   test_graphs.py           — 22 tests
#   test_assessment.py       — 24 tests
#   test_integration.py      — 15 tests (HTTP transport)
#   test_core.py             — 28 tests (graph delegation, path utils, agent ID)
#   test_cli.py              — 11 tests (argparse parser, subcommand dispatch)
#   test_concurrent.py       — 8 tests (threading: locks, registration, notifications)
#   test_scenario.py         — 6 tests (end-to-end multi-agent lifecycle workflows)
#   test_hooks.py            — 23 tests (Claude Code hook handlers)
```

Always run the test suite before and after changes.
