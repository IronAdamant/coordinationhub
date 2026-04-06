# CLAUDE.md — CoordinationHub

**Audience:** Autonomous coding agents (MCP, CLI invoked by agents), not a standalone dashboard.

## Project Overview

CoordinationHub externalizes the coordination bottleneck for multi-agent coding swarms. It tracks agent identity and lineage, enforces document locking, detects lock conflicts, propagates coordination context to spawned sub-agents, and provides a shared ground truth for "who is doing what" across all LLMs and IDEs.

Zero third-party dependencies in core. Part of the Stele + Chisel + Trammel + CoordinationHub quartet.

## Architecture

```
coordinationhub/
  __init__.py         — Package init, exports CoordinationEngine, CoordinationHubMCPServer
  core.py             — CoordinationEngine: all 27 tool methods + helpers (~454 LOC)
  paths.py            — Project-root detection and path normalization (~47 LOC)
  context.py          — Context bundle builder for register_agent responses (~98 LOC)
  schemas.py          — Schema aggregator, re-exports TOOL_SCHEMAS (~31 LOC)
  schemas_identity.py  — Identity & Registration schemas (~123 LOC)
  schemas_locking.py    — Document Locking schemas (~145 LOC)
  schemas_coordination.py — Coordination Action schemas (~59 LOC)
  schemas_change.py     — Change Awareness schemas (~77 LOC)
  schemas_audit.py     — Audit & Status schemas (~43 LOC)
  schemas_visibility.py — Graph & Visibility schemas (~132 LOC)
  dispatch.py         — Tool dispatch table (~48 LOC)
  graphs.py           — Thin aggregator re-exporting from graph_validate/graph_loader/graph (~105 LOC)
  graph_validate.py   — Pure validation functions (~131 LOC)
  graph_loader.py     — File loading (YAML/JSON) and spec auto-detection (~49 LOC)
  graph.py            — CoordinationGraph in-memory object (~66 LOC)
  visibility.py     — Thin re-export aggregator for scan/agent_status/responsibilities (~15 LOC)
  scan.py           — File ownership scan, nearest-ancestor assignment (~105 LOC)
  agent_status.py   — Agent status query and file map helpers (~111 LOC)
  responsibilities.py — Agent role/responsibilities storage from graph (~35 LOC)
  assessment.py       — Assessment runner, 4 metric scorers (~394 LOC)
  mcp_server.py       — HTTP MCP server (ThreadedHTTPServer, stdlib only)
  mcp_stdio.py        — Stdio MCP server (optional mcp package required)
  cli.py              — argparse CLI parser + lazy dispatch (~229 LOC)
  cli_commands.py     — Re-exports all CLI handlers from domain sub-modules (~34 LOC)
  cli_agents.py       — Agent identity & lifecycle CLI commands (~205 LOC)
  cli_locks.py        — Document locking & coordination CLI commands (~214 LOC)
  cli_vis.py          — Change awareness, audit, graph & assessment CLI commands (~307 LOC)
  db.py               — SQLite schema + thread-local ConnectionPool
  agent_registry.py — Thin re-export aggregator for registry_ops/registry_query (~23 LOC)
  registry_ops.py   — Agent lifecycle ops: register, heartbeat, deregister (~107 LOC)
  registry_query.py — Agent registry queries: list, lineage, siblings, reaping (~142 LOC)
  lock_ops.py         — Shared lock primitives
  conflict_log.py     — Conflict recording and querying
  notifications.py    — Change notification storage and retrieval
  tests/              — pytest suite (124 tests, 10 test files)
```

## Module Design

- **Zero internal deps in sub-modules**: `agent_registry.py`, `lock_ops.py`, `conflict_log.py`, `notifications.py`, `visibility.py` all receive `connect: ConnectFn` from the caller. They have no internal imports from each other.
- **Thread-local connection pool**: `db.py` provides a `ConnectionPool` that gives each thread its own reused SQLite connection. WAL mode enabled, 30s busy timeout.
- **Dispatch separation**: `schemas.py` (schemas only) and `dispatch.py` (dispatch table) are separate modules shared by both HTTP and stdio servers.
- **Project root detection**: `detect_project_root()` in `paths.py` walks up from CWD looking for `.git`. Used by `CoordinationEngine.__init__`.

## Key Design Decisions

- **Agent ID format**: `{namespace}.{PID}.{sequence}` for root agents, `{parent_id}.{sequence}` for children. PID encoded to distinguish agents from different processes. Sequence numbers derived via `_next_seq()` helper.
- **TTL-based locks**: All locks expire unless refreshed. Default 300s. `heartbeat()` does NOT reap expired locks — call `reap_expired_locks()` explicitly.
- **Force steal with conflict log**: `acquire_lock(force=True)` records the steal in `lock_conflicts` before overwriting, so conflicts are auditable.
- **Cascade orphaning**: When an agent dies, children are re-parented to the grandparent (or become root if no grandparent). No agent is permanently orphaned.
- **No message passing**: CoordinationHub is a shared database, not a message bus. Agents communicate by convention (lock acquisition, change notifications) and polling.
- **Coordination URLs in context bundle**: Parent agents embed `coordination_urls` dict. Override defaults via `COORDINATIONHUB_COORDINATION_URL`, `COORDINATIONHUB_STELE_URL`, `COORDINATIONHUB_CHISEL_URL`, `COORDINATIONHUB_TRAMMEL_URL` environment variables.
- **SQLite WAL mode**: `PRAGMA wal_checkpoint(TRUNCATE)` on engine close ensures no unbounded WAL growth.
- **`broadcast` message/action params removed**: These were previously accepted but never stored. Removed from both schema and implementation to avoid misleading LLMs.

## 27 MCP Tools

Identity: `register_agent`, `heartbeat`, `deregister_agent`, `list_agents`, `get_lineage`, `get_siblings`
Locking: `acquire_lock`, `release_lock`, `refresh_lock`, `get_lock_status`, `release_agent_locks`, `reap_expired_locks`, `reap_stale_agents`
Coordination: `broadcast`, `wait_for_locks`
Change: `notify_change`, `get_notifications`, `prune_notifications`
Audit: `get_conflicts`, `status`
Graph & Visibility (0.3.0): `load_coordination_spec`, `validate_graph`, `scan_project`, `get_agent_status`, `get_file_agent_map`, `update_agent_status`, `run_assessment`

**Tool count is dynamic** — `status()` returns `len(TOOL_DISPATCH)` (currently 27), not a hardcoded number.

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

## Known Issues

- **Existing DBs**: The `lineage` table uses a composite primary key `(parent_id, child_id)`. Existing `.coordinationhub/coordination.db` files created before this change may have incorrect lineage data for multi-child parents. A manual migration or fresh start is recommended.
- **`_normalize_path`**: Has no explicit test coverage for edge cases (Windows paths, non-UTF8, symlinks).

## Test Suite

```bash
python -m pytest tests/ -v
# 149 tests across 11 test files:
#   test_agent_lifecycle.py  — 16 tests
#   test_locking.py           — 16 tests
#   test_notifications.py    — 7 tests
#   test_conflicts.py         — 6 tests
#   test_coordination.py      — 7 tests
#   test_visibility.py       — 14 tests
#   test_graphs.py           — 14 tests
#   test_assessment.py        — 9 tests
#   test_integration.py      — 15 tests (HTTP transport)
#   test_core.py            — 25 tests (graph delegation, path utils, agent ID)
```

Always run the test suite before and after changes. Record results with `chisel record_result`.
