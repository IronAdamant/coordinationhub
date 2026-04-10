# LLM_Development.md — CoordinationHub

**Version:** 0.3.7
**Last updated:** 2026-04-11

## Change Log

All significant changes to the CoordinationHub project are documented here in reverse chronological order.

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
