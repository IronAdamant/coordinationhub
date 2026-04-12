# CoordinationHub — Complete Project Documentation

**Version:** <!-- GEN:version -->0.4.9<!-- /GEN -->
**Last updated:** 2026-04-12

## v0.4.8 Changelog — Lock Release on PostToolUse (Findings Phase 9 Fix)

### Motivation

Phase 9 findings identified a critical gap: `PostToolUse(Write/Edit)` was only *refreshing* the lock TTL after a write completed, causing locks to persist for up to 10 minutes (300s TTL). This blocked other agents from working on the same file well after the write operation finished.

The correct behavior (enforcement, not just detection) is: lock acquired before write → write completes → lock released immediately.

### Changed

- **`handle_post_write`** in `coordinationhub/hooks/claude_code.py`: replaced `engine.refresh_lock(...)` with `engine.release_lock(...)`. Lock is now released immediately after Write/Edit completes.

### Verification

- Full test suite: 335 passed, 1 skipped
- `test_hooks.py`: 66 passed

---

## v0.4.7 Changelog — Sub-agent Task Correlation (Real Event Shape)

### Motivation

Trying to demonstrate v0.4.6's sub-agent task visibility live surfaced that the v0.4.6 changelog's premise was false. The claim that "sub-agents already had their current_task populated automatically via `handle_subagent_start` reading the Agent tool's description field" was wrong in production — it was only true in our fabricated test fixture. Event capture on two separate sub-agent spawns confirmed: real Claude Code `SubagentStart` events carry `agent_id` and `agent_type` at the top level with no `tool_input` key at all. The description is only present in the *preceding* `PreToolUse` event with `tool_name == "Agent"`.

Three symptoms from one root cause (fabricated fixture):

| Bug | Root cause | Production impact |
|---|---|---|
| A | `_subagent_id` read `tool_input.subagent_type` (absent) and defaulted to `"agent"` | All sub-agents collapsed to `.agent.N` in ID; Explore, Plan, general-purpose indistinguishable |
| B | `handle_subagent_start` read `tool_input.description` (absent) | Sub-agent `current_task` was always NULL despite description being passed |
| C | `SubagentStart.json` contract fixture fabricated the event shape | Contract tests passed against a reality-fiction gap |

### Added

- **`pending_subagent_tasks` table** in `db.py._SCHEMAS` — keyed by `tool_use_id` with `(session_id, subagent_type, description, prompt, created_at, consumed_at)`. Index on `(session_id, subagent_type, consumed_at)` for FIFO lookup.
- **`coordinationhub/pending_tasks.py`** (~105 LOC) — new zero-internal-deps module with `stash_pending_task`, `consume_pending_task`, `prune_consumed_pending_tasks`. Same pattern as `notifications.py` and `conflict_log.py`.
- **`handle_pre_agent` in `hooks/claude_code.py`** — new handler for `PreToolUse[Agent]`. Reads `tool_input.description`, `tool_input.prompt`, `tool_input.subagent_type`, `tool_use_id` and calls `stash_pending_task`. No-ops if tool_use_id or subagent_type is missing.
- **`_subagent_type` helper** — reads top-level `agent_type` (real shape) with fallback to `tool_input.subagent_type` (legacy). Used by both `_subagent_id` and `handle_subagent_start`.
- **`Agent` matcher in `_HOOKS_CONFIG["PreToolUse"]`** so `coordinationhub init` installs the hook for Agent tool calls.
- **Fixed fixture `SubagentStart.json`** — rewritten to real captured shape (`agent_id`, `agent_type` top-level, no `tool_input`).
- **New fixture `PreToolUse_Agent.json`** for the new handler.
- **6 new functional tests** in `TestPreAgentAndSubagentShape` covering: PreToolUse[Agent] → SubagentStart happy path, no-pending-task graceful no-op, FIFO ordering within a subagent_type, bucketing across types (Explore + Plan don't collide), real agent_type appearing in generated hub IDs, `_subagent_type` helper unit test.

### Changed

- **`handle_subagent_start`** reads `agent_id` first with `subagent_id` fallback, calls `consume_pending_task(session_id, subagent_type)` to get the description instead of reading the nonexistent `tool_input.description`. Falls back to `event.tool_input.description` only if no pending task exists (keeps legacy unit-test fixtures passable during transition).
- **`main()` dispatch** branches on `tool_name == "Agent"` → `handle_pre_agent`.
- **`test_subagent_id_is_hex_string`** now reads `agent_id` (real field) and additionally asserts `agent_type` is present at the top level.
- **`_FIXTURE_HANDLERS["SubagentStart"]`** now requires `[hook_event_name, session_id, agent_id, agent_type]`.

### Live validation

After `coordinationhub init` and spawning an Explore agent with `description="LIVE-TEST-validate-v047-fix"`, sub-agents in the project DB:
```
hub.cc.046b7ee2-26a.Explore.0   stopped   LIVE-TEST-validate-v047-fix   ← post-fix
hub.cc.046b7ee2-26a.agent.3     stopped                                 ← pre-fix
hub.cc.046b7ee2-26a.agent.2     stopped                                 ← pre-fix
hub.cc.046b7ee2-26a.agent.1     stopped                                 ← pre-fix
hub.cc.046b7ee2-26a.agent.0     stopped                                 ← pre-fix
```
Same session, same DB, same Claude Code instance. The post-fix row carries the real `Explore` agent type in its ID and a populated `current_task` column.

### Why this escaped earlier reviews

The test suite's `TestEventContract` was meant to catch exactly this kind of drift — the docstring even says "Replace these with real captured events to catch schema drift." The `COORDINATIONHUB_CAPTURE_EVENTS=1` mechanism was added in v0.4.0 but nobody used it on `SubagentStart`. The fixture was written to an imagined shape in v0.3.7 when the subagent hook was first added, tests were built against the fixture, and the contract check became a self-referential loop. CLAUDE.md now carries a warning in the Key Design Decisions section: **never write fixtures without live capture**.

### Counts

- Tests: 328 → 336 collected (335 passing + 1 skipped). `test_hooks.py`: 58 → 66.
- Source: new `pending_tasks.py` (~105 LOC), `hooks/claude_code.py` 378 → 438 LOC, `cli_setup.py` 269 → 272 LOC, `db.py` 243 → 255 LOC.
- Hook events handled: 7 → 8 (`PreToolUse[Agent]` added).

---

## v0.4.6 Changelog — UserPromptSubmit Hook (Root Agent Task Visibility)

### Motivation

Sub-agents already had their `current_task` populated automatically (via `handle_subagent_start` reading the Agent tool's `description` field), but the root session agent had no equivalent path. A user running `coordinationhub watch` saw locks and notifications from the root agent but no narrative of what it was working on. Making the `watch` view actually useful for "who's doing what" requires the root agent's task column to be populated automatically, with no behavioral dependency.

Claude Code fires `UserPromptSubmit` on every user prompt and includes the prompt text in the event JSON. A hook handler that stamps the root agent's `current_task` from the prompt makes the two code paths symmetric and leaves the full agent tree self-documenting.

### Added

- **`handle_user_prompt_submit` in `hooks/claude_code.py`** — resolves the root agent via `_session_agent_id`, calls `_ensure_registered` (self-heals if SessionStart was missed), truncates the prompt to 120 chars with an ellipsis suffix, collapses multi-line whitespace, and calls `engine.update_agent_status(agent_id, current_task=summary)`. No-ops on empty/missing prompts. Wired into `main()` dispatch.
- **`_HOOKS_CONFIG["UserPromptSubmit"]` in `cli_setup.py`** — new default matcher block with `statusMessage: "Stamping current task"`. `coordinationhub init` merges it into `~/.claude/settings.json` via the existing `_merge_hooks` path without clobbering user hooks. `_check_hooks_config` now requires `UserPromptSubmit` in its check set, so `coordinationhub doctor` flags setups missing the hook.
- **`tests/fixtures/claude_code_events/UserPromptSubmit.json`** — contract fixture with the minimum shape (`hook_event_name`, `session_id`, `cwd`, `prompt`). Picked up automatically by the parametrized `TestEventContract` class.
- **6 new functional tests** in `TestUserPromptSubmit`: happy path, long-prompt truncation, multi-line whitespace collapse, empty-prompt no-op, latest-prompt-wins, self-heal when SessionStart was missed.

### Note for existing sessions

Claude Code reads `~/.claude/settings.json` at session start. Running `coordinationhub init` installs the hook into the on-disk config but does not retroactively attach it to sessions already open. New sessions populate on first prompt.

### Counts

- Tests: 320 → 328 collected (327 passing + 1 skipped). `test_hooks.py`: 50 → 58.
- Hook events handled: 6 → 7.
- `hooks/claude_code.py`: 352 → 378 LOC.

---

## v0.4.5 Changelog — Live-Session Assessment + Dead-Table Cleanup

### Motivation

Post-v0.4.4 audit surfaced two real issues. First, `run_assessment` was a use-case orphan: the scorers, MCP tool, and CLI subcommand all existed, but no suite JSON files existed anywhere in the repo, nothing called `run_assessment` automatically, and the v0.4.4 pipeline test had to hand-author a trace to exercise the feature end-to-end. The assessment runner was a complete implementation missing one piece — "how do you get from a live session to a scoreable trace?" Second, `coordination_context` was a dead table: defined in the v0.2.0 initial commit, never read or written, only referenced in a test that asserted its existence.

(`load_coordination_spec` and the graph system were audited at the same time and confirmed actively used — every engine start auto-loads the spec, populating `agent_responsibilities` which feeds `get_agent_tree`, `scan_project`, and context bundles.)

### Added

- **`build_trace_from_db(connect, trace_id, worktree_root)` in `assessment.py`** — synthesizes an assessment trace from live DB state. Reads `agents` LEFT JOIN `agent_responsibilities` for register events (with `graph_id` and `parent_id`), `change_notifications` where `change_type='modified'` for synthetic `lock → modified → unlock` triples, and `lineage` where parent and child have distinct graph roles for `handoff` events. Events sorted by timestamp; internal sort keys stripped before return.
- **`build_suite_from_db`** — one-call wrapper returning a `{name, traces: [...]}` dict.
- **`CoordinationEngine.assess_current_session(format, graph_agent_id, scope)`** — scores the live session. Refuses with a structured error if no coordination graph is loaded. `scope="project"` (default) filters to the engine's worktree; `scope="all"` scores every agent in the DB.
- **`assess_current_session` MCP tool** — new dispatch entry, schema, and `coordinationhub assess-session` CLI subcommand. MCP tool count 30 → 31.
- **9 new converter unit tests** in `test_assessment.py::TestBuildTraceFromDB` covering empty DB, single agents, graph_id/parent_id propagation, lock/modify triples, `indexed` changes being ignored, handoffs from lineage, same-role suppression, worktree filter, and suite wrapping.
- **2 new scenario tests** in `test_scenario.py`: `test_assess_current_session_from_live_db` (end-to-end hook-driven session → `assess_current_session` with no hand-built suite) and `test_assess_current_session_without_graph_returns_error` (no-graph error path).

### Removed

- `coordination_context` table removed from `db.py._SCHEMAS`. Existing DBs keep the empty table — no drop migration (a dropped table in a migration is harder to reason about than an ignored empty one). `test_db_migration.py` updated accordingly.

### Fixed

- `coordinationhub/__init__.py` `__version__` had stayed at `0.4.3` through v0.4.4 — a drift the CI version-consistency check catches. Now synced to `0.4.5`.
- `test_get_tools_returns_all_30_tools` and `test_status_via_http` hardcoded the tool count at 30. Replaced with `len(TOOL_DISPATCH)` so they track the dispatch table.

### Counts

- Tests: 309 → 320 collected (319 passing + 1 skipped). `test_assessment.py`: 24 → 33. `test_scenario.py`: 11 → 13.
- MCP tools: 30 → 31.
- CLI subcommands: 34 → 35.

---

## v0.4.4 Changelog — Close Review Thirteen (Assessment + Graph Pipeline Test)

### Context

Review Thirteen flagged six gaps from a 15-sub-agent RecipeLab run. Four were code bugs that had already been closed in earlier releases (SubagentStop status transition in v0.3.8, background dedup in v0.3.8, same-file contention test in v0.4.1, and file-ownership population in v0.4.0). The remaining two — assessment scoring and coordination-graph integration — had unit coverage but had never been exercised together through the real hook entry points.

### Added

- `test_coordination_graph_and_assessment_pipeline` in `tests/test_scenario.py` — end-to-end pipeline test: writes a coordination spec, loads it via `engine.load_coordination_spec`, spawns two sub-agents through `handle_subagent_start`/`handle_post_write`, resolves their `hub.cc.*` IDs via `find_agent_by_claude_id`, deregisters via `handle_subagent_stop`, builds a trace from hook-visible events, runs `engine.run_assessment`, and verifies all five metrics scored + results persisted to `assessment_results`. Closes Review Thirteen gaps 5 and 6 with a single test that fails locally on regression instead of only in a live review.

### Changed

- `pyproject.toml`: 0.4.3 → 0.4.4.
- Tests: 308 → 309 collected (308 passing + 1 skipped). `test_scenario.py`: 10 → 11.

### Not changed

- `coordinationhub/` source unchanged from v0.4.3. The four code gaps were already closed. The v0.4.4 delta is a scenario-level safety net, not a bug fix.

---

## v0.4.3 Changelog — Review Fourteen Root Cause

### Investigation

Review Fourteen reported three symptoms on a live swarm test: `agent-tree` errored with `no such column: region_start`, general-purpose sub-agents appeared not to land in the registry (parallel writes to the same file overwrote each other without contention), and `list-agents` vs `dashboard` disagreed on status. The reviewer inferred Claude Code was selectively firing `SubagentStart` for Explore but not general-purpose sub-agents.

Post-fix investigation disproved that inference. `~/.coordinationhub/hook.log` showed SubagentStart and SubagentStop events firing for general-purpose sub-agents during the Review Fourteen window — they just crashed inside `register_agent` with `table agents has no column named claude_agent_id`, and earlier the same day 300+ PreToolUse events had crashed with `no such column: region_start`. Every hook call was silently failing because the DB was in a "stuck-version" state: `schema_version=3` had been stamped by an earlier buggy init_schema path, but the v1→v2 and v2→v3 migrations had never actually executed, leaving tables at their v1 shape. A live test after the DB fix (spawning a general-purpose sub-agent from Claude Code) registered correctly as `hub.cc.{session}.agent.0` with the claude_agent_id mapping populated and parent linked — confirming SubagentStart does fire for all sub-agent types. The symptoms were all downstream of the DB bug.

### Fixed
- **`init_schema` is now idempotent and self-healing.** Earlier code stamped `schema_version` in a fresh-install branch that was a no-op on existing tables, producing "stuck-version" DBs where the recorded version advanced but the underlying tables stayed at v1. `init_schema` now always runs every migration in version order (each is idempotent via `PRAGMA table_info` checks) and creates indexes after migrations. Legacy DBs, partially migrated DBs, and DBs with bogus version stamps all converge on the latest schema. This is the root-cause fix for Review Fourteen — once the hook stops crashing, sub-agent registration, lock acquisition, file ownership, and SubagentStop all work on their own.
- **`list-agents` and `dashboard` output now agrees.** Both CLI commands call `reap_stale_agents` before querying so a stale active-in-DB agent is reaped once, and both commands render it as stopped thereafter.

### Added
- `tests/test_db_migration.py` — 7 tests covering pre-v0.3.3 legacy DBs, stuck-version DBs (the exact broken state found in the project), and fresh installs.
- 3 `TestListAgentsDashboardConsistency` tests in `test_cli.py` — stale-agent seeding + cross-command consistency.

### Changed
- `coordinationhub/db.py`: `init_schema` rewritten to run all migrations unconditionally and create indexes after migrations.
- `coordinationhub/cli_agents.py`, `coordinationhub/cli_vis.py`: auto-reap before list/dashboard output.
- Tests: 297 → 308 across 17 files.

### Not changed (explicitly)
- **Hooks/claude_code.py is unchanged from v0.4.2.** An earlier iteration of this commit added an `auto_register` fallback to `_resolve_agent_id` that would create a `{parent}.auto.{hex[:6]}` child when a sub-agent's SubagentStart appeared to have been skipped. Empirical testing showed SubagentStart fires for every sub-agent type — the apparent skip was the hook crashing on the stuck-version DB. The fallback was removed to avoid designing for a hypothetical scenario.

---

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

The table below is auto-generated by `scripts/gen_docs.py` from source
files in `coordinationhub/`. Run the script after any file change to
keep it in sync; CI checks for drift on every push.

<!-- GEN:file-inventory -->
| Path | LOC | Purpose |
|------|-----|---------|
| `coordinationhub/__init__.py` | 14 | CoordinationHub — multi-agent swarm coordination MCP server |
| `coordinationhub/_storage.py` | 101 | Storage backend for CoordinationHub — SQLite pool, path resolution, lifecycle |
| `coordinationhub/agent_registry.py` | 292 | Agent lifecycle: register, heartbeat, deregister, lineage management |
| `coordinationhub/agent_status.py` | 262 | Agent status and file-map query helpers for CoordinationHub |
| `coordinationhub/assessment.py` | 322 | Assessment runner for CoordinationHub coordination test suites |
| `coordinationhub/assessment_scorers.py` | 237 | Assessment metric scorers for CoordinationHub |
| `coordinationhub/cli.py` | 182 | CoordinationHub CLI — command-line interface for all 31 coordination tool methods |
| `coordinationhub/cli_agents.py` | 127 | Agent identity and lifecycle CLI commands |
| `coordinationhub/cli_commands.py` | 48 | CoordinationHub CLI command handlers |
| `coordinationhub/cli_locks.py` | 158 | Document locking and coordination CLI commands |
| `coordinationhub/cli_setup.py` | 272 | CLI commands for setup and diagnostics: doctor, init, watch |
| `coordinationhub/cli_utils.py` | 21 | Shared CLI helper functions used by all cli_* sub-modules |
| `coordinationhub/cli_vis.py` | 290 | Change awareness, audit, graph, and assessment CLI commands |
| `coordinationhub/conflict_log.py` | 44 | Conflict recording and querying for CoordinationHub |
| `coordinationhub/context.py` | 91 | Context bundle builder for CoordinationHub agent registration responses |
| `coordinationhub/core.py` | 281 | CoordinationEngine — core business logic for CoordinationHub |
| `coordinationhub/core_locking.py` | 269 | Locking and coordination methods for CoordinationEngine |
| `coordinationhub/db.py` | 266 | SQLite schema, migrations, and connection pool for CoordinationHub |
| `coordinationhub/dispatch.py` | 38 | Tool dispatch table for CoordinationHub |
| `coordinationhub/graphs.py` | 256 | Declarative coordination graph: loader, validator, in-memory representation |
| `coordinationhub/hooks/__init__.py` | 1 | Hooks package — Claude Code integration via stdin/stdout event protocol |
| `coordinationhub/hooks/claude_code.py` | 438 | CoordinationHub hook for Claude Code |
| `coordinationhub/lock_ops.py` | 191 | Shared lock primitives used by both local locks and coordination locks |
| `coordinationhub/mcp_server.py` | 209 | HTTP-based MCP server for CoordinationHub — zero external dependencies |
| `coordinationhub/mcp_stdio.py` | 142 | Stdio-based MCP server for CoordinationHub using the ``mcp`` Python package |
| `coordinationhub/notifications.py` | 81 | Change notification storage and retrieval for CoordinationHub |
| `coordinationhub/paths.py` | 38 | Path normalization and project-root detection utilities |
| `coordinationhub/pending_tasks.py` | 105 | Pending sub-agent task storage for CoordinationHub |
| `coordinationhub/scan.py` | 198 | File ownership scan for CoordinationHub |
| `coordinationhub/schemas.py` | 675 | Tool schemas for CoordinationHub — all 31 MCP tools |
<!-- /GEN -->

**Total: <!-- GEN:test-count -->341<!-- /GEN --> tests across 16 test files.**

---

## Architecture

<!-- GEN:directory-tree -->
```
coordinationhub/
  __init__.py           — CoordinationHub — multi-agent swarm coordination MCP server (~14 LOC)
  _storage.py           — Storage backend for CoordinationHub — SQLite pool, path resolution, lifecycle (~101 LOC)
  agent_registry.py     — Agent lifecycle: register, heartbeat, deregister, lineage management (~292 LOC)
  agent_status.py       — Agent status and file-map query helpers for CoordinationHub (~262 LOC)
  assessment.py         — Assessment runner for CoordinationHub coordination test suites (~322 LOC)
  assessment_scorers.py — Assessment metric scorers for CoordinationHub (~237 LOC)
  cli.py                — CoordinationHub CLI — command-line interface for all 31 coordination tool methods (~182 LOC)
  cli_agents.py         — Agent identity and lifecycle CLI commands (~127 LOC)
  cli_commands.py       — CoordinationHub CLI command handlers (~48 LOC)
  cli_locks.py          — Document locking and coordination CLI commands (~158 LOC)
  cli_setup.py          — CLI commands for setup and diagnostics: doctor, init, watch (~272 LOC)
  cli_utils.py          — Shared CLI helper functions used by all cli_* sub-modules (~21 LOC)
  cli_vis.py            — Change awareness, audit, graph, and assessment CLI commands (~290 LOC)
  conflict_log.py       — Conflict recording and querying for CoordinationHub (~44 LOC)
  context.py            — Context bundle builder for CoordinationHub agent registration responses (~91 LOC)
  core.py               — CoordinationEngine — core business logic for CoordinationHub (~281 LOC)
  core_locking.py       — Locking and coordination methods for CoordinationEngine (~269 LOC)
  db.py                 — SQLite schema, migrations, and connection pool for CoordinationHub (~266 LOC)
  dispatch.py           — Tool dispatch table for CoordinationHub (~38 LOC)
  graphs.py             — Declarative coordination graph: loader, validator, in-memory representation (~256 LOC)
  lock_ops.py           — Shared lock primitives used by both local locks and coordination locks (~191 LOC)
  mcp_server.py         — HTTP-based MCP server for CoordinationHub — zero external dependencies (~209 LOC)
  mcp_stdio.py          — Stdio-based MCP server for CoordinationHub using the ``mcp`` Python package (~142 LOC)
  notifications.py      — Change notification storage and retrieval for CoordinationHub (~81 LOC)
  paths.py              — Path normalization and project-root detection utilities (~38 LOC)
  pending_tasks.py      — Pending sub-agent task storage for CoordinationHub (~105 LOC)
  scan.py               — File ownership scan for CoordinationHub (~198 LOC)
  schemas.py            — Tool schemas for CoordinationHub — all 31 MCP tools (~675 LOC)
  hooks/
    __init__.py         — Hooks package — Claude Code integration via stdin/stdout event protocol (~1 LOC)
    claude_code.py      — CoordinationHub hook for Claude Code (~438 LOC)
```
<!-- /GEN -->

The `tests/` directory holds <!-- GEN:test-count -->341<!-- /GEN --> tests across 16 files,
plus `tests/fixtures/claude_code_events/` for hook contract fixtures.

**Module design principles:**
- Zero internal deps in sub-modules: each receives `connect: ConnectFn` from the caller.
- Storage layer isolated in `_storage.py`: both `core.py` and CLI entry points depend on it; sub-modules have no path to `core`.
- Thread-local connection pool: `db.py` gives each thread its own SQLite connection (WAL mode, 30s busy timeout).
- Dispatch separation: `schemas.py` (all 30 tool schemas as pure data) and `dispatch.py` (dispatch table) shared by HTTP + stdio servers.
- `connect` callable pattern: `agent_registry.py`, `lock_ops.py`, `conflict_log.py`, `notifications.py`, `scan.py`, `agent_status.py` all receive `connect` rather than importing `_db.connect`.

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

## MCP Tools (<!-- GEN:tool-count -->31<!-- /GEN --> total)

Full list auto-generated from `coordinationhub/schemas.py`:

<!-- GEN:mcp-tools -->
| Tool | Description |
|------|-------------|
| `register_agent` | Register an agent with the coordination hub and receive a context bundle containing sibling agents, active locks, coo... |
| `heartbeat` | Send a heartbeat to keep the agent registered and alive |
| `deregister_agent` | Deregister an agent, orphan its children to the grandparent, and release all its locks |
| `list_agents` | List all registered agents |
| `get_lineage` | Get the ancestor chain (parent → grandparent) and all descendants (direct children, grandchildren) of a given agent |
| `get_siblings` | Get all agents that share the same parent as the given agent |
| `acquire_lock` | Acquire an exclusive or shared lock on a document path or region |
| `release_lock` | Release a held lock |
| `refresh_lock` | Extend a lock's TTL without releasing and re-acquiring it |
| `get_lock_status` | Check if a document is currently locked and by whom |
| `list_locks` | List all active (non-expired) locks |
| `release_agent_locks` | Release all locks held by a given agent (including region locks) |
| `reap_expired_locks` | Clear all expired locks from the lock table |
| `reap_stale_agents` | Mark stale agents as stopped and release their locks |
| `broadcast` | Announce an intention to all live sibling agents before taking an action |
| `wait_for_locks` | Poll until all specified locks are released or a timeout expires |
| `notify_change` | Record a change event so other agents can poll for it |
| `get_notifications` | Poll for change notifications since a timestamp |
| `prune_notifications` | Clean up old notifications by age or entry count |
| `get_conflicts` | Query the conflict log for lock steals and ownership violations |
| `get_contention_hotspots` | Rank files by lock contention frequency |
| `status` | Get a summary of the coordination system state: registered agents, active locks, pending notifications, conflicts, an... |
| `load_coordination_spec` | Reload the coordination spec from disk |
| `validate_graph` | Validate the currently loaded coordination graph schema |
| `scan_project` | Perform a file ownership scan of the worktree_root |
| `get_agent_status` | Get full status for a specific agent: current task, responsibilities (from the coordination graph), owned files, line... |
| `get_file_agent_map` | Get a map of all tracked files to their assigned Agent ID and responsibility summary |
| `update_agent_status` | Update the current task description for an agent |
| `run_assessment` | Run an assessment suite against the loaded coordination graph |
| `assess_current_session` | Score the current live session against the loaded coordination graph |
| `get_agent_tree` | Get the hierarchical agent tree with live work status |
<!-- /GEN -->

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
# <!-- GEN:test-count -->341<!-- /GEN --> tests across 16 test files
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
