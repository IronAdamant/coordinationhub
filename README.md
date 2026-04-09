# CoordinationHub

**Coordination hub for multi-agent coding swarms — root agent as project manager, spawned agents as team members, zero third-party deps in core.**

CoordinationHub tracks who is doing what across a swarm. The root agent acts as project manager (top-level coordinator), spawning child agents that act as team leaders or team members. Files are locked and owned by Agent ID — every agent and human can see the full assignment tree at any time.

Works standalone or alongside **Stele + Chisel + Trammel + CoordinationHub**.

## How It Works

```
Root Agent (project manager)
├── Child Agent A (team leader)
│   ├── Grandchild A.0 (team member — does the work)
│   └── Grandchild A.1 (team member)
├── Child Agent B (team leader)
│   └── Grandchild B.0 (team member)
└── Child Agent C (team member)
```

Every node in the tree is a registered agent with an Agent ID. Files are locked by Agent ID so the project manager can see who holds what. Children are re-parented to the grandparent if their parent dies (cascade orphaning) — no agent is permanently stranded.

## Coordination Model

| Role | Description |
|------|-------------|
| **Root agent** | Project manager — top-level coordinator. Spawns team leaders. Does not do the work itself. |
| **Team leader** | Child of root (or grandchild). Spawns team members, assigns tasks, reviews work. |
| **Team member** | Leaf node. Does the work: writes code, runs tests, calls `notify_change` when done. |

**Agents communicate via change notifications, not messages.** A team member calls `notify_change(path, 'modified', agent_id)` after writing a shared file. Teammates poll `get_notifications` to discover what changed. `broadcast` checks sibling lock state before a significant action — it does not send or store messages; the calling agent decides what to do with the conflict data.

`acquire_lock` enforces exclusive access: a team member locks a file before writing, releases it when done. Review the full agent tree at any time:

```bash
coordinationhub agent-tree                    # oldest root agent as root
coordinationhub agent-tree hub.12345.0        # specific agent as root
coordinationhub agent-status <agent_id> --tree
```

- **Declarative coordination graphs** — agents, handoffs, escalation rules, and assessment
  criteria defined in `coordination_spec.yaml` or `coordination_spec.json` at project root.
- **File ownership tracking** — SQLite `file_ownership` table populated by `scan_project`;
  maps every tracked file to its responsible Agent ID.
- **Visibility layer** — MCP tools + `coordinationhub dashboard` CLI command for live
  human/LLM-readable status.
- **Assessment runner** — `coordinationhub assess --suite <file>` scores graph fidelity
  against MiniMax coordination test traces. 5 real metric scorers.
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
coordinationhub assess --suite my_minimax_tests.json --graph-agent-id planner
```

## Coordination Graph

Place `coordination_spec.yaml` (or `.json`) at your project root.
Example files are provided in the repo root:

- [`coordination_spec.yaml`](coordination_spec.yaml) — YAML format
- [`coordination_spec.json`](coordination_spec.json) — JSON format

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
    - spawn_propagation
```

## 29 MCP Tools

| Tool | Purpose |
|------|---------|
| `register_agent` | Register and get coordination context + responsibilities from graph |
| `heartbeat` | Keep agent alive (updates timestamp only) |
| `deregister_agent` | Remove agent, orphan children, release locks |
| `list_agents` | List registered agents with staleness |
| `get_lineage` | Get ancestors and descendants of an agent |
| `get_siblings` | Get agents sharing the same parent |
| `acquire_lock` | Acquire exclusive or shared lock on a document |
| `release_lock` | Release a held lock |
| `refresh_lock` | Extend lock TTL without releasing |
| `get_lock_status` | Check if a document is locked |
| `list_locks` | List all active locks (optionally filtered by agent) |
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
| `get_agent_tree` | Hierarchical agent tree for human/LLM review (nested + plain-text) |

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
coordinationhub agent-status <agent_id> --tree
coordinationhub agent-tree <agent_id>
coordinationhub agent-tree

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
coordinationhub list-locks
coordinationhub list-locks --agent-id <agent_id>
coordinationhub release-agent-locks <agent_id>
coordinationhub reap-expired-locks
coordinationhub reap-stale-agents

# Coordination
coordinationhub broadcast <agent_id> [--document-path <path>]
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
  core.py             — CoordinationEngine: all 28 tool methods (~431 LOC)
  _storage.py         — CoordinationStorage: SQLite pool, path resolution, lifecycle (~121 LOC)
  paths.py            — Project-root detection and path normalization (~48 LOC)
  context.py          — Context bundle builder for register_agent responses (~100 LOC)
  schemas.py           — Schema aggregator, re-exports TOOL_SCHEMAS (~31 LOC)
  schemas_identity.py   — Identity & Registration schemas (~123 LOC)
  schemas_locking.py    — Document Locking schemas (~145 LOC)
  schemas_coordination.py — Coordination Action schemas (~59 LOC)
  schemas_change.py     — Change Awareness schemas (~77 LOC)
  schemas_audit.py     — Audit & Status schemas (~43 LOC)
  schemas_visibility.py — Graph & Visibility schemas (8 tools, ~156 LOC)
  dispatch.py          — Tool dispatch table (~48 LOC)
  graphs.py           — Thin aggregator + graph auto-mapping (~105 LOC)
  graph_validate.py   — Pure validation functions (~131 LOC)
  graph_loader.py     — File loading (YAML/JSON) and spec auto-detection (~49 LOC)
  graph.py            — CoordinationGraph in-memory object (~66 LOC)
  visibility.py       — Thin re-export aggregator (~15 LOC)
  scan.py             — File ownership scan, graph-role-aware (~105 LOC)
  agent_status.py     — Agent status query, file map, and agent tree helpers (~225 LOC)
  responsibilities.py — Agent role/responsibilities storage (~35 LOC)
  agent_registry.py   — Thin re-export aggregator (~23 LOC)
  registry_ops.py     — Agent lifecycle ops (~120 LOC)
  registry_query.py   — Agent registry queries (~142 LOC)
  assessment.py       — Assessment runner, 5 metric scorers (~394 LOC)
  mcp_server.py       — HTTP MCP server (ThreadedHTTPServer, stdlib only)
  mcp_stdio.py        — Stdio MCP server (requires optional mcp package)
  cli.py              — argparse CLI parser + lazy dispatch (~237 LOC)
  cli_commands.py     — Re-exports all CLI handlers (~44 LOC)
  cli_agents.py       — Agent identity & lifecycle CLI commands (~205 LOC)
  cli_locks.py        — Document locking & coordination CLI commands (~214 LOC)
  cli_vis.py          — Change awareness, audit, graph & assessment CLI + agent-tree (~346 LOC)
  db.py               — SQLite schema + thread-local ConnectionPool (~215 LOC)
  lock_ops.py         — Shared lock primitives (~119 LOC)
  conflict_log.py     — Conflict recording and querying (~53 LOC)
  notifications.py    — Change notification storage and retrieval (~115 LOC)
  tests/              — pytest suite (187 tests, 12 test files)
```

## Zero-Dependency Guarantee

Core modules use **only the Python standard library**. The `mcp` package is
**optional** — only needed for `mcp_stdio.py`. Air-gapped install:
`pip install coordinationhub --no-deps`.

## Port Allocation

| Server | Port |
|--------|------|
| Stele | 9876 |
| CoordinationHub | 9877 |
| Chisel | 8377 |
| Trammel | 8737 |
