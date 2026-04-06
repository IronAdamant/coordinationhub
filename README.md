# CoordinationHub

**Declarative multi-agent coordination hub for coding swarms — zero third-party deps in core.**

Tracks agent identity and lineage, enforces document locking, loads declarative coordination
graphs (YAML/JSON), auto-assigns Agent ID ownership to files via worktree scans,
exposes live status for both LLMs (MCP tools) and humans (CLI dashboard), and ships
with an assessment runner for scoring protocol fidelity against MiniMax coordination
test suites.

Part of the **Stele + Chisel + Trammel + CoordinationHub** quartet.

## Features

- **Declarative coordination graphs** — agents, handoffs, escalation rules, and assessment
  criteria defined in `coordination_spec.yaml` or `coordination_spec.json` at project root.
- **File ownership tracking** — SQLite `file_ownership` table populated by `scan_project`;
  maps every tracked file to its responsible Agent ID.
- **Visibility layer** — MCP tools + `coordinationhub dashboard` CLI command for live
  human/LLM-readable status.
- **Assessment runner** — `coordinationhub assess --suite <file>` scores graph fidelity
  against MiniMax coordination test traces. 4 real metric scorers.
- **Agent identity & lineage** — Hierarchical agent IDs with parent-child relationships.
- **Document locking** — TTL-based exclusive/shared locks with force-steal and conflict recording.
- **Cascade orphaning** — Children re-parented to grandparent when parent dies.
- **Change notifications** — Polling-based change awareness across agents.
- **Zero third-party dependencies in core** — Supply chain security is non-negotiable.

## Quickstart

```bash
# HTTP server (stdlib only)
coordinationhub serve --port 9877

# Stdio MCP (requires optional mcp package):
pip install coordinationhub[mcp]
coordinationhub serve-mcp

# Scan project and assign file ownership
coordinationhub scan-project

# View live dashboard
coordinationhub dashboard
coordinationhub dashboard --json

# Run assessment suite
coordinationhub assess --suite my_minimax_tests.json
```

## Coordination Graph

Place `coordination_spec.yaml` (or `.json`) at your project root:

```yaml
agents:
  - id: planner
    role: decompose tasks
    model: minimax-m2.7
    responsibilities:
      - break down user stories
      - assign subtasks
  - id: executor
    role: implement
    model: minimax-m2.7
    responsibilities:
      - write code
      - run tests

handoffs:
  - from: planner
    to: executor
    condition: task_size < 500 && no_blockers

escalation:
  max_retries: 3
  fallback: human_review

assessment:
  metrics:
    - role_stability
    - handoff_latency
    - outcome_verifiability
    - protocol_adherence
```

## 27 MCP Tools

| Tool | Purpose |
|------|---------|
| `register_agent` | Register and get coordination context + responsibilities from graph |
| `heartbeat` | Keep agent alive + reap expired locks |
| `deregister_agent` | Remove agent, orphan children, release locks |
| `list_agents` | List registered agents with staleness |
| `get_lineage` | Get ancestors and descendants of an agent |
| `get_siblings` | Get agents sharing the same parent |
| `acquire_lock` | Acquire exclusive or shared lock on a document |
| `release_lock` | Release a held lock |
| `refresh_lock` | Extend lock TTL without releasing |
| `get_lock_status` | Check if a document is locked |
| `release_agent_locks` | Release all locks held by an agent |
| `reap_expired_locks` | Clear all expired locks |
| `reap_stale_agents` | Mark stale agents as stopped |
| `broadcast` | Announce intention to siblings (checks lock state only) |
| `wait_for_locks` | Poll until locks are released |
| `notify_change` | Record a change event |
| `get_notifications` | Poll for change notifications |
| `prune_notifications` | Clean up old notifications |
| `get_conflicts` | Query the conflict log |
| `status` | System status summary (includes `graph_loaded`) |
| `load_coordination_spec` | Reload coordination spec from disk |
| `validate_graph` | Validate loaded graph schema |
| `scan_project` | Perform file ownership scan |
| `get_agent_status` | Full status for an agent (work, responsibilities, owned files) |
| `get_file_agent_map` | Map of all tracked files → agent + responsibility |
| `update_agent_status` | Broadcast what an agent is currently working on |
| `run_assessment` | Run an assessment suite against the loaded graph |

## CLI Commands

```bash
# Server
coordinationhub serve --port 9877
coordinationhub serve-mcp

# Status & visibility
coordinationhub status
coordinationhub list-agents
coordinationhub dashboard
coordinationhub dashboard --json
coordinationhub agent-status <agent_id>
coordinationhub agent-status <agent_id> --json

# Graph
coordinationhub load-spec
coordinationhub validate-spec

# File ownership
coordinationhub scan-project

# Assessment
coordinationhub assess --suite my_minimax_tests.json

# Agent lifecycle
coordinationhub register <agent_id>
coordinationhub register <agent_id> --parent-id <parent>
coordinationhub heartbeat <agent_id>
coordinationhub deregister <agent_id>
coordinationhub lineage <agent_id>
coordinationhub siblings <agent_id>

# Locking
coordinationhub acquire-lock <path> <agent_id>
coordinationhub release-lock <path> <agent_id>
coordinationhub refresh-lock <path> <agent_id>
coordinationhub lock-status <path>
coordinationhub release-agent-locks <agent_id>
coordinationhub reap-expired-locks
coordinationhub reap-stale-agents

# Coordination
coordinationhub broadcast <agent_id>
coordinationhub wait-for-locks <agent_id> <paths...>
coordinationhub notify-change <path> <type> <agent_id>
coordinationhub get-notifications
coordinationhub prune-notifications
coordinationhub get-conflicts
```

## Agent ID Format

```
hub.12345.0           — root agent (namespace.PID.sequence)
hub.12345.0.0         — child of hub.12345.0
hub.12345.0.1         — another child of hub.12345.0
hub.12345.0.0.0       — grandchild
```

When a coordination graph is loaded, agents may also have a `graph_agent_id`
(e.g., `planner`) mapped via the `agent_responsibilities` table.

## Architecture

```
coordinationhub/
  __init__.py         — Package init, exports CoordinationEngine, CoordinationHubMCPServer
  core.py             — CoordinationEngine: all 27 tool methods (~524 LOC)
  schemas.py           — Schema aggregator, re-exports TOOL_SCHEMAS (~31 LOC)
  schemas_identity.py   — Identity & Registration schemas (~123 LOC)
  schemas_locking.py    — Document Locking schemas (~145 LOC)
  schemas_coordination.py — Coordination Action schemas (~59 LOC)
  schemas_change.py     — Change Awareness schemas (~77 LOC)
  schemas_audit.py     — Audit & Status schemas (~43 LOC)
  schemas_visibility.py — Graph & Visibility schemas (~132 LOC)
  dispatch.py          — Tool dispatch table (~48 LOC)
  graphs.py           — Coordination graph loader + validator (~310 LOC)
  visibility.py       — File ownership scan, agent status, file map (~233 LOC)
  assessment.py       — Assessment runner (~397 LOC)
  mcp_server.py       — HTTP MCP server (ThreadedHTTPServer, stdlib only)
  mcp_stdio.py        — Stdio MCP server (requires optional mcp package)
  cli.py              — argparse CLI parser + lazy dispatch (~229 LOC)
  cli_commands.py     — All 26 command handlers (~671 LOC)
  db.py               — SQLite schema + thread-local ConnectionPool
  agent_registry.py   — Agent lifecycle: register, heartbeat, deregister, lineage
  lock_ops.py         — Shared lock primitives
  conflict_log.py     — Conflict recording and querying
  notifications.py    — Change notification storage and retrieval
  tests/              — pytest suite (106 tests, 9 test files)
```

## Zero-Dependency Guarantee

Core modules (`core.py`, `schemas.py`, `dispatch.py`, `graphs.py`, `visibility.py`,
`assessment.py`, `mcp_server.py`, `cli.py`, `cli_commands.py`, `db.py`,
`agent_registry.py`, `lock_ops.py`, `conflict_log.py`, `notifications.py`)
use **only the Python standard library**.

The `mcp` package is **optional** — only needed for the stdio transport shim
(`mcp_stdio.py`). Air-gapped install: `pip install coordinationhub[mcp]`.

## Port Allocation

| Server | Port |
|--------|------|
| Stele | 9876 |
| CoordinationHub | 9877 |
| Chisel | 8377 |
| Trammel | 8737 |
