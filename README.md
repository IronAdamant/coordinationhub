# CoordinationHub

**Multi-agent swarm coordination for coding agents.**

Tracks agent identity and lineage, enforces document locking, detects lock conflicts, propagates coordination context to spawned sub-agents, and provides a shared ground truth for "who is doing what" across all LLMs and IDEs.

Part of the **Stele + Chisel + Trammel + CoordinationHub** quartet.

## Features

- **Agent identity & lineage** — Hierarchical agent IDs with parent-child relationships
- **Document locking** — TTL-based exclusive/shared locks with force-steal and conflict recording
- **Cascade orphaning** — Children re-parented to grandparent when parent dies
- **Change notifications** — Polling-based change awareness across agents
- **Conflict audit log** — Full history of lock steals and ownership violations
- **Zero third-party dependencies in core** — Supply chain security is non-negotiable

## Quickstart

```bash
# HTTP server (stdlib only)
coordinationhub serve --port 9877

# Or with Claude Code's MCP config (HTTP):
{
  "mcpServers": {
    "coordinationhub": {
      "command": "coordinationhub",
      "args": ["serve", "--port", "9877"]
    }
  }
}

# Stdio transport (requires optional mcp package):
pip install coordinationhub[mcp]
coordinationhub serve-mcp

# Claude Code MCP stdio config:
{
  "mcpServers": {
    "coordinationhub": {
      "command": "coordinationhub",
      "args": ["serve-mcp"]
    }
  }
}
```

## CLI Commands

```bash
coordinationhub status                        # System status summary
coordinationhub register <agent_id>           # Register agent
coordinationhub register <agent_id> --parent-id <parent>  # Spawn sub-agent
coordinationhub heartbeat <agent_id>          # Send heartbeat (call every 30s)
coordinationhub list-agents                   # List active agents
coordinationhub acquire-lock <path> <agent_id>  # Acquire lock
coordinationhub release-lock <path> <agent_id>  # Release lock
coordinationhub lock-status <path>            # Check lock state
coordinationhub broadcast <agent_id> <msg>    # Announce to siblings
coordinationhub notify-change <path> <type> <agent_id>  # Record change
coordinationhub get-conflicts                # Query conflict log
coordinationhub serve --port 9877             # Start HTTP server
```

## 17 MCP Tools

| Tool | Purpose |
|------|---------|
| `register_agent` | Register and get coordination context bundle |
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
| `broadcast` | Announce intention to siblings |
| `wait_for_locks` | Poll until locks are released |
| `notify_change` | Record a change event |
| `get_notifications` | Poll for change notifications |
| `prune_notifications` | Clean up old notifications |
| `get_conflicts` | Query the conflict log |
| `status` | System status summary |

## Agent ID Format

```
hub.12345.0         — root agent (namespace.PID.sequence)
hub.12345.0.0       — child of hub.12345.0
hub.12345.0.1       — another child of hub.12345.0
hub.12345.0.0.0     — grandchild of hub.12345.0
```

## Coordination Context Bundle

When an agent registers (or spawns a sub-agent), the response includes:

```json
{
  "agent_id": "hub.12345.0",
  "parent_id": null,
  "worktree_root": "/path/to/project",
  "registered_agents": [...],
  "active_locks": [...],
  "pending_notifications": [...],
  "coordination_urls": {
    "coordinationhub": "http://localhost:9877",
    "stele": "http://localhost:9876",
    "chisel": "http://localhost:8377",
    "trammel": "http://localhost:8737"
  }
}
```

Parent agents pass this bundle to spawned sub-agents so they know the coordination URLs and current state.

## Port Allocation

| Server | Port |
|--------|------|
| Stele | 9876 |
| CoordinationHub | 9877 |
| Chisel | 8377 |
| Trammel | 8737 |

## Architecture

```
coordinationhub/
  core.py              — CoordinationEngine, all 17 tool methods
  schemas.py           — JSON Schema + dispatch table (shared)
  mcp_server.py        — HTTP server (ThreadedHTTPServer, stdlib only)
  mcp_stdio.py         — stdio MCP server (requires optional mcp package)
  cli.py               — argparse CLI (24 subcommands)
  db.py                — SQLite schema + thread-local connection pool
  agent_registry.py    — Agent lifecycle: register, heartbeat, lineage
  lock_ops.py          — Shared lock primitives
  conflict_log.py      — Conflict recording and querying
  notifications.py     — Change notification storage and retrieval
```

## Zero-Dependency Guarantee

Core modules (`core.py`, `schemas.py`, `mcp_server.py`, `cli.py`, `db.py`, `agent_registry.py`, `lock_ops.py`, `conflict_log.py`, `notifications.py`) use **only the Python standard library**. No `requests`, `httpx`, `aiohttp`, or external HTTP libraries.

The `mcp` package is **optional** — only needed for the stdio transport shim (`mcp_stdio.py`). Air-gapped install: `pip install -e . --no-deps`.
