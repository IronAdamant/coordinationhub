# CoordinationHub — Complete Project Documentation

**Version:** 0.4.0
**Last updated:** 2026-04-11

## v0.4.0 Changelog

### Architecture
- **Module consolidation — 13 files deleted.** Re-export aggregators and artificial 500-LOC splits collapsed into domain modules:
  - `registry_ops.py` + `registry_query.py` → `agent_registry.py` (~290 LOC)
  - 6 `schemas_*.py` → single `schemas.py` (~590 LOC, pure data grouped by function)
  - `graph.py` + `graph_validate.py` + `graph_loader.py` → `graphs.py` (~330 LOC)
  - `responsibilities.py` → merged into `scan.py`; `visibility.py` (re-export) removed, `core.py` imports `agent_status` + `scan` directly
  - Net: 42 → 29 Python files in `coordinationhub/`.

### Fixed
- **TTL locks no longer expire mid-operation.** `reap_expired_locks(agent_grace_seconds=N)` implicitly refreshes expired locks held by agents with a recent heartbeat. Hook PreToolUse passes `agent_grace_seconds=120.0`; hook TTL bumped from 120s to 300s; hook PostToolUse refreshes the lock after `notify_change`.
- **`file_ownership` table now populated automatically.** New `CoordinationEngine.claim_file_ownership(path, agent_id)` method uses `INSERT OR IGNORE`. Hook PostToolUse calls it on every write — first agent to write a file becomes its owner. Boundary warnings, agent-tree ownership labels, and file-agent maps now have real data.
- **`graphs.py` missing `Any` import** — worked at runtime only because of `from __future__ import annotations`. Fixed during consolidation.

### Added
- **Version consistency CI check** (`.github/workflows/test.yml`) — fails the build if `pyproject.toml` and `__init__.py` versions don't match.
- **Contract test fixtures** (`tests/fixtures/claude_code_events/*.json`) — minimum event shape each hook handler depends on. `COORDINATIONHUB_CAPTURE_EVENTS=1` env var saves real events to `~/.coordinationhub/event_snapshots/` for updating fixtures.
- **`TestEventContract` class** — 12 tests that validate required fields and run each handler against its fixture.
- **2 file ownership tests** — `test_post_write_claims_file_ownership`, `test_file_ownership_first_write_wins`.
- **2 smart reap tests** — `test_reap_spares_active_agent_locks`, `test_reap_removes_crashed_agent_locks`.

### Changed
- `lock_ops.py`: ~175 LOC → ~195 LOC (smart reap with grace period).
- `core_locking.py`: ~260 LOC → ~265 LOC (grace period passthrough).
- `hooks/claude_code.py`: ~428 LOC → ~450 LOC (TTL bump, ownership claim, event capture, PostToolUse refresh).
- `core.py`: `claim_file_ownership` method added; `visibility` import removed.
- Tests: 274 → 290 across 16 files. `test_hooks.py`: 33 → 47. `test_locking.py`: 38 → 40.

---

## v0.3.8 Changelog

### Fixed
- **SubagentStop status transition** — `handle_subagent_stop` now uses `_resolve_agent_id` to look up the correct `hub.cc.*` child ID from the raw Claude hex ID, then calls `deregister_agent` which sets `status='stopped'`. Previously used `_subagent_id` which generated a wrong sequence-based ID, so deregistration silently failed and all agents stayed "active" permanently.
- **Background agent double registration** — `handle_subagent_start` now checks `find_agent_by_claude_id` before generating a new child ID. Agents with `run_in_background: true` fire SubagentStart twice with the same Claude hex ID — the second call now heartbeats the existing agent instead of creating a duplicate entry.

### Added
- **2 new hook tests** — `test_subagent_stop_sets_status_stopped_via_claude_id`, `test_background_agent_dedup`.

### Changed
- `hooks/claude_code.py`: ~400 LOC → ~428 LOC (SubagentStop rewrite, SubagentStart dedup).
- Tests: 272 → 274 across 16 files. `test_hooks.py`: 31 → 33.

---

## v0.3.7 Changelog

### Added
- **`coordinationhub doctor` CLI command** — Validates setup: importability, hooks config in `~/.claude/settings.json`, storage directory, schema version, hook Python interpreter (detects venv trap). Returns structured results with OK/FAIL per check.
- **`coordinationhub init` CLI command** — One-command setup: creates `.coordinationhub/` directory, initializes DB, writes/merges hook config into `~/.claude/settings.json` using the absolute path to the current Python interpreter (avoids venv trap), then runs doctor checks.
- **`coordinationhub watch` CLI command** — Live-refresh agent tree with configurable interval (`--interval N`). Displays agent count, lock count, and conflict count in a status bar. Ctrl+C to stop.
- **Hook error logging** — Errors in `hooks/claude_code.py` are now logged to `~/.coordinationhub/hook.log` with timestamps and tracebacks (max 1 MB, auto-truncated). Also prints to stderr. Hooks still fail open (exit 0).
- **Session summary on SessionEnd** — `handle_session_end` now returns a summary in `additionalContext`: agents tracked, locks held, conflicts, notifications. Visible in Claude Code's status line at session close.
- **`cli_setup.py` module** (~348 LOC) — Contains `cmd_doctor`, `cmd_init`, `cmd_watch`, `run_doctor()`, `_merge_hooks()`, `_fill_hook_command()`.
- **`test_setup.py`** — 8 tests covering doctor checks, hook merge logic, idempotency, and Python path injection.
- **3 new hook tests** — Error logging (log file creation, never raises), session summary (returns counts).

### Changed
- `hooks/claude_code.py`: ~330 LOC → ~400 LOC (error logging, session summary).
- `cli.py`: ~237 LOC → ~267 LOC (3 new subparsers + dispatch entries).
- `cli_commands.py`: ~44 LOC → ~51 LOC (re-exports from cli_setup).
- CLI commands: 31 → 34. Tests: 261 → 272 across 16 files (was 15).

---

## v0.3.6 Changelog

### Fixed
- **Critical: Sub-agent ID mismatch bug (Review Twelve)** — `_resolve_agent_id` in the Claude Code hook now maps raw Claude Code hex IDs (e.g. `ac70a34bf2d2264d4`) back to the `hub.cc.*` child IDs registered during SubagentStart. Previously, PreToolUse/PostToolUse hooks created ghost agent entries under the raw hex ID, disconnected from the parent-child hierarchy — causing 0 locks, empty parent_id, and broken assessment scoring for sub-agents.
- **Ghost agent duplication eliminated** — Sub-agents no longer exist twice in the database (once from SubagentStart with correct hierarchy, once from PreToolUse with no parent). The `claude_agent_id` column on the agents table stores the mapping.

### Added
- **`claude_agent_id` column on agents table** — Stores the raw Claude Code hex ID on the `hub.cc.*` agent record. Indexed for fast lookup. Schema version 2 → 3 with auto-migration.
- **`find_agent_by_claude_id` query** — New function in `registry_ops.py`, exposed via `agent_registry.py` and `CoordinationEngine`.
- **5 new hook tests** — `test_maps_raw_claude_id_to_hub_child`, `test_subagent_lock_uses_hub_id`, `test_no_ghost_agents`, `test_post_write_uses_hub_id`, `test_unmapped_raw_id_falls_back`.

### Changed
- `db.py`: ~280 LOC → ~295 LOC (v3 migration). `_CURRENT_SCHEMA_VERSION = 3`.
- `registry_ops.py`: ~106 LOC → ~145 LOC (`claude_agent_id` param + `find_agent_by_claude_id`).
- `hooks/claude_code.py`: ~310 LOC → ~330 LOC (engine-aware `_resolve_agent_id`, SubagentStart stores mapping).
- `core.py`: ~280 LOC → ~285 LOC (`claude_agent_id` passthrough + `find_agent_by_claude_id` method).
- Tests: 256 → 261 across 15 files. `test_hooks.py`: 23 → 28 tests.

---

## v0.3.5 Changelog

### Added
- **Ownership-aware locking** — `acquire_lock` now cross-checks `file_ownership` table. When an agent locks a file owned by another agent, the response includes an `ownership_warning` field identifying the owner. A `boundary_crossing` conflict is recorded in the conflict log, and a `boundary_crossing` change notification is fired for the owning agent to discover via polling.
- **`get_contention_hotspots` MCP tool** — Ranks files by lock contention frequency from the conflict log. Returns files ordered by conflict count with all involved agents listed. Identifies coordination chokepoints (files that multiple agents need access to).
- **`contention-hotspots` CLI command** — `coordinationhub contention-hotspots [--limit N]` for chokepoint identification.
- **10 new tests** in `test_conflicts.py`: 6 boundary crossing tests (no warning when no ownership, same owner, cross-owner warning, conflict logging, notification firing, self-refresh no warning) + 4 contention hotspot tests (empty, ranked by count, all agents included, limit).

### Changed
- `core_locking.py`: ~230 LOC → ~260 LOC (ownership boundary check method).
- `core.py`: ~260 LOC → ~280 LOC (get_contention_hotspots method).
- Tool count: 29 → 30. CLI commands: 30 → 31. Tests: 246 → 256 across 15 files.

---

## v0.3.4 Changelog

### Added
- **`core_locking.py` (~230 LOC)** — `LockingMixin` extracted from `core.py`. Contains all locking and coordination methods: `acquire_lock`, `release_lock`, `refresh_lock`, `get_lock_status`, `list_locks`, `release_agent_locks`, `reap_expired_locks`, `reap_stale_agents`, `broadcast`, `wait_for_locks`. `CoordinationEngine` inherits from `LockingMixin`.
- **Assessment keyword synonyms** — `_EVENT_RESPONSIBILITY_MAP` in `assessment_scorers.py` expanded with ~20 synonyms + token-overlap fallback for unknown event types.
- **SQLite perf tuning** — `db.py` adds `PRAGMA cache_size=-8000` and `PRAGMA mmap_size=67108864`. New composite expiry index `idx_locks_expiry`.

### Changed
- `core.py`: ~495 LOC → ~260 LOC (locking/coordination extracted to `core_locking.py`).
- `assessment_scorers.py`: ~304 LOC → ~315 LOC (synonym expansion).
- `db.py`: ~275 LOC → ~280 LOC (perf pragmas + expiry index).

---

## v0.3.3 Changelog

### Added
- **CI test workflow** — `.github/workflows/test.yml` runs pytest on push/PR across Python 3.10-3.12.
- **DB schema versioning** — `db.py` now has `schema_version` table, `_CURRENT_SCHEMA_VERSION = 2`, migration runner (`_migrate_v1_to_v2`), auto-migration on `init_schema()`.
- **Region locking** — `document_locks` table changed from `document_path TEXT PRIMARY KEY` to `id INTEGER PRIMARY KEY AUTOINCREMENT` with `region_start INTEGER` and `region_end INTEGER` columns. Multiple locks per file on non-overlapping regions. Shared locks enforced (multiple shared locks allowed, exclusive blocks). `acquire_lock`, `release_lock`, `refresh_lock`, `get_lock_status`, `list_locks` all support `region_start`/`region_end` params. New functions in `lock_ops.py`: `_regions_overlap`, `find_conflicting_locks`, `find_own_lock`.
- **Hook unit tests** — new `tests/test_hooks.py` with 23 tests covering all hook handlers.
- **`acquire_lock` uses `BEGIN IMMEDIATE`** for thread-safe concurrent locking with new schema.
- CLI commands `acquire-lock`, `release-lock`, `refresh-lock` have `--region-start`/`--region-end` flags.

### Changed
- `lock_ops.py`: ~119 LOC → ~175 LOC. `db.py`: ~215 LOC → ~275 LOC. `core.py`: ~470 LOC → ~495 LOC. `schemas_locking.py`: ~160 LOC → ~165 LOC.
- Test count: 206 → 246 across 15 files (was 14). `test_locking.py`: 21 → 38 tests.

---

## v0.3.2 Changelog

### Added
- **`list_locks` MCP tool** — lists all active (non-expired) locks, optionally filtered by `agent_id`. Returns lock details: document path, holder, expiry time, lock type, worktree.
- **`list-locks` CLI command** — `coordinationhub list-locks [--agent-id <id>]` for lock observability.
- **5 new tests** in `test_locking.py` for `list_locks`: empty, active, expired-excluded, agent-filtered, detail fields.

### Changed
- **Hook TTL reduced from 600s to 120s** — `handle_pre_write` now uses 120s lock TTL (was 600s). Prevents stale locks from completed agents blocking work for 10 minutes.
- **Hook reaps expired locks before acquire** — `handle_pre_write` calls `reap_expired_locks()` before `acquire_lock()` as a safety net for stale locks from crashed agents.
- Tool count: 28 → 29. CLI commands: 29 → 30. Test count: 202 → 206.

### Fixed
- **Review Ten bug: stale locks from completed agents** — combination of shorter TTL + pre-acquire reaping ensures expired locks are cleaned up before blocking new work.

---

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
| `coordinationhub/core.py` | `CoordinationEngine` — identity, change, audit, graph/visibility methods (~285 LOC) | _storage, core_locking, agent_registry, lock_ops, conflict_log, notifications, graphs, visibility, assessment, paths, context |
| `coordinationhub/core_locking.py` | `LockingMixin` — all locking + coordination methods (~230 LOC) | lock_ops, conflict_log |
| `coordinationhub/_storage.py` | `CoordinationStorage` — SQLite pool, path resolution, thread-safe ID gen (~131 LOC) | db |
| `coordinationhub/paths.py` | Project-root detection and path normalization (~48 LOC) | (no internal deps) |
| `coordinationhub/context.py` | Context bundle builder for `register_agent` responses (~100 LOC) | (no internal deps) |
| `coordinationhub/schemas.py` | Schema aggregator — imports all groups, re-exports `TOOL_SCHEMAS` (~31 LOC) | (no internal deps) |
| `coordinationhub/schemas_identity.py` | Identity & Registration schemas (6 tools, ~123 LOC) | (no internal deps) |
| `coordinationhub/schemas_locking.py` | Document Locking schemas (8 tools, ~165 LOC) | (no internal deps) |
| `coordinationhub/schemas_coordination.py` | Coordination Action schemas (2 tools, ~59 LOC) | (no internal deps) |
| `coordinationhub/schemas_change.py` | Change Awareness schemas (3 tools, ~77 LOC) | (no internal deps) |
| `coordinationhub/schemas_audit.py` | Audit & Status schemas (2 tools, ~43 LOC) | (no internal deps) |
| `coordinationhub/schemas_visibility.py` | Graph & Visibility schemas (8 tools, ~156 LOC) | (no internal deps) |
| `coordinationhub/dispatch.py` | Tool dispatch table: name → (method_name, allowed_kwargs) (~48 LOC) | (no internal deps) |
| `coordinationhub/graphs.py` | Graph aggregator: singleton + disk loading + validation (~146 LOC) | graph_validate, graph_loader, graph |
| `coordinationhub/graph_validate.py` | Pure validation functions: agents, handoffs, escalation, assessment (~131 LOC) | (no internal deps) |
| `coordinationhub/graph_loader.py` | File loading (YAML/JSON) and spec auto-detection (~49 LOC) | (no internal deps; optional ruamel.yaml) |
| `coordinationhub/graph.py` | CoordinationGraph in-memory object with lookup helpers (~66 LOC) | graph_validate |
| `coordinationhub/visibility.py` | Thin re-export aggregator for scan/agent_status/responsibilities (~15 LOC) | scan, agent_status, responsibilities |
| `coordinationhub/scan.py` | File ownership scan, graph-role-aware assignment, spawned-agent inheritance (~207 LOC) | (no internal deps) |
| `coordinationhub/agent_status.py` | Agent status query, file map, rich agent tree with locks/warnings (~290 LOC) | (no internal deps) |
| `coordinationhub/responsibilities.py` | Agent role/responsibilities storage from graph (~35 LOC) | (no internal deps) |
| `coordinationhub/agent_registry.py` | Thin re-export aggregator for registry_ops/registry_query (~23 LOC) | registry_ops, registry_query |
| `coordinationhub/registry_ops.py` | Agent lifecycle ops: register, heartbeat, deregister, find_by_claude_id (~145 LOC) | db |
| `coordinationhub/registry_query.py` | Agent registry queries: list, lineage, siblings, reaping (~142 LOC) | db |
| `coordinationhub/assessment_scorers.py` | 5 metric scorers + shared `event_matches_responsibility` helper + `_EVENT_RESPONSIBILITY_MAP` (~315 LOC) | (no internal deps) |
| `coordinationhub/assessment.py` | Suite loading, `run_assessment`, Markdown report, SQLite storage (~241 LOC). Re-exports scorers for backward compat. | assessment_scorers, graphs |
| `coordinationhub/mcp_server.py` | HTTP MCP server (`ThreadedHTTPServer`, stdlib only) | core, dispatch, schemas |
| `coordinationhub/mcp_stdio.py` | Stdio MCP server (requires optional `mcp` package) | core, mcp_server, schemas |
| `coordinationhub/cli.py` | argparse CLI argument parser + lazy dispatch (~237 LOC) | core |
| `coordinationhub/cli_commands.py` | Re-exports all CLI handlers from domain sub-modules (~44 LOC) | cli_agents, cli_locks, cli_vis |
| `coordinationhub/cli_utils.py` | Shared CLI helpers: print_json, engine_from_args, close (~30 LOC) | core |
| `coordinationhub/cli_agents.py` | Agent identity & lifecycle CLI commands (~180 LOC) | cli_utils |
| `coordinationhub/cli_locks.py` | Document locking & coordination CLI commands (~210 LOC) | cli_utils |
| `coordinationhub/cli_vis.py` | Change awareness, audit, graph, assessment, dashboard CLI + agent-tree (~323 LOC) | cli_utils |
| `coordinationhub/db.py` | SQLite schema + schema versioning (v3) + perf pragmas + thread-local `ConnectionPool` (~295 LOC) | (no internal deps) |
| `coordinationhub/lock_ops.py` | Shared lock primitives: acquire, release, refresh, reap, region overlap (~175 LOC) | db |
| `coordinationhub/conflict_log.py` | Conflict recording and querying (~52 LOC) | lock_ops |
| `coordinationhub/notifications.py` | Change notification storage and retrieval (~94 LOC) | db |
| `coordinationhub/hooks/__init__.py` | Hooks package init | — |
| `coordinationhub/cli_setup.py` | CLI: doctor, init, watch commands (~348 LOC) | cli_utils, core, paths, db |
| `coordinationhub/hooks/claude_code.py` | Claude Code hook: auto-locking, notifications, agent ID mapping, error logging, session summary, Stele/Trammel bridge (~400 LOC) | core |
| `tests/conftest.py` | pytest fixtures: `engine`, `registered_agent`, `two_agents` | core |
| `tests/test_agent_lifecycle.py` | Agent lifecycle tests (21 tests) | conftest |
| `tests/test_locking.py` | Lock acquisition, release, refresh, status, list, reap, region locking (38 tests) | conftest |
| `tests/test_notifications.py` | Change notification tests (8 tests) | conftest |
| `tests/test_conflicts.py` | Conflict logging, boundary crossing, contention hotspots, lineage table tests (16 tests) | conftest |
| `tests/test_coordination.py` | Broadcast and wait_for_locks tests (7 tests) | conftest |
| `tests/test_visibility.py` | Visibility tools, file scan, graph loading, agent tree tests (30 tests) | conftest, graphs |
| `tests/test_graphs.py` | Graph validation and CoordinationGraph tests (22 tests) | graphs |
| `tests/test_assessment.py` | Assessment runner tests (24 tests) | assessment, graphs |
| `tests/test_integration.py` | HTTP transport integration tests (15 tests) | conftest, core |
| `tests/test_core.py` | Core engine tests: graph delegation, path utils, agent ID generation (28 tests) | conftest |
| `tests/test_cli.py` | CLI argument parser and subcommand dispatch (11 tests) | conftest, core |
| `tests/test_concurrent.py` | Concurrent stress tests: locks, registration, notifications (8 tests) | conftest |
| `tests/test_scenario.py` | End-to-end multi-agent lifecycle workflows (6 tests) | conftest |
| `tests/test_hooks.py` | Claude Code hook handler tests (31 tests) | conftest, hooks |
| `tests/test_setup.py` | Doctor, init, hook merge tests (8 tests) | cli_setup |
| `pyproject.toml` | Package config, dependencies, entry points | — |
| `.claude/settings.json` | Claude Code hooks: auto-lock, notify, Stele/Trammel bridge | — |

**Total: 272 tests across 16 test files.**

---

## Architecture

```
coordinationhub/
  __init__.py         — Package init, exports CoordinationEngine, CoordinationHubMCPServer
  core.py             — CoordinationEngine: identity, change, audit, graph/visibility methods (~285 LOC)
  core_locking.py     — LockingMixin: all locking + coordination methods (~260 LOC)
  _storage.py         — CoordinationStorage: SQLite pool, path resolution, thread-safe ID gen (~131 LOC)
  paths.py            — Project-root detection and path normalization (~47 LOC)
  context.py          — Context bundle builder for register_agent responses (~97 LOC)
  schemas.py          — Schema aggregator, re-exports TOOL_SCHEMAS (~31 LOC)
  schemas_identity.py — Identity & Registration schemas (~123 LOC)
  schemas_locking.py   — Document Locking schemas (~165 LOC)
  schemas_coordination.py — Coordination Action schemas (~59 LOC)
  schemas_change.py    — Change Awareness schemas (~77 LOC)
  schemas_audit.py    — Audit & Status schemas (~43 LOC)
  schemas_visibility.py — Graph & Visibility schemas (8 tools, ~156 LOC)
  dispatch.py         — Tool dispatch table (~49 LOC)
  graphs.py           — Graph aggregator: singleton + disk loading + validation (~146 LOC)
  graph_validate.py   — Pure validation functions (~131 LOC)
  graph_loader.py     — File loading (YAML/JSON) and spec auto-detection (~49 LOC)
  graph.py            — CoordinationGraph in-memory object (~66 LOC)
  visibility.py       — Thin re-export aggregator (~15 LOC)
  scan.py             — File ownership scan, graph-role-aware (~207 LOC)
  agent_status.py     — Agent status query, file map, rich agent tree with locks/warnings (~290 LOC)
  responsibilities.py — Agent role/responsibilities storage (~35 LOC)
  agent_registry.py   — Thin re-export aggregator (~23 LOC)
  registry_ops.py     — Agent lifecycle ops + claude_id lookup (~145 LOC)
  registry_query.py   — Agent registry queries (~152 LOC)
  assessment_scorers.py — 5 metric scorers + shared event_matches_responsibility (~315 LOC)
  assessment.py       — Suite loading, run_assessment, report, storage (~241 LOC)
  mcp_server.py       — HTTP MCP server (ThreadedHTTPServer, stdlib only, ~275 LOC)
  mcp_stdio.py        — Stdio MCP server (requires optional mcp package, ~175 LOC)
  cli_setup.py        — CLI: doctor, init, watch commands (~348 LOC)
  cli.py              — argparse CLI parser + lazy dispatch (~267 LOC)
  cli_commands.py     — Re-exports all CLI handlers (~51 LOC)
  cli_utils.py        — Shared CLI helpers: print_json, engine_from_args, close (~30 LOC)
  cli_agents.py       — Agent identity & lifecycle CLI commands (~180 LOC)
  cli_locks.py        — Document locking & coordination CLI commands (~210 LOC)
  cli_vis.py          — Change awareness, audit, graph & assessment CLI + agent-tree (~323 LOC)
  db.py               — SQLite schema (canonical) + schema versioning (v3) + perf pragmas + thread-local ConnectionPool (~295 LOC)
  lock_ops.py         — Shared lock primitives + region overlap (~175 LOC)
  conflict_log.py     — Conflict recording and querying (~52 LOC)
  notifications.py    — Change notification storage and retrieval (~94 LOC)
  hooks/
    claude_code.py    — Claude Code hook: auto-locking, notifications, agent ID mapping, error logging, session summary (~400 LOC)
  tests/              — 272 tests across 16 test files
```

**Module design principles:**
- Zero internal deps in sub-modules: each receives `connect: ConnectFn` from the caller.
- Storage layer isolated in `_storage.py`: both `core.py` and CLI entry points depend on it; sub-modules have no path to `core`.
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
    agent_id        TEXT PRIMARY KEY,
    parent_id       TEXT,
    worktree_root   TEXT NOT NULL,
    pid             INTEGER,
    started_at      REAL NOT NULL,
    last_heartbeat  REAL NOT NULL,
    status          TEXT DEFAULT 'active',
    claude_agent_id TEXT
)
```

`claude_agent_id` stores the raw Claude Code hex ID (e.g. `ac70a34bf2d2264d4`) so that PreToolUse/PostToolUse hooks can map it back to the `hub.cc.*` child ID registered during SubagentStart. Indexed via `idx_agents_claude_id`.

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
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    document_path TEXT NOT NULL,
    locked_by    TEXT NOT NULL,
    locked_at    REAL NOT NULL,
    lock_ttl     REAL DEFAULT 300.0,
    lock_type    TEXT DEFAULT 'exclusive',
    worktree_root TEXT,
    region_start INTEGER,
    region_end   INTEGER
)
```

Multiple locks per file are allowed for non-overlapping regions. Shared locks on overlapping regions are permitted; exclusive locks block all others on the same region. `region_start`/`region_end` of `NULL` means whole-file lock.

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

## MCP Tools (30 total)

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

`acquire_lock`, `release_lock`, `refresh_lock`, `get_lock_status`, `list_locks`,
`release_agent_locks`, `reap_expired_locks`, `reap_stale_agents`.

All locking tools support optional `region_start`/`region_end` parameters for region-level locking. Shared locks on overlapping regions are permitted; exclusive locks block all others.

### Coordination Actions

`broadcast` — `message`/`action` params removed (were never stored or forwarded).

`wait_for_locks` — unchanged.

### Change Awareness

`notify_change`, `get_notifications`, `prune_notifications`.

### Audit

`get_conflicts`, `get_contention_hotspots`.

### Status

`status` — returns `graph_loaded: bool` and `owned_files: int`.

### Graph & Visibility (8 tools)

| Tool | Description |
|------|-------------|
| `load_coordination_spec` | Reload spec from disk, auto-maps registered agents to graph roles |
| `validate_graph` | Validate current graph, return error list |
| `scan_project` | File ownership scan with graph-role-aware assignment |
| `get_agent_status` | Full agent info: task, responsibilities, owned files (with task mapping), locks, lineage |
| `get_file_agent_map` | Full file→agent→role→responsibilities→task map |
| `update_agent_status` | Set `current_task` for an agent |
| `run_assessment` | Run suite, output report, store results incl. full traces + refinements |
| `get_agent_tree` | Rich hierarchical agent tree: current tasks, active locks, boundary warnings, nested children |

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

## CLI Subcommands (34 total)

### Setup & Diagnostics
`init`, `doctor`, `watch`

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
`acquire-lock`, `release-lock`, `refresh-lock`, `lock-status`, `list-locks`,
`release-agent-locks`, `reap-expired-locks`, `reap-stale-agents`

### Coordination
`broadcast`, `wait-for-locks`

### Change Awareness
`notify-change`, `get-notifications`, `prune-notifications`

### Audit
`get-conflicts`, `contention-hotspots`

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
# 272 tests across 16 test files
```

---

## Migration from 0.2.0 / 0.3.0

- All existing tables (`agents`, `lineage`, `document_locks`, `lock_conflicts`,
  `change_notifications`) are preserved unchanged.
- Existing `.coordinationhub/coordination.db` files work without migration. Orphaned agents created before the lineage cleanup fix retain stale `lineage` rows — these are cleaned up automatically when a new orphan event occurs, or can be left as historical record (they do not affect `get_lineage` which walks `agents.parent_id`).
- `broadcast` no longer accepts `message` or `action` params (removed from schema and CLI).
- `status()` now returns `graph_loaded: bool` and `owned_files: int` as additional fields.
- `register_agent()` now accepts an optional `graph_agent_id` parameter.
- New tables added: `agent_responsibilities`, `file_ownership`, `assessment_results`.
- `spawn_propagation` metric added in 0.3.1 (scored even if not listed in graph assessment.metrics).
- `run_assessment` now stores full trace JSON and suggested graph refinements in `details_json`.
