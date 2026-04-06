# LLM_Development.md — CoordinationHub

**Version:** 0.3.0
**Last updated:** 2026-04-06

## Change Log

All significant changes to the CoordinationHub project are documented here in reverse chronological order.

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

**`schemas.py` split:**
- `schemas.py` (~574 LOC): JSON Schema for all 27 tool parameters only
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
- **Coordination context bundle**: Agent registration returns a JSON bundle with `agent_id`, `registered_agents`, `active_locks`, `pending_notifications`, and `coordination_urls` — parent agents pass this to spawned sub-agents
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
