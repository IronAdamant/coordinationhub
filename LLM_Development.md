# LLM_Development.md — CoordinationHub

**Version:** <!-- GEN:version -->0.4.6<!-- /GEN -->
**Last updated:** 2026-04-11

## Change Log

All significant changes to the CoordinationHub project are documented here in reverse chronological order.

---

## 2026-04-11 — v0.4.6 UserPromptSubmit Hook — Root Agent Task Visibility

### Motivation

After v0.4.5 shipped the live-session assessment runner, an audit question surfaced: does the main Claude Code agent actually show *what it's working on* in `get_agent_tree` / `coordinationhub watch`? The answer was no. Sub-agents had their `current_task` column populated automatically by `handle_subagent_start` from the Agent tool's `description` field, but the root session agent had no equivalent path. Its task column stayed `NULL` even while it held file locks and fired `notify_change` events. A user running `coordinationhub watch` in a sidecar terminal would see "the root agent exists and is touching these files" but not "here's what the user asked it to do."

The fix is a new `UserPromptSubmit` hook. Claude Code fires this event every time the user submits a prompt, with the prompt text in the event JSON. A handler that stamps the root agent's `current_task` from the prompt makes the two paths symmetric: sub-agents get their task from the Agent spawn description, the root agent gets its task from the user prompt.

This is passive and automatic — no behavioral dependency on me remembering to call `update_agent_status`. Just install the hook once and every future session populates itself.

### Added

**`handle_user_prompt_submit(event)` in `hooks/claude_code.py`**:

- Reads `event["prompt"]`, strips whitespace, and bails on empty/missing prompts (so the hook stays a no-op if Claude Code ever sends an empty submit event or changes the field name).
- Truncates to 120 characters with an ellipsis suffix to keep the agent tree rendering compact.
- Collapses multi-line whitespace so multi-line prompts render as a single line in `text_tree` output.
- Resolves the root agent via `_session_agent_id(session_id)`, calls `_ensure_registered` (so the hook self-heals if SessionStart was somehow missed), then `engine.update_agent_status(agent_id, current_task=summary)`.
- Wired into `main()` dispatch between SessionStart and PreToolUse.

**`_HOOKS_CONFIG["UserPromptSubmit"]` entry in `cli_setup.py`**:

- New matcher block with `statusMessage: "Stamping current task"`. Picked up by `coordinationhub init` via the existing `_merge_hooks` path, so re-running `init` installs the new hook into `~/.claude/settings.json` without clobbering other hooks.
- `_check_hooks_config()` (the doctor check) now includes `UserPromptSubmit` in its required set, so setups missing the hook show up as FAIL in `coordinationhub doctor`.

**Contract fixture `tests/fixtures/claude_code_events/UserPromptSubmit.json`** with the minimum shape the handler reads: `hook_event_name`, `session_id`, `cwd`, `prompt`. Picked up by the parametrized `TestEventContract` class, which now runs two additional invocations per test (required-fields check + handler-does-not-crash).

**6 new functional tests** in `test_hooks.py::TestUserPromptSubmit`:
- `test_sets_current_task_from_prompt` — happy path, verifies `current_task` equals the prompt text
- `test_truncates_long_prompts` — 500-char prompt → truncated with `...` suffix, ≤120 chars
- `test_collapses_multiline_whitespace` — multi-line prompt renders as a single line
- `test_empty_prompt_is_noop` — empty/whitespace prompt does not overwrite the previous task
- `test_latest_prompt_overwrites_previous` — second prompt replaces the first
- `test_registers_root_when_session_start_missed` — hook self-heals if SessionStart didn't fire

### Note on existing sessions

Claude Code reads `~/.claude/settings.json` at session start. Running `coordinationhub init` installs the hook into the on-disk config but does not retroactively add it to sessions that are already open. The session this commit was authored in showed `current_task: null` for the root agent because the session predated the hook. New sessions (and any existing session that restarts) will populate on the first user prompt.

### Counts

- Version: 0.4.5 → 0.4.6
- Tests: 320 → 328 collected (327 passing + 1 skipped). `test_hooks.py`: 50 → 58 (+6 functional + 2 parametrized contract invocations for the new fixture).
- Source LOC: `hooks/claude_code.py` 352 → 378 (+26 for the new handler). `cli_setup.py` 268 → 269 (+1 for the hook config entry).
- Hook events handled: 6 → 7.

---

## 2026-04-11 — v0.4.5 Live-Session Assessment + Dead-Table Cleanup

### Motivation

Post-v0.4.4 audit surfaced two real issues (and correctly discarded one false alarm):

1. **`run_assessment` had no production consumer.** The engine method, 5 metric scorers, 33 unit tests, MCP tool schema, and `coordinationhub assess --suite <file>` CLI command all existed and worked. But no suite JSON files existed anywhere in the repo, nothing called `run_assessment` automatically, and using it required a user to hand-author a trace suite describing a session after the fact. The v0.4.4 pipeline test had to manually construct a trace to exercise the scorers. The feature was a complete implementation missing exactly one piece: "how do you get from a live session to a scoreable trace?"
2. **`coordination_context` table was dead.** Defined in `db.py._SCHEMAS` since the v0.2.0 initial commit (`git log -S"coordination_context"` returns one result — that commit). Zero reads, zero writes, ever. The only reference outside `db.py` was `test_db_migration.py` asserting the table exists on a fresh DB — a test enforcing the deadness rather than exercising a live use.

`load_coordination_spec` and the graph system were audited at the same time and confirmed to be actively used: `CoordinationEngine.start()` auto-loads the spec on every engine creation (which every hook call triggers), the loaded graph populates `agent_responsibilities` via `_populate_agent_responsibilities_from_graph`, and that table feeds `get_agent_tree`, `scan_project` responsibility inheritance, context bundles, and boundary warnings. Not dead — the opposite of dead.

### Added

**`build_trace_from_db(connect, trace_id, worktree_root=None)` in `assessment.py`** — synthesizes an assessment trace from live DB state. Reads three tables:

- **`agents`** LEFT JOIN `agent_responsibilities` → `register` events carrying `agent_id`, `graph_id` (when a role is mapped), and `parent_id`.
- **`change_notifications`** where `change_type='modified'` → synthetic `lock → modified → unlock` triples so `score_outcome_verifiability` has data to work with. Hooks never emit explicit unlock events, so the converter fabricates them from the fact that each write completed. Microsecond offsets keep the triple in-order even when merged with register events that share a timestamp.
- **`lineage`** where parent and child have distinct graph roles → `handoff` events. Children that inherit the same role as their parent are correctly suppressed.

Events are sorted by timestamp with internal `_ts` sort keys stripped before return, so the output only contains fields documented in the scorer interface.

**`build_suite_from_db(connect, suite_name, worktree_root=None)`** — one-call wrapper that returns `{"name": ..., "traces": [<one trace>]}`.

**`CoordinationEngine.assess_current_session(format, graph_agent_id, scope)`** — scores the live session. Refuses with a clear error if no coordination graph is loaded (rather than returning vacuous-1.0 scores). `scope="project"` (default) filters to the engine's worktree; `scope="all"` scores every agent in the DB.

**`assess_current_session` MCP tool** — new dispatch entry, schema, and `assess-session` CLI subcommand with `--format`, `--graph-agent-id`, `--scope`, and `--output` flags. Tool count 30 → 31.

### Removed

- `coordination_context` table removed from `db.py._SCHEMAS`. Existing DBs keep the empty table (no drop migration — it would be pure churn, and a dropped table in a migration is harder to reason about than a lingering empty one). `test_db_migration.py` updated to no longer assert its existence.

### Added Tests

- **9 new unit tests** in `test_assessment.py::TestBuildTraceFromDB` — empty DB, single agent with no writes, graph_id + parent_id propagation, lock/modified/unlock triples, `indexed` change type is ignored, handoffs from lineage with distinct roles, no handoffs for same-role children, worktree_root filter, suite wrapping.
- **2 new scenario tests** in `test_scenario.py::TestHookLevelMultiAgentScenario`:
  - `test_assess_current_session_from_live_db` — drives multi-agent hooks, tags sub-agents with graph roles, then calls `assess_current_session` with no hand-built suite. Asserts all 5 metrics scored, `outcome_verifiability > 0` (the synthesized lock/modify pairs are not vacuous), and results persisted.
  - `test_assess_current_session_without_graph_returns_error` — verifies the no-graph path returns a structured error instead of silent 1.0s.

### Fixed (incidental)

- `coordinationhub/__init__.py` `__version__` had stayed at `0.4.3` through v0.4.4 (the CI version-consistency check caught this). Now synced to `0.4.5`.
- Two integration tests (`test_get_tools_returns_all_30_tools`, `test_status_via_http`) hardcoded the tool count at 30. Replaced with `len(TOOL_DISPATCH)` so they track the dispatch table automatically.

### Counts

- Version: 0.4.4 → 0.4.5
- Tests: 309 → 320 collected (319 passing + 1 skipped). `test_assessment.py`: 24 → 33. `test_scenario.py`: 11 → 13.
- MCP tools: 30 → 31 (`assess_current_session` added).
- CLI subcommands: 34 → 35 (`assess-session` added).

---

## 2026-04-11 — v0.4.4 Close Review Thirteen (Assessment + Coordination Graph Pipeline Test)

### Context

Review Thirteen (2026-04-11, RecipeLab, 15 sub-agents across two parallel waves) flagged six gaps. Four were code bugs that had already been fixed in earlier releases; two were untested feature-integration paths that only had unit coverage:

| # | Gap | Closed by | Status at v0.4.3 |
|---|-----|-----------|------------------|
| 1 | SubagentStop did not transition agents to `stopped` | v0.3.8 (`_resolve_agent_id` in `handle_subagent_stop`) | Code + `test_subagent_stop_sets_status_stopped_via_claude_id` |
| 2 | `run_in_background` agents registered twice | v0.3.8 (`find_agent_by_claude_id` dedup in `handle_subagent_start`) | Code + `test_background_agent_dedup` |
| 3 | No same-file lock contention test | v0.4.1 (`test_concurrent_contention_on_same_file` in `test_scenario.py`) | Test |
| 4 | `file_ownership` table not populated | v0.4.0 (`handle_post_write` → `claim_file_ownership`) | Code + `test_file_ownership_first_write_wins` + `test_wave_of_subagents_full_lifecycle` |
| 5 | Assessment scoring never exercised end-to-end | v0.4.4 (`test_coordination_graph_and_assessment_pipeline`) | Covered this release |
| 6 | Coordination graph integration never exercised end-to-end | v0.4.4 (same test) | Covered this release |

Gaps 5 and 6 had comprehensive unit coverage (`test_assessment.py` × 24, `test_graphs.py` × 22, `test_visibility.py` graph loading × multiple), but no single scenario test walked the entire pipeline — load a spec, drive sub-agent activity through the real hook entry points, resolve `hub.cc.*` IDs for the resulting agents, then feed a trace through `run_assessment` with results persisted to SQLite.

### Added

**`test_coordination_graph_and_assessment_pipeline`** in `tests/test_scenario.py`:

1. Writes a two-agent `coordination_spec.json` (planner + builder with a handoff) into the project root.
2. Fires `handle_session_start`, then calls `engine.load_coordination_spec(spec_path)` — asserts `loaded=True` and both agents appear in the returned manifest.
3. Spawns two sub-agents via `handle_subagent_start`, has each call `handle_post_write` on a unique file — exercises the full hook path including `claim_file_ownership`.
4. Resolves the `hub.cc.*` IDs via `find_agent_by_claude_id` **before** deregistering (the lookup filters by `status = 'active'`).
5. Deregisters both via `handle_subagent_stop`.
6. Builds a trace suite mirroring the session (`register`, `lock`, `modified`, `handoff` events) and calls `engine.run_assessment(suite_path, format="json")`.
7. Asserts the result has `graph_loaded=True`, all five metrics scored, overall score in (0, 1], and that `assessment_results` rows were persisted (one per metric).

This is a single test, but it traverses: `load_coordination_spec` → hook handlers → `find_agent_by_claude_id` → `run_assessment` → `store_assessment_results`. Any regression in that pipeline will now fail locally instead of only showing up in a live RecipeLab review.

### Files Changed

- `tests/test_scenario.py`: +1 test (`TestHookLevelMultiAgentScenario` now has 6 tests, up from 5).
- `pyproject.toml`: 0.4.3 → 0.4.4.

### Counts

- Tests: 308 → 309 collected (308 passing + 1 skipped).
- Files: unchanged.

---

## 2026-04-11 — v0.4.3 Review Fourteen Root Cause

### Investigation

Review Fourteen (conducted on RecipeLab_alt, 2026-04-11) reported three
symptoms on a live swarm test:

1. `agent-tree` errored with `no such column: region_start`.
2. Parallel general-purpose sub-agent writes to the same file silently
   overwrote each other; neither sub-agent appeared in the CoordinationHub
   registry. The reviewer concluded SubagentStart was not firing for
   general-purpose sub-agents (only for Explore).
3. `list-agents` and `dashboard` disagreed on agent status.

The initial fix assumed symptom (2) was a Claude Code hook-coverage gap
and added an `auto_register` fallback in `_resolve_agent_id`. Post-fix
investigation invalidated that assumption:

* `~/.coordinationhub/hook.log` showed **SubagentStart and SubagentStop
  events firing for general-purpose sub-agents** on 2026-04-11 — they
  just crashed inside `register_agent` with `table agents has no column
  named claude_agent_id`. Earlier the same day, 300+ PreToolUse calls
  had crashed with `no such column: region_start`. Every hook call had
  been silently failing for hours.
* The DB was in a "stuck-version" state: `schema_version=3` stamped by
  an earlier buggy `init_schema` path that ran a no-op fresh-install
  branch on existing tables, then recorded the version. The actual
  v1→v2 and v2→v3 migrations never ran, so tables stayed at v1 while
  the recorded version advanced.
* A live test after the DB fix (spawning a general-purpose sub-agent
  from Claude Code) registered correctly as `hub.cc.{session}.agent.0`
  with `claude_agent_id` populated and `parent_id` linked. Proving
  SubagentStart fires for every sub-agent type.

All three symptoms were downstream of the DB bug. The `auto_register`
fallback was reverted — it was solving a problem that didn't exist.

### Fixed

**`db.py init_schema` is now idempotent and self-healing.** Every
call:

1. Creates `schema_version` if missing.
2. Runs `CREATE TABLE IF NOT EXISTS` for every table in the latest
   shape (no-op on existing tables, adds any missing tables for
   legacy DBs).
3. Runs **every** migration in version order unconditionally — each
   migration checks `PRAGMA table_info` and skips work already applied,
   so stuck DBs stamped at v3 but still shaped like v1 get their
   `document_locks` restructured and `agents.claude_agent_id` added
   on the next call.
4. Creates all indexes after migrations, so index DDL always references
   the latest column set.
5. Overwrites `schema_version` with `_CURRENT_SCHEMA_VERSION`.

This is the root-cause fix. Once hooks stop crashing, sub-agent
registration, lock acquisition, file ownership, and SubagentStop all
work without further intervention.

**`cmd_list_agents` and `cmd_dashboard` both call `reap_stale_agents`
before querying** so their output converges on the same DB state.
Previously a stale agent with `status='active'` and an old heartbeat
would render as `active (STALE)` in list-agents but `[stopped]` in
dashboard (or vice versa) depending on which command last ran.

### Added

- `tests/test_db_migration.py` — 7 tests for legacy, stuck-version, and
  fresh-install schema paths. Includes the exact broken DB state found
  in the project (`schema_version=3` with `document_locks` in v1 shape).
- `TestListAgentsDashboardConsistency` in `tests/test_cli.py` — 3 tests
  that seed a stale agent, run each CLI command, and verify the DB is
  left in the same state.

### Reverted from the intermediate fix

An earlier iteration of this commit added `_looks_like_raw_claude_id`,
an `auto_register` parameter to `_resolve_agent_id`, and 5 tests in
`TestAutoRegisterUnmappedSubagent`. All of it was removed once the DB
fix turned out to be sufficient. The hook file matches v0.4.2 exactly.

### Test Count

297 → 308 tests (+11, across 17 files). All passing.

---

## 2026-04-11 — v0.4.2 Auto-Generated Doc Sections

### Motivation

Documentation drift was compounding: file inventory tables, directory trees, test counts, and tool counts appeared in 5 docs and needed manual updating on every change. Three times in this session I caught drift after-the-fact.

### Added

**`scripts/gen_docs.py`** — stdlib-only script (~230 LOC) that scans `coordinationhub/` and rewrites marker blocks in target docs. Six generators:
- `file-inventory` — Markdown table with path, LOC, and module docstring first-line
- `directory-tree` — ASCII tree grouped by directory with per-file LOC
- `mcp-tools` — Table of all MCP tools with descriptions (auto-extracted from `TOOL_SCHEMAS`)
- `test-count` — Integer count from `pytest --collect-only`
- `tool-count` — Integer count from `len(TOOL_SCHEMAS)`
- `version` — Version string from `pyproject.toml`

Modes:
- `python scripts/gen_docs.py` — rewrite in place
- `python scripts/gen_docs.py --check` — exit 1 on drift (CI mode)

**CI drift check** in `.github/workflows/test.yml` — runs `gen_docs.py --check` and fails the build if any doc is out of date.

### Marker conventions

Block markers for multi-line content:
```markdown
<!-- GEN:file-inventory -->
| Path | LOC | Purpose |
|------|-----|---------|
| `coordinationhub/__init__.py` | 14 | CoordinationHub — multi-agent swarm coordination MCP server |
| `coordinationhub/_storage.py` | 101 | Storage backend for CoordinationHub — SQLite pool, path resolution, lifecycle |
| `coordinationhub/agent_registry.py` | 231 | Agent lifecycle: register, heartbeat, deregister, lineage management |
| `coordinationhub/agent_status.py` | 262 | Agent status and file-map query helpers for CoordinationHub |
| `coordinationhub/assessment.py` | 322 | Assessment runner for CoordinationHub coordination test suites |
| `coordinationhub/assessment_scorers.py` | 237 | Assessment metric scorers for CoordinationHub |
| `coordinationhub/cli.py` | 182 | CoordinationHub CLI — command-line interface for all 31 coordination tool methods |
| `coordinationhub/cli_agents.py` | 127 | Agent identity and lifecycle CLI commands |
| `coordinationhub/cli_commands.py` | 48 | CoordinationHub CLI command handlers |
| `coordinationhub/cli_locks.py` | 158 | Document locking and coordination CLI commands |
| `coordinationhub/cli_setup.py` | 269 | CLI commands for setup and diagnostics: doctor, init, watch |
| `coordinationhub/cli_utils.py` | 21 | Shared CLI helper functions used by all cli_* sub-modules |
| `coordinationhub/cli_vis.py` | 290 | Change awareness, audit, graph, and assessment CLI commands |
| `coordinationhub/conflict_log.py` | 44 | Conflict recording and querying for CoordinationHub |
| `coordinationhub/context.py` | 88 | Context bundle builder for CoordinationHub agent registration responses |
| `coordinationhub/core.py` | 280 | CoordinationEngine — core business logic for CoordinationHub |
| `coordinationhub/core_locking.py` | 269 | Locking and coordination methods for CoordinationEngine |
| `coordinationhub/db.py` | 243 | SQLite schema, migrations, and connection pool for CoordinationHub |
| `coordinationhub/dispatch.py` | 38 | Tool dispatch table for CoordinationHub |
| `coordinationhub/graphs.py` | 256 | Declarative coordination graph: loader, validator, in-memory representation |
| `coordinationhub/hooks/__init__.py` | 1 | Hooks package — Claude Code integration via stdin/stdout event protocol |
| `coordinationhub/hooks/claude_code.py` | 378 | CoordinationHub hook for Claude Code |
| `coordinationhub/lock_ops.py` | 191 | Shared lock primitives used by both local locks and coordination locks |
| `coordinationhub/mcp_server.py` | 209 | HTTP-based MCP server for CoordinationHub — zero external dependencies |
| `coordinationhub/mcp_stdio.py` | 142 | Stdio-based MCP server for CoordinationHub using the ``mcp`` Python package |
| `coordinationhub/notifications.py` | 81 | Change notification storage and retrieval for CoordinationHub |
| `coordinationhub/paths.py` | 38 | Path normalization and project-root detection utilities |
| `coordinationhub/scan.py` | 198 | File ownership scan for CoordinationHub |
| `coordinationhub/schemas.py` | 675 | Tool schemas for CoordinationHub — all 31 MCP tools |
<!-- /GEN -->
```

Inline markers for single values (render invisibly in Markdown):
```markdown
This project has <!-- GEN:test-count -->328<!-- /GEN --> tests.
```

Unknown marker names raise an error during rewrite (catches typos).

### What stays human

- README.md prose, quickstart, feature pitch — hand-maintained
- CLAUDE.md "Module Design" and "Key Design Decisions" narrative sections
- LLM_Development.md changelog entries
- All "why" discussions, trade-off notes, and examples

### Files changed

- New: `scripts/gen_docs.py`
- Modified: `.github/workflows/test.yml` (added drift check), all 5 doc targets (markers added), `coordinationhub/hooks/__init__.py` (added docstring so auto-gen shows summary)

### Counts

- Version: 0.4.1 → 0.4.2
- Tests: unchanged (298 collected, 297 passing + 1 skipped)

---

## 2026-04-11 — v0.4.1 Close Validation Gap: Hook-Level Integration Tests, Contract Test Hardening

### Motivation

Post-v0.4.0 assessment identified three weak points: contract tests were still synthetic (never validated against real events), `reap_expired_locks` had misleading semantics after the smart-reap addition, and the fundamental validation gap (unit tests vs. real concurrent agents) remained unaddressed.

### Fixed

**Hook-level integration test (`tests/test_scenario.py`):** New `TestHookLevelMultiAgentScenario` class (4 tests) drives the real Claude Code hook handlers in end-to-end concurrent workflows:
- `test_wave_of_subagents_full_lifecycle` — 11 sub-agents register, write, stop with full attribution (mirrors Review Thirteen batch 2)
- `test_concurrent_contention_on_same_file` — two hook handlers race on same file via threading; exactly one wins
- `test_smart_reap_survives_long_model_call` — verifies smart reap refreshes instead of deleting when agent has recent heartbeat
- `test_crashed_agent_locks_reaped` — verifies smart reap still deletes locks held by stale agents

**Contract tests strengthened (`tests/test_hooks.py`):**
- Per-event-type required-field checks via dotted-path (`tool_input.file_path`, `tool_input.subagent_type`)
- Hex-format assertion on `SubagentStart.subagent_id` (Claude Code hex string format)
- New `TestEventCapture` class (2 tests) validates `_save_event_snapshot` writes real files and fails open on I/O errors

**`reap_expired_locks` semantics clarified (`coordinationhub/core_locking.py`):** Engine method now carries a docstring explicitly noting that `agent_grace_seconds > 0` refreshes instead of deleting. Name not changed (would break MCP tool contract and require schema migration for cosmetic fix).

### Audit finding (no code change)

**`hooks/claude_code.py` at ~450 LOC is not a structural problem.** Every function in the file was audited: 1 error logger, 1 event-capture helper, 5 shared helpers, 8 event handlers, 1 dispatch. No dead code, clear section headers. Splitting would create artificial file boundaries that v0.4.0 explicitly removed. Flag withdrawn.

### Counts

- Tests: 290 → 297 (+7: 4 integration, 2 capture, 1 contract format)
- Version: 0.4.0 → 0.4.1

---

## 2026-04-11 — v0.4.0 Architectural Cleanup: Consolidation, Smart Reap, File Ownership, Contract Tests

### Motivation

Post-Review-Thirteen assessment identified 5 architectural issues:
1. **Module count too high** — 13 files existed solely as re-export aggregators or artificial splits driven by the 500 LOC rule.
2. **Version numbering drift** — `__init__.py` and `pyproject.toml` fell out of sync across releases.
3. **No integration tests against real Claude Code events** — synthetic event dicts didn't validate the actual hook contract.
4. **TTL locks expired mid-operation** — 120s TTL was too short for slow model calls, with no refresh mechanism.
5. **File ownership table was dead** — populated only by manual `scan_project` calls, never by hooks.

### Changes

**Module consolidation (13 files deleted):**
- `registry_ops.py` + `registry_query.py` → merged into `agent_registry.py` (~290 LOC)
- 6 `schemas_*.py` files → merged into `schemas.py` (~590 LOC, pure data with group headers)
- `graph.py` + `graph_validate.py` + `graph_loader.py` → merged into `graphs.py` (~330 LOC; also fixes missing `Any` import)
- `responsibilities.py` → inlined into `scan.py`; `visibility.py` (pure re-export) removed, `core.py` imports `agent_status` and `scan` directly
- Net: 42 → 29 Python files in `coordinationhub/`. No external consumers depended on the deleted files.

**Version consistency CI check:**
- Added step to `.github/workflows/test.yml` that extracts version from both `pyproject.toml` and `__init__.py` and fails if they differ.

**Smart lock reaping (`lock_ops.py`, `core_locking.py`, `hooks/claude_code.py`):**
- `reap_expired_locks(agent_grace_seconds=N)` implicitly refreshes expired locks held by agents with a recent heartbeat instead of deleting them. The TTL becomes a fallback for crashed agents, not a hard deadline.
- Hook PreToolUse now passes `agent_grace_seconds=120.0` to `reap_expired_locks`.
- Hook PreToolUse bumps acquired TTL from 120s to 300s.
- Hook PostToolUse refreshes the lock with TTL=300s after `notify_change` (best-effort, fail-open).

**First-write-wins file ownership (`core.py`, `hooks/claude_code.py`):**
- New `CoordinationEngine.claim_file_ownership(path, agent_id)` method does `INSERT OR IGNORE` into `file_ownership`.
- Hook PostToolUse now calls it after `notify_change` — first agent to write a file becomes its owner.
- Populates the previously-dead `file_ownership` table on every sub-agent write.
- Boundary-crossing warnings, agent-tree ownership labels, and file-agent maps now have real data.

**Contract tests (`hooks/claude_code.py`, `tests/fixtures/claude_code_events/`, `tests/test_hooks.py`):**
- New `_save_event_snapshot()` helper activated by `COORDINATIONHUB_CAPTURE_EVENTS=1` env var. Writes raw events to `~/.coordinationhub/event_snapshots/`.
- New `tests/fixtures/claude_code_events/*.json` fixtures for 6 event types (SessionStart, PreToolUse_Write, PostToolUse_Write, SubagentStart, SubagentStop, SessionEnd).
- New `TestEventContract` class with 12 tests — validates required fields and that each handler accepts its fixture without raising.

### Files Changed

- Deleted: `registry_ops.py`, `registry_query.py`, `visibility.py`, `responsibilities.py`, `graph.py`, `graph_validate.py`, `graph_loader.py`, 6× `schemas_*.py`.
- Consolidated: `agent_registry.py` (~290 LOC), `schemas.py` (~590 LOC), `graphs.py` (~330 LOC), `scan.py` (~240 LOC).
- Modified: `lock_ops.py` (smart reap), `core_locking.py` (grace period passthrough), `core.py` (import updates + `claim_file_ownership`), `hooks/claude_code.py` (TTL + ownership + capture + refresh).
- New: 6 fixture JSON files, `TestEventContract` class.
- Tests: 274 → 290. `test_hooks.py`: 33 → 47 (12 contract + 2 ownership). `test_locking.py`: 38 → 40 (2 smart reap).
- CI: `.github/workflows/test.yml` adds version consistency check.

### Counts

- Python files in `coordinationhub/`: 42 → 29.
- Tests: 274 → 290 across 16 files.
- Version: 0.3.8 → 0.4.0 (minor bump for architectural work, not just bug fixes).

---

## 2026-04-11 — v0.3.8 Fix SubagentStop Status Transition & Background Agent Dedup (Review Thirteen)

### Motivation

Review Thirteen tested 15 sub-agents across two parallel waves in RecipeLab. Two bugs surfaced:
1. All 15 agents remained `status: active` after completing — SubagentStop could not find the correct agent to deregister.
2. Background agents (`run_in_background: true`) registered twice with the same `claude_agent_id`, creating duplicate entries.

### Bug Fix 1: SubagentStop Status Transition

**Root cause:** `handle_subagent_stop` used `_subagent_id()` to reconstruct the child ID, but this function generates a NEW sequence-based ID (counting existing children) rather than finding the EXISTING registered agent. SubagentStop events carry the raw Claude hex ID in `subagent_id`, not `tool_use_id`, so the derived ID was always wrong.

**Fix:** Replaced `_subagent_id()` call with `_resolve_agent_id()`, which looks up the `hub.cc.*` child ID from the `claude_agent_id` column via `find_agent_by_claude_id`. Falls back to `_subagent_id` derivation only when `_resolve_agent_id` returns the session root (no `subagent_id` in the event).

### Bug Fix 2: Background Agent Double Registration

**Root cause:** `handle_subagent_start` deduplicated by comparing the GENERATED `child_id` against existing agent IDs. Background agents fire SubagentStart twice with the same `claude_agent_id` but different sequence numbers, so the dedup check always failed.

**Fix:** Added early `find_agent_by_claude_id` check before generating a new child ID. If an agent with the same raw Claude hex ID already exists, heartbeat it instead of creating a duplicate.

### Files Changed

- `hooks/claude_code.py`: ~400 LOC → ~428 LOC. `handle_subagent_stop` rewritten to use `_resolve_agent_id`. `handle_subagent_start` adds `find_agent_by_claude_id` dedup.
- `tests/test_hooks.py`: 31 → 33 tests. New: `test_subagent_stop_sets_status_stopped_via_claude_id`, `test_background_agent_dedup`.

### Counts

- Tests: 272 → 274 across 16 files. `test_hooks.py`: 31 → 33.

---

## 2026-04-11 — v0.3.7 Adoption Friction Fixes: init, doctor, watch, error logging, session summary

### Motivation

Five adoption friction points identified during post-Review-Twelve analysis:
1. Silent failure masking (`except Exception: pass`) hid bugs across multiple review cycles.
2. No one-command setup — users manually edited `~/.claude/settings.json`.
3. No "is it working?" signal during normal operation.
4. The venv trap — `python3` in hooks resolved to a venv Python without coordinationhub.
5. Dashboard is pull-only — no live view during multi-agent sessions.

### New Commands

- **`coordinationhub init`** — One-command setup: creates `.coordinationhub/`, initializes DB, writes/merges hook config into `~/.claude/settings.json` using `sys.executable` (absolute path, avoids venv trap), then runs doctor checks.
- **`coordinationhub doctor`** — 5 diagnostic checks: importability, hooks config, storage dir, schema version, hook Python interpreter. Returns structured OK/FAIL per check.
- **`coordinationhub watch [--interval N]`** — Live-refresh agent tree with status bar (agents, locks, conflicts). Ctrl+C to stop.

### Hook Improvements

- **Error logging to `~/.coordinationhub/hook.log`** — Timestamps, tracebacks, auto-truncation at 1 MB. Also prints to stderr. Replaces `except Exception: pass` in main dispatch.
- **Session summary on SessionEnd** — Returns "Session summary: N agents tracked, N locks held, N conflicts, N notifications" as `additionalContext`.

### Hook Merge Logic

`_merge_hooks(existing, new)` merges CoordinationHub hooks into existing config:
- For each event type, checks if a coordinationhub hook already exists by command string.
- If found, updates the command (e.g., new Python path). If not, appends.
- Preserves all non-CoordinationHub hooks (e.g., user's custom Bash lint hooks).
- Idempotent: running `init` twice produces identical config.

### Files Changed

- New: `cli_setup.py` (~348 LOC), `tests/test_setup.py` (8 tests).
- Modified: `hooks/claude_code.py` (~330 → ~400 LOC), `cli.py` (~237 → ~267 LOC), `cli_commands.py` (~44 → ~51 LOC).
- Docs: all 4 docs updated (README, COMPLETE_PROJECT_DOCUMENTATION, LLM_Development, CLAUDE.md).

### Counts

- CLI commands: 31 → 34.
- Tests: 261 → 272 across 16 files (was 15). `test_hooks.py`: 28 → 31. New: `test_setup.py` (8).

---

## 2026-04-10 — v0.3.6 Fix Sub-Agent ID Mismatch (Review Twelve)

### Root Cause

All sub-agent coordination failures (missing parent_id, 0 locks, ghost agents, broken assessment) traced to a single bug: `_resolve_agent_id` in the Claude Code hook returned the raw Claude Code hex ID (e.g. `ac70a34bf2d2264d4`) instead of the `hub.cc.*` child ID that SubagentStart created. This caused each sub-agent to exist twice in the DB — once properly parented (from SubagentStart, never used for tool calls) and once as a ghost (from PreToolUse, with no hierarchy).

### Fix

- **`claude_agent_id` column** added to agents table (schema v2 → v3 with auto-migration). Stores the raw Claude Code hex ID on the `hub.cc.*` agent record during SubagentStart.
- **`_resolve_agent_id`** now accepts an engine parameter. When a raw Claude Code ID is present, it queries `find_agent_by_claude_id` to map it back to the `hub.cc.*` child before proceeding. Falls back to raw ID only when no mapping exists (backward compat).
- **`handle_subagent_start`** now extracts the raw `subagent_id`/`agent_id` from the event and passes it as `claude_agent_id` during registration.
- All handlers (`handle_pre_write`, `handle_post_write`, `handle_post_stele_index`, `handle_post_trammel_claim`) now pass the engine to `_resolve_agent_id`.

### Cascade of Fixes

1. **parent_id populated** — Sub-agents queried from PreToolUse/PostToolUse now resolve to the properly-parented `hub.cc.*` entries.
2. **Locks acquired under correct ID** — Locks are associated with the hierarchical agent, not a disconnected ghost.
3. **No ghost duplication** — A single agent record per sub-agent.
4. **Assessment scoring works** — 5-metric assessment can now compute on sub-agents with proper parent-child relationships.
5. **Change notifications attributed correctly** — PostToolUse notifications use the `hub.cc.*` ID, not the raw hex.

### Files Changed

- `db.py`: ~280 → ~295 LOC. `_CURRENT_SCHEMA_VERSION = 3`, `_migrate_v2_to_v3`, `claude_agent_id` column + index.
- `registry_ops.py`: ~106 → ~145 LOC. `claude_agent_id` param on `register_agent`, new `find_agent_by_claude_id`.
- `core.py`: ~280 → ~285 LOC. `claude_agent_id` passthrough + `find_agent_by_claude_id` method.
- `agent_registry.py`: re-exports `find_agent_by_claude_id`.
- `hooks/claude_code.py`: ~310 → ~330 LOC. Engine-aware `_resolve_agent_id`, SubagentStart stores mapping.
- `test_hooks.py`: 23 → 28 tests (5 new: mapping, lock ID, no ghosts, post-write ID, fallback).

### Counts

- Tests: 256 → 261 across 15 files.
- Schema version: 2 → 3.

---

## 2026-04-10 — v0.3.5 Ownership-Aware Locking & Contention Hotspots

### New Features

**Ownership-aware locking (`core_locking.py`):**
- `acquire_lock` now cross-checks the `file_ownership` table after acquiring a lock.
- When an agent locks a file assigned to a different agent, the response includes `ownership_warning: {owned_by: "<owner_agent_id>", message: "..."}`.
- A `boundary_crossing` conflict is recorded in `lock_conflicts` with resolution `allowed`.
- A `boundary_crossing` change notification is fired so the owning agent (or project manager) discovers the incursion via `get_notifications`.
- Self-lock refreshes (re-acquiring own lock) skip the ownership check entirely — no false warnings.

**`get_contention_hotspots` MCP tool + CLI (`core.py`, `schemas_audit.py`, `dispatch.py`, `cli_vis.py`, `cli.py`):**
- Queries `lock_conflicts` grouped by `document_path`, ranked by conflict count descending.
- Returns `{hotspots: [{document_path, conflict_count, agents_involved}], total}`.
- CLI: `coordinationhub contention-hotspots [--limit N]`.
- Identifies coordination chokepoints — files that multiple agents need concurrent access to.

**Rich agent tree (`agent_status.py`):**
- `get_agent_tree` now renders a project-management-style view: each agent node shows its current task, active file locks (with lock type and region info), and boundary crossing warnings.
- Any agent in the swarm calls `agent-tree` to see the same live hierarchy — shared situational awareness across the swarm.
- `agent_status.py`: ~225 LOC → ~290 LOC (rich tree renderer extracted as `_render_rich_tree` / `_render_node`).

### Motivation (Review Eleven)

These features directly address gaps observed during a 3-agent parallel refactor:
1. Agent A crossed into Agent B's file territory undetected — ownership-aware locking now surfaces this.
2. `routeLoader.js` was a coordination chokepoint touched by every agent — contention hotspots tool now identifies such files.

### Counts

- MCP tools: 29 → 30
- CLI commands: 30 → 31
- Tests: 246 → 256 across 15 files. `test_conflicts.py`: 6 → 16 tests.

---

## 2026-04-10 — Review Eleven Findings (No Code Changes)

### Summary

CoordinationHub was indirectly challenged during a 3-agent parallel refactor in RecipeLab (DistributedRecipeValidator feature + multi-agent refactor). The feature phase did not exercise CoordinationHub (single-agent service simulating consensus, not real multi-agent coordination). The refactor phase spawned 3 agents with prompt-based file boundaries — no automated locking.

### Key Validations

1. **Prompt-based boundaries are fragile** — Agent A crossed into Agent B's territory (modified `mcpChallengeRoutes.js`, a route file assigned to Agent B) because import paths changed. `acquire_lock` contention detection would have caught this.
2. **Completion order matters** — Agent B finished before Agent A, creating a race condition on `routeLoader.js`. `wait_for_locks` + `notify_change` would have sequenced this.
3. **Region locking is needed** — `routeLoader.js` is a coordination chokepoint touched by every feature/route change. Region locking (`region_start`/`region_end`) would allow multiple agents to lock non-overlapping sections.
4. **Manual agent tracking is inferior** — Task IDs + output polling vs `register_agent`/`get_agent_tree`/`heartbeat`.
5. **No overwrites occurred (lucky)** — careful pre-partitioning prevented conflicts, but this is exactly the fragile coordination CoordinationHub automates.

### Remaining Gaps (Future Testing)

- Actually connect CoordinationHub MCP server in a multi-agent workflow
- Test region-based locking on shared files
- Test cascade orphaning by killing an agent mid-work
- Test `broadcast` for conflict pre-check before writes
- Compare prompt-based vs lock-based coordination on overlapping file assignments

### Verdict

No code changes required. Existing design (region locking, cascade orphaning, lock contention, change notifications) addresses all observed coordination problems. Findings closed.

---

## 2026-04-10 — v0.3.4 Core Split, Assessment Synonyms, SQLite Perf

### New Features

**`core_locking.py` — LockingMixin extraction:**
- All locking and coordination methods extracted from `core.py` into `core_locking.py` (~230 LOC) as `LockingMixin`.
- `CoordinationEngine` inherits from `LockingMixin`, keeping `core.py` focused on identity, change awareness, audit, and graph/visibility (~260 LOC, down from ~495).
- Methods moved: `acquire_lock`, `release_lock`, `refresh_lock`, `get_lock_status`, `list_locks`, `release_agent_locks`, `reap_expired_locks`, `reap_stale_agents`, `broadcast`, `wait_for_locks`.

**Assessment keyword matching improved (`assessment_scorers.py`):**
- `_EVENT_RESPONSIBILITY_MAP` expanded with ~20 synonyms covering common event-type variations.
- Token-overlap fallback for unknown event types that don't match any mapped keyword.
- `assessment_scorers.py`: ~304 → ~315 LOC.

**SQLite performance tuning (`db.py`):**
- `PRAGMA cache_size=-8000` (8MB page cache, up from default 2MB).
- `PRAGMA mmap_size=67108864` (64MB memory-mapped I/O).
- New composite expiry index `idx_locks_expiry` for faster lock reaping queries.
- `db.py`: ~275 → ~280 LOC.

### Counts

- MCP tools: 29 (unchanged)
- CLI commands: 30 (unchanged)
- Tests: 246 across 15 files (unchanged)

---

## 2026-04-10 — v0.3.3 Region Locking & CI

### New Features

**CI test workflow:**
- `.github/workflows/test.yml` runs pytest on push/PR across Python 3.10-3.12.

**DB schema versioning (db.py):**
- `schema_version` table with `_CURRENT_SCHEMA_VERSION = 2`.
- Migration runner `_migrate_v1_to_v2` restructures `document_locks` for region locking.
- `init_schema()` auto-migrates existing databases on startup.

**Region locking (lock_ops.py, core.py, schemas_locking.py):**
- `document_locks` table changed from `document_path TEXT PRIMARY KEY` to `id INTEGER PRIMARY KEY AUTOINCREMENT` with `region_start INTEGER` and `region_end INTEGER` columns.
- Multiple locks per file on non-overlapping regions. Shared locks enforced (multiple shared allowed, exclusive blocks all).
- New functions: `_regions_overlap`, `find_conflicting_locks`, `find_own_lock`.
- `acquire_lock` uses `BEGIN IMMEDIATE` for thread-safe concurrent locking.
- All locking tools (`acquire_lock`, `release_lock`, `refresh_lock`, `get_lock_status`, `list_locks`) support `region_start`/`region_end` params.
- CLI commands `acquire-lock`, `release-lock`, `refresh-lock` have `--region-start`/`--region-end` flags.

**Hook unit tests:**
- New `tests/test_hooks.py` with 23 tests covering all Claude Code hook handlers.

### Counts

- MCP tools: 29 (unchanged)
- CLI commands: 30 (unchanged)
- Tests: 206 → 246 across 15 files (was 14). `test_locking.py`: 21 → 38 tests.
- `lock_ops.py`: ~119 → ~175 LOC. `db.py`: ~215 → ~275 LOC. `core.py`: ~470 → ~495 LOC.

---

## 2026-04-10 — v0.3.2 Review Ten Fixes

Addresses findings from RecipeLab Review Ten (6-feature parallel build + refactoring phase).

### Bug Fix

**Stale locks from completed agents (Review Ten bug #1):**
- Hook TTL reduced from 600s to 120s — prevents completed-agent locks from blocking work for 10 minutes.
- `handle_pre_write` now calls `reap_expired_locks()` before `acquire_lock()` — cleans up stale locks from crashed agents as a safety net.
- `acquire_lock` already handled expired locks via TTL check, but the combination of shorter TTL + pre-acquire reaping ensures faster cleanup.

### New Feature

**`list_locks` tool + `list-locks` CLI:**
- `list_locks(agent_id?)` — lists all active (non-expired) locks with document path, holder, expiry time, lock type, worktree.
- `list-locks` CLI: `coordinationhub list-locks [--agent-id <id>]`.
- Added to dispatch table, schemas (schemas_locking.py), CLI parser.
- 5 new tests in `test_locking.py`.

### Counts

- MCP tools: 28 → 29
- CLI commands: 29 → 30
- Tests: 202 → 206

---

## 2026-04-07 — v0.3.1 Polish Pass

### New Features

**`spawn_propagation` assessment metric (assessment.py):**
- New scorer `score_spawn_propagation(trace, graph)` verifies child agents inherit and act within their parent's declared responsibilities.
- Events from a child agent are checked against the union of the child's own scope and the parent's scope.
- Always included in metrics even if not listed in graph's `assessment.metrics`.
- `_suggest_graph_refinements(suite, graph)` returns `missing_handoff` and `missing_agent` suggestions for graph refinement.

**Graph-role-aware file scan (scan.py):**
- `_role_based_agent(graph, path)` maps file extension to graph role: `.py` → `implement`/`write`, `.md/.yaml/.yml` → `document`/`plan`, `.json/.toml` → `config`/`data`.
- `_get_spawned_agent_responsibilities(connect, agent_id)` resolves a spawned agent's parent's graph role from the lineage table.
- Scan assignment priority: exact path → nearest ancestor → graph role → spawned-agent inheritance → first-registered fallback.
- `SKIP_PARTS` expanded to include `.git`, `.venv`, `venv`, `.env`, `.eggs`, `*.egg-info`, `.mypy_cache`, `.tox`, `.ruff_cache`.

**`run_assessment` graph_agent_id filter:**
- `graph_agent_id` param filters traces to only those where a `register` event uses that `graph_agent_id`.
- Added to `core.py`, `dispatch.py`, `schemas_visibility.py`, `cli.py`, `cli_vis.py`.
- CLI: `coordinationhub assess --suite <file> --graph-agent-id planner`.

**Full trace storage in SQLite:**
- `run_assessment` result now includes `full_trace_json` (JSON-encoded traces) and `suggested_refinements`.
- `store_assessment_results` persists both in `details_json` alongside per-metric scores.
- Markdown report updated to show filter info and suggested graph refinements section.

### Visibility / Dashboard Improvements

**Dashboard JSON mode (`cli_vis.py`):**
- `dashboard --json` now includes the full `file_map` with each entry carrying `graph_agent_id`, `role`, `responsibilities`, and `task_description`.

**`get_agent_status` and `get_file_agent_map` (agent_status.py):**
- `get_agent_status` now returns `owned_files_with_tasks`: list of `{file, task}` dicts per owned file.
- `get_file_agent_map` now includes `graph_agent_id` in each entry alongside `role` and `responsibilities`.

**Graph auto-mapping on load (graphs.py):**
- `_populate_agent_responsibilities_from_graph(connect, graph)` called inside `load_coordination_spec_from_disk` after a successful load.
- For each graph agent whose id matches an active registered agent, `agent_responsibilities` is upserted.

### Input Validation

- `load_coordination_spec(path)`: returns `{"loaded": False, "error": "Coordination spec not found: <path>"}` when explicit path does not exist.
- `scan_project(extensions=[])`: returns `{"scanned": 0, "owned": 0, "error": "extensions list cannot be empty"}`.

### Code Quality

- All 7 graph/visibility tool methods in `core.py` now have comments explaining their relationship to the lock/lineage foundation.
- `visibility.py` re-exports `_role_based_agent` and `_get_spawned_agent_responsibilities` from `scan.py`.
- All schema files: schemas_identity (~123 LOC), schemas_locking (~145 LOC), schemas_coordination (~59 LOC), schemas_change (~77 LOC), schemas_audit (~43 LOC), schemas_visibility (~156 LOC) — all well under 500 LOC.

### Tests

15 new tests added across `test_assessment.py` and `test_visibility.py`:
- `score_spawn_propagation`: child within scope, child outside scope, coordination always OK, empty trace
- `run_assessment`: spawn_propagation included, graph_agent_id filter, full trace stored, suggested refinements
- `format_markdown_report`: with refinements section
- Graph auto-mapping on load
- Spawned agent inherits parent role during scan
- `get_agent_status` includes `owned_files_with_tasks`
- `get_file_agent_map` includes `graph_agent_id`
- `scan_project` empty extensions returns error
- Dashboard JSON output structure

Total: **165 tests** (up from 150).

### Example Files

- `coordination_spec.yaml` — YAML format example with `planner`, `executor`, `reviewer` agents
- `coordination_spec.json` — JSON format equivalent
- README.md updated to reference both with relative links

---

## 2026-04-06 — v0.3.0 Strategic Redesign

### New Modules

**`visibility.py` (NEW):**
- File ownership scan, agent status, and file map helpers extracted from `graphs.py`
- Functions: `store_responsibilities`, `update_agent_status_tool`, `get_agent_status_tool`, `get_file_agent_map_tool`, `scan_project_tool`, `_default_owner_agent`
- All functions receive `connect` callable — zero internal deps

**`dispatch.py` (NEW):**
- `TOOL_DISPATCH` table extracted from `schemas.py`
- Maps `tool_name → (engine_method_name, allowed_kwargs)`
- `schemas.py` retains only `TOOL_SCHEMAS`

**`cli_commands.py` (NEW):**
- All 26 CLI command handlers extracted from `cli.py`
- Imported lazily on-demand to keep startup time minimal

### Architecture Changes

**`schemas.py` split into group files:**
- `schemas.py` (~31 LOC): Schema aggregator — imports all groups, re-exports `TOOL_SCHEMAS`
- `schemas_identity.py` (~123 LOC): Identity & Registration (6 tools)
- `schemas_locking.py` (~145 LOC): Document Locking (7 tools)
- `schemas_coordination.py` (~59 LOC): Coordination Actions (2 tools)
- `schemas_change.py` (~77 LOC): Change Awareness (3 tools)
- `schemas_audit.py` (~43 LOC): Audit & Status (2 tools)
- `schemas_visibility.py` (~156 LOC): Graph & Visibility (8 tools)
- `dispatch.py` (~48 LOC): `TOOL_DISPATCH` table only
- Updated imports in `core.py`, `mcp_server.py`, `test_notifications.py`

**`core.py` refactoring:**
- `update_agent_status` delegation fixed: now routes to `_v.update_agent_status_tool` (was `_g.update_agent_status_tool`)
- `store_responsibilities` call fixed: now calls `_v.store_responsibilities` (was `_g.store_responsibilities`)
- `core.py` now imports `dispatch.py` instead of `schemas.py` for `TOOL_DISPATCH`

**`cli.py` split (776 → 229 LOC):**
- `cli.py`: argument parser + lazy dispatch only
- `cli_commands.py`: all 26 command handlers, imported on-demand

### Assessment Runner — Real Metric Implementations

All four metric scorers replaced with real implementations:

**`score_role_stability`:** Maps event types to declared responsibilities from the graph. Penalizes events outside the agent's declared scope. Lock/unlock/notify_change always permitted.

**`score_handoff_latency`:** Validates handoff from/to pairs against graph definitions. Partial credit (0.5) for correct pair without condition, full credit (1.0) when condition is present and non-trivial.

**`score_outcome_verifiability`:** Evaluates lock-write-unlock patterns per file. Tracks locked paths, scores modification events as verified only if path was previously locked. Unlocked paths contribute to verification score.

**`score_protocol_adherence`:** Checks agents act within declared responsibilities. Violations reduce score proportionally. Events outside scope are penalized.

### Bug Fixes (from v0.2.0 audit)

- **Visibility schema duplication eliminated:** `_init_visibility_schema()` removed from `core.py`; visibility tables now defined only in `db.py._SCHEMAS`
- **json.loads(None) TypeError:** `resp.get("responsibilities") or "[]"` guard added — was failing when key exists with null value
- **SQL tuple placeholder bug:** `(agent_id,)` added trailing comma — was unpacking string chars as individual bindings
- **YAML test failures:** Tests now skip when `ruamel.yaml` unavailable; JSON used as fallback
- **Graph validation missing agents check:** Added `if "agents" not in data: errors.append("agents: required field is missing")`
- **test_validate_missing_agents_field:** Now correctly fails invalid graphs

### CLI Changes

**`broadcast` positional `message` arg removed:** CLI no longer accepts positional message argument. `broadcast` only checks lock state, it does not store or forward messages.

### Version Bump

- `coordinationhub/__init__.py`: `__version__ = "0.3.0"`
- `pyproject.toml`: `version = "0.3.0"`

---

## 2026-04-06 — v0.2.0 Audit Fixes

### Critical Bug Fixes

**`lineage` table silent data loss (db.py):**
- `PRIMARY KEY (parent_id)` → `PRIMARY KEY (parent_id, child_id)`. Single-column PK only allowed one child per parent — second child registration silently replaced the first. Multi-child parents were impossible.
- Added `idx_lineage_parent ON lineage(parent_id)` for efficient lineage walks.

**`generate_agent_id` double-dot LIKE collision (core.py):**
- `child_prefix = 'hub.123.0.' + '|| .%'` produced `'hub.123.0..%'` — double dot matched nothing, all child sequence lookups returned NULL, collisions were inevitable.
- Extracted `_next_seq(prefix, conn)` helper with `base = prefix.rstrip(".")` normalization before LIKE pattern construction: `'hub.123.0.%'` (single dot).
- This bug was masked by the lineage PK bug — fixing the PK exposed the collision in tests.

**`record_conflict` bind count mismatch (lock_ops.py):**
- INSERT bound 10 values into 7 columns (`expected_version`, `actual_version` had no table columns). Bound values were shifted: `resolution` received `conflict_type`, `details_json` received `resolution`, rest were NULL/misaligned.
- Fixed to 7 columns matching the schema.

**`refresh_lock` wrong expiry arithmetic (lock_ops.py):**
- `new_expires = row["locked_at"] + new_ttl` — used the original lock time, not current time. Lock could expire in the past if TTL was short.
- Fixed to `new_expires = now + new_ttl`.

### Refactoring

**`acquire_lock` body extracted into 4 helpers (core.py):**
- `_try_refresh_lock()` — self-renewal when caller already holds the lock
- `_handle_contested_lock()` — contested case: conflict log + steal or reject
- `_steal_lock()` — force acquisition with conflict recording
- `_insert_new_lock()` — insert when no existing lock
- Replaces ~85-line monolithic body with coherent single-responsibility methods.

**`heartbeat()` made pure timestamp update (core.py):**
- Removed `stale_released` from return and the internal call to `reap_expired_locks`.
- Lock reaping is now explicit only via `reap_expired_locks()` or `reap_stale_agents()`.
- This separation was implicit before but not enforced.

**`reap_stale_agents` batch DELETE (core.py):**
- O(n) per-agent DELETE loop → single `DELETE FROM document_locks WHERE locked_by IN (...)`.
- Orphaning uses batch UPDATE with `NULL` parent_id for root-level agents.

**`broadcast()` batch SQL (core.py + schemas.py):**
- Per-sibling connection-per-query loop → single `SELECT ... WHERE locked_by IN (siblings)` batch query.
- Removed `message` and `action` params — broadcast has no persistent message storage; siblings receive an empty acknowledgment.
- `TOOL_DISPATCH["broadcast"]` kwargs reduced to `["agent_id", "document_path", "ttl"]`.

**`status()` single compound query (core.py):**
- 5 separate `COUNT(*)` queries → single `SELECT (SELECT COUNT(*) ...) AS agents, (SELECT COUNT(*) ...) AS locks, ...` query.

### Minor Fixes

- **agent_registry.py:** `get_lineage` ancestor entries now correctly include `parent_id` field (was `None` for root ancestor, losing the grandparent edge).
- **agent_registry.py:** `reap_stale_agents` docstring removed erroneous "release their locks" claim.
- **conflict_log.py:** Deleted dead `init_conflicts_table` function (duplicated `db.py` schema init; unreachable since `init_schema` already runs at engine start).
- **conflict_log.py:** Removed unused `import time`.
- **mcp_stdio.py:** Added `try/except` with `logger.exception` around `engine.heartbeat(server_agent_id)` in `heartbeat_loop`.
- **cli.py:** Env var `COORDINATIONHUB_STORAGE_DIR` only set when `--storage-dir` is explicitly provided (`None` check, not falsy `""` check).
- **db.py `_resolve_storage_dir`:** Type hint broadened to `Path | str | None` to accept `mcp_stdio` string argument.
- **Test suite:** `test_broadcast_with_document_path`, `test_broadcast_stale_sibling_excluded` updated for removed `message`/`action` params.
- **Test suite:** `test_reap_stale_agents` now directly UPDATE sets `last_heartbeat = 0` (was relying on `timeout=0.1` against a fresh agent, which was never stale).

### Version Bump

- `coordinationhub/__init__.py`: `__version__ = "0.2.0"`
- `pyproject.toml`: `version = "0.2.0"`

---

## 2026-04-06 — v0.1.0 Initial Implementation

### Added

**Core modules:**
- `coordinationhub/db.py` — SQLite schema with 6 tables (`agents`, `lineage`, `document_locks`, `lock_conflicts`, `change_notifications`) + thread-local `ConnectionPool` with WAL mode
- `coordinationhub/agent_registry.py` — Agent lifecycle: `register_agent`, `heartbeat`, `deregister_agent`, `list_agents`, `reap_stale_agents`, `get_lineage`, `get_siblings`
- `coordinationhub/lock_ops.py` — Shared primitives: `refresh_lock`, `reap_expired_locks`, `record_conflict`, `query_conflicts`, `release_agent_locks`
- `coordinationhub/conflict_log.py` — Conflict recording (`record_conflict`) and querying (`query_conflicts`) wrapping `lock_ops`
- `coordinationhub/notifications.py` — Change notification storage (`notify_change`, `get_notifications`, `prune_notifications`) with age-based and count-based pruning
- `coordinationhub/core.py` — `CoordinationEngine` class with all 17 MCP tool methods wired to sub-modules
- `coordinationhub/schemas.py` — JSON Schema for all 17 tools + `TOOL_DISPATCH` table shared by both transports

**Transport layer:**
- `coordinationhub/mcp_server.py` — HTTP MCP server using `ThreadedHTTPServer(ThreadingMixIn, HTTPServer)`. Endpoints: `GET /tools`, `GET /health`, `POST /call`. Background heartbeat thread. Zero external dependencies.
- `coordinationhub/mcp_stdio.py` — Stdio MCP server using official `mcp` SDK. Requires optional `mcp>=1.0.0` package. Heartbeat via `asyncio.ensure_future`.
- `coordinationhub/cli.py` — argparse CLI with 24 subcommands covering all tool methods

**Package scaffolding:**
- `coordinationhub/__init__.py` — Package init exporting `CoordinationEngine`, `CoordinationHubMCPServer`, `__version__`
- `pyproject.toml` — Package config with stdlib-only core, optional `mcp` extra, console script `coordinationhub`

**Documentation:**
- `wiki-local/spec-project.md` — Full architecture spec including SQLite schema, tool schemas, agent ID format, port allocation, zero-dependency guarantee
- `wiki-local/index.md` — Wiki navigation page
- `README.md` — Quickstart, CLI commands, feature overview, architecture diagram
- `COMPLETE_PROJECT_DOCUMENTATION.md` — File inventory, data flow diagrams, SQLite schema, transport layer details
- `CLAUDE.md` — Agent guidance for working in this project

### Architecture Decisions

- **Separate MCP server**: CoordinationHub is built as a separate MCP server (not extending Stele) because coordination is a different problem domain than code intelligence
- **Zero third-party deps in core**: HTTP server built on `http.server` + `socketserver.ThreadingMixIn`. No `requests`, `httpx`, `aiohttp`
- **Stdio optional**: The `mcp` package is only required for stdio transport. Air-gapped install works with `pip install -e . --no-deps`
- **Coordination context bundle**: Agent registration returns a JSON bundle with `agent_id`, `registered_agents`, `active_locks`, `pending_notifications`, and `coordination_url` — parent agents pass this to spawned sub-agents
- **17 tools**: All tool methods on `CoordinationEngine` are directly MCP-callable

### SQLite Tables

1. `agents` — agent registry with parent_id, worktree_root, pid, heartbeat timestamps, status
2. `lineage` — parent→child relationships (written on spawn, used for orphaning)
3. `document_locks` — TTL-based locks with owner, type, expiry
4. `lock_conflicts` — audit log of lock steals and ownership violations
5. `change_notifications` — time-ordered change events for polling

### MCP Tools

| # | Tool | Engine Method |
|---|------|--------------|
| 1 | `register_agent` | `register_agent` |
| 2 | `heartbeat` | `heartbeat` |
| 3 | `deregister_agent` | `deregister_agent` |
| 4 | `list_agents` | `list_agents` |
| 5 | `get_lineage` | `get_lineage` |
| 6 | `get_siblings` | `get_siblings` |
| 7 | `acquire_lock` | `acquire_lock` |
| 8 | `release_lock` | `release_lock` |
| 9 | `refresh_lock` | `refresh_lock` |
| 10 | `get_lock_status` | `get_lock_status` |
| 11 | `release_agent_locks` | `release_agent_locks` |
| 12 | `reap_expired_locks` | `reap_expired_locks` |
| 13 | `reap_stale_agents` | `reap_stale_agents` |
| 14 | `broadcast` | `broadcast` |
| 15 | `wait_for_locks` | `wait_for_locks` |
| 16 | `notify_change` | `notify_change` |
| 17 | `get_notifications` | `get_notifications` |
| 18 | `prune_notifications` | `prune_notifications` |
| 19 | `get_conflicts` | `get_conflicts` |
| 20 | `status` | `status` |
