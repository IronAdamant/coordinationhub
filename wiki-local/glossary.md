# CoordinationHub Glossary

**Version:** 0.1.0

## Agent

An independent LLM process or thread registered with CoordinationHub. Identified by a unique agent ID. Has a lifecycle: registered → active → stale/deregistered.

## Agent ID

A globally unique identifier of the form `hub.12345.0` (root agent: namespace.PID.sequence) or `hub.12345.0.0` (child: parent_id.sequence). PID distinguishes agents from different processes; sequence ensures uniqueness within that process.

## Agent Lineage

The parent→child→grandchild relationship tree between agents. Tracked via the `lineage` table and queryable via `get_lineage` and `get_siblings` tools.

## Cascade Orphaning

When an agent dies (becomes stale and is reaped), its direct children are re-parented to the grandparent (or become root-level agents if no grandparent exists). This prevents permanent orphaning of agent sub-trees.

## Change Notification

A time-ordered record of a document change written by `notify_change`. Other agents poll via `get_notifications` to detect changes made by their siblings.

## Conflict Log

A record of lock steals and ownership violations. Written when `acquire_lock(force=True)` overwrites an existing lock held by another agent. Queryable via `get_conflicts`.

## Coordination Context Bundle

A JSON payload returned by `register_agent` containing:
- The registering agent's ID and parent ID
- Worktree root path
- List of all registered agents
- All currently active locks
- Recent change notifications
- CoordinationHub HTTP URL (settable via `COORDINATIONHUB_COORDINATION_URL`)

Parent agents pass this bundle to spawned sub-agents.

## Document Lock

A TTL-based advisory lock on a document path. Types: `exclusive` (default, one owner) or `shared` (multiple readers). Lock expires if not refreshed before `locked_at + lock_ttl`. Force-acquire steals the lock and records a conflict.

## Force Steal

Calling `acquire_lock(force=True)` when a lock is held by another agent. Records the steal in the conflict log before taking ownership. Used when an agent believes a lock holder has died and the lock should be reclaimed.

## Heartbeat

A periodic call to `heartbeat(agent_id)` that updates the agent's `last_heartbeat` timestamp. Also triggers expired lock cleanup. Call at least every 30 seconds.

## Lineage Table

SQLite table mapping `child_id → parent_id` with a `spawned_at` timestamp. Written when an agent registers with a `parent_id`. Used to walk ancestor chains and build descendant trees.

## Lock Conflict

An entry in `lock_conflicts` recording that agent B stole a lock held by agent A. Includes `conflict_type` (e.g., `lock_stolen`), resolution (e.g., `force_overwritten`), and optional details JSON.

## MCP (Model Context Protocol)

The client-server protocol used by Claude Code, Claude Desktop, and other MCP-aware clients to communicate with tool servers. CoordinationHub implements both stdio and HTTP transports.

## Namespace

The prefix of an agent ID before the PID. Default: `hub`. Allows multiple independent agent swarms to coexist without ID collision.

## Notification Pruning

Cleanup of old notifications via `prune_notifications`. Supports age-based deletion (`max_age_seconds`) and count-based deletion (keep newest `max_entries`). Prevents unbounded table growth.

## Orphaning

When an agent's parent dies, the child agent is re-parented to the grandparent (cascade orphaning). An agent without a parent is a root agent.

## Polling

The coordination pattern where agents periodically call `get_notifications` or `get_lock_status` to discover changes made by other agents. CoordinationHub does not push notifications — agents must poll.

## Project Root

The directory containing a `.git` folder, detected by walking up from CWD. Used to normalize document paths to project-relative form and to resolve default storage directories.

## Reaping

The process of marking stale agents as stopped and releasing their locks. Triggered by `reap_stale_agents` or automatically during `heartbeat`. Cascade orphans children before stopping the parent.

## Stale Agent

An agent whose `last_heartbeat` is older than `stale_timeout` (default: 600 seconds). Reaped by `reap_stale_agents`. Its locks are released and its children are orphaned.

## TTL (Time-To-Live)

The duration in seconds after which a lock expires. Default: 300 seconds. Refreshable by the lock owner via `refresh_lock`.

## Worktree Root

The filesystem root of a git worktree. Used to scope lock and notification storage. Multiple worktrees of the same repo share the coordination DB via the git common directory.
