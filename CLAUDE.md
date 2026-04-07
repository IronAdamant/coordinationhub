# CLAUDE.md — CoordinationHub

**Audience:** Autonomous coding agents (MCP, CLI invoked by agents), not a standalone dashboard.

## Project Overview

CoordinationHub externalizes the coordination bottleneck for multi-agent coding swarms. It tracks agent identity and lineage, enforces document locking, detects lock conflicts, propagates coordination context to spawned sub-agents, and provides a shared ground truth for "who is doing what" across all LLMs and IDEs.

Zero third-party dependencies in core. Works standalone or alongside Stele, Chisel, and Trammel.

## Architecture

```
coordinationhub/
  __init__.py         — Package init, exports CoordinationEngine, CoordinationHubMCPServer
  core.py             — CoordinationEngine: all 28 tool methods (~431 LOC)
  _storage.py        — CoordinationStorage: SQLite pool, path resolution, lifecycle (~121 LOC)
  paths.py            — Project-root detection and path normalization (~48 LOC)
  context.py          — Context bundle builder for register_agent responses (~100 LOC)
  schemas.py          — Schema aggregator, re-exports TOOL_SCHEMAS (~31 LOC)
  schemas_identity.py  — Identity & Registration schemas (~123 LOC)
  schemas_locking.py    — Document Locking schemas (~145 LOC)
  schemas_coordination.py — Coordination Action schemas (~59 LOC)
  schemas_change.py     — Change Awareness schemas (~77 LOC)
  schemas_audit.py     — Audit & Status schemas (~43 LOC)
  schemas_visibility.py — Graph & Visibility schemas (8 tools, ~156 LOC)
  dispatch.py         — Tool dispatch table (~48 LOC)
  graphs.py           — Thin aggregator re-exporting from graph_validate/graph_loader/graph (~105 LOC)
  graph_validate.py   — Pure validation functions (~131 LOC)
  graph_loader.py     — File loading (YAML/JSON) and spec auto-detection (~49 LOC)
  graph.py            — CoordinationGraph in-memory object (~66 LOC)
  visibility.py       — Thin re-export aggregator for scan/agent_status/responsibilities (~15 LOC)
  scan.py             — File ownership scan, nearest-ancestor assignment (~105 LOC)
  agent_status.py     — Agent status query, file map, and agent tree helpers (~225 LOC)
  responsibilities.py  — Agent role/responsibilities storage from graph (~35 LOC)
  assessment.py       — Assessment runner, 5 metric scorers (~394 LOC)
  mcp_server.py       — HTTP MCP server (ThreadedHTTPServer, stdlib only)
  mcp_stdio.py        — Stdio MCP server (optional mcp package required)
  cli.py              — argparse CLI parser + lazy dispatch (~237 LOC)
  cli_commands.py     — Re-exports all CLI handlers from domain sub-modules (~43 LOC)
  cli_agents.py       — Agent identity & lifecycle CLI commands (~205 LOC)
  cli_locks.py        — Document locking & coordination CLI commands (~214 LOC)
  cli_vis.py          — Change awareness, audit, graph, assessment CLI + agent-tree (~346 LOC)
  db.py               — SQLite schema + thread-local ConnectionPool (~215 LOC)
  agent_registry.py   — Thin re-export aggregator for registry_ops/registry_query (~23 LOC)
  registry_ops.py     — Agent lifecycle ops: register, heartbeat, deregister (~120 LOC)
  registry_query.py   — Agent registry queries: list, lineage, siblings, reaping (~142 LOC)
  lock_ops.py         — Shared lock primitives (~119 LOC)
  conflict_log.py     — Conflict recording and querying (~53 LOC)
  notifications.py     — Change notification storage and retrieval (~115 LOC)
  tests/              — pytest suite (187 tests, 12 test files)
```

## Module Design

- **Zero internal deps in sub-modules**: `agent_registry.py`, `lock_ops.py`, `conflict_log.py`, `notifications.py`, `visibility.py` all receive `connect: ConnectFn` from the caller. They have no internal imports from each other.
- **Storage layer isolated in `_storage.py`**: `CoordinationStorage` owns the SQLite pool, path resolution, and schema init. Both `core.py` and CLI entry points depend on it.
- **Thread-local connection pool**: `db.py` provides a `ConnectionPool` that gives each thread its own reused SQLite connection. WAL mode enabled, 30s busy timeout.
- **Dispatch separation**: `schemas.py` (schemas only) and `dispatch.py` (dispatch table) are separate modules shared by both HTTP and stdio servers.
- **Project root detection**: `detect_project_root()` in `paths.py` walks up from CWD looking for `.git`. Used by `CoordinationEngine.__init__`.

## Key Design Decisions

- **Agent ID format**: `{namespace}.{PID}.{sequence}` for root agents, `{parent_id}.{sequence}` for children. PID encoded to distinguish agents from different processes. Sequence numbers derived via `_next_seq()` helper.
- **TTL-based locks**: All locks expire unless refreshed. Default 300s. `heartbeat()` does NOT reap expired locks — call `reap_expired_locks()` explicitly.
- **Assessment keyword matching**: The `role_stability`, `protocol_adherence`, and `spawn_propagation` metric scorers use keyword heuristics to match event types against declared responsibilities (e.g. `"write" in resp_lower`). Non-standard vocabulary (e.g. `"perform_edits"` instead of `"write_code"`) will reduce scores. The assessment framework is designed for human-readable traces with conventional responsibility names.
- **Force steal with conflict log**: `acquire_lock(force=True)` records the steal in `lock_conflicts` before overwriting, so conflicts are auditable.
- **Cascade orphaning**: When an agent dies, children are re-parented to the grandparent (or become root if no grandparent). The stale `lineage` rows referencing the dead agent as parent are deleted so the responsibility-inheritance scan always joins on a live spawning parent. No agent is permanently orphaned.
- **No message passing**: CoordinationHub is a shared database, not a message bus. Agents communicate by convention (lock acquisition, change notifications) and polling.
- **Coordination URL in context bundle**: Parent agents embed `coordination_url` string. Override via `COORDINATIONHUB_COORDINATION_URL` environment variable.
- **SQLite WAL mode**: `PRAGMA wal_checkpoint(TRUNCATE)` on engine close ensures no unbounded WAL growth.
- **`broadcast` message/action params removed**: The `message` and `action` positional params were removed (they were never stored). The `document_path` optional param remains — when provided, it is used to check for lock conflicts among acknowledged siblings and is not persisted.

## 28 MCP Tools

Identity: `register_agent`, `heartbeat`, `deregister_agent`, `list_agents`, `get_lineage`, `get_siblings`
Locking: `acquire_lock`, `release_lock`, `refresh_lock`, `get_lock_status`, `release_agent_locks`, `reap_expired_locks`, `reap_stale_agents`
Coordination: `broadcast`, `wait_for_locks`
Change: `notify_change`, `get_notifications`, `prune_notifications`
Audit: `get_conflicts`, `status`
Graph & Visibility (0.3.1): `load_coordination_spec`, `validate_graph`, `scan_project`, `get_agent_status`, `get_file_agent_map`, `update_agent_status`, `run_assessment`, `get_agent_tree`

**Tool count is dynamic** — `status()` returns `len(TOOL_DISPATCH)` (currently 28), not a hardcoded number.

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

- **Existing DBs**: The `lineage` table uses a composite primary key `(parent_id, child_id)`. Existing `.coordinationhub/coordination.db` files created before the orphan lineage cleanup change may have stale lineage rows for re-parented children. A manual migration or fresh start is recommended.

## Test Suite

```bash
python -m pytest tests/ -v
# 187 tests across 12 test files:
#   test_agent_lifecycle.py  — 21 tests
#   test_locking.py          — 16 tests
#   test_notifications.py    — 8 tests
#   test_conflicts.py         — 6 tests
#   test_coordination.py     — 7 tests
#   test_visibility.py      — 23 tests
#   test_graphs.py           — 22 tests
#   test_assessment.py       — 15 tests
#   test_integration.py      — 15 tests (HTTP transport)
#   test_core.py             — 33 tests (graph delegation, path utils, agent ID)
#   test_cli.py              — 11 tests (argparse parser, subcommand dispatch)
```

Always run the test suite before and after changes.
