# HA Coordinator + Spawner Implementation Plan

**Version:** 0.7.0 (planned)
**Last updated:** 2026-04-13

---

## Context

Review Seventeen identified two design-level gaps deferred from v0.6.1:

1. **Centralized Bottleneck** — CoordinationHub is a single-writer SQLite coordinator. If the coordinator process dies, all agents lose their coordination authority. The review proposed replication/leader-election but that conflicts with the zero-third-party-deps constraint.

2. **Agent Spawning External** — CoordinationHub tracks agents but doesn't spawn them. The review proposed a `ScalingCoordinator` that spawns OS processes, which is process management, not coordination.

Both must remain **zero-dependency** (stdlib only).

---

## Solution Architecture

### HA Coordinator: SQLite WAL + Lease-Based Leadership

SQLite in WAL mode supports **one writer + many readers**. The "coordinator replica" pattern:

- **Writer lease**: One agent holds a named lock (`COORDINATOR_LEADER`) with a short TTL (10s). It must refresh within the TTL or the lease is released. This is leader election without a separate service.
- **Read replicas**: Any agent can open the SQLite DB in read-only mode to read agent state, locks, notifications — without going through the writer.
- **Claim protocol**: On startup, an agent attempts to acquire `COORDINATOR_LEADER`. If it gets it, it becomes the active coordinator. If it doesn't, it becomes a read-replica that polls for leadership changes.

**Key insight**: The lock is advisory — we use the existing `document_locks` table (or a new `coordinator_leases` table) with TTL-based locking. The "leader" is simply the agent that holds the lease. All writes go through the leaseholder. Read replicas read directly from SQLite WAL file.

**Constraint satisfied**: Zero new dependencies. SQLite WAL is built-in. Lock TTL refresh is already implemented (`refresh_lock`).

### Agent Spawner: Claude Code Sub-Agent Registry + Auto-Registration

Instead of spawning OS processes (which is Claude Code's job), the spawner wraps Claude Code's own sub-agent lifecycle:

- **Spawner agent**: A root agent calls `spawn_subagent(type, description)` — this creates a pending sub-agent record, generates a child ID, and awaits registration.
- **Auto-registration hook**: When Claude Code fires `SubagentStart`, the hook automatically registers the sub-agent with the pending spawner record, correlating via `tool_use_id` (already implemented for `PreToolUse[Agent]` correlation).
- **Alive tracking**: The spawner monitors heartbeats and can request deregistration of stale sub-agents.

**Key insight**: The spawner doesn't fork processes — it manages the *registration lifecycle* of agents that Claude Code spawns. The coordination context bundle is passed to the sub-agent so it knows who its parent is and what locks are held.

---

## Implementation Stages

- [x] **Stage 1: HA Coordinator — Lease Table + Leadership Acquisition**
- [x] **Stage 2: HA Coordinator — Read Replica Mode**
- [x] **Stage 3: HA Coordinator — Leadership Transfer on Lease Expiry**
- [x] **Stage 4: HA Coordinator — CLI commands + Assessment**
- [x] ~~**Stage 5: Spawner — Pending Sub-Agent Registry**~~ ✅
- [x] **Stage 6: Spawner — Auto-Registration Hook Integration**
- [x] **Stage 7: Spawner — CLI Commands** ✅
- [x] **Stage 8: Spawner — Health Polling + Deregistration Requests** ✅

---

## Stage 1: HA Coordinator — Lease Table + Leadership Acquisition

### Objective
Add a `coordinator_leases` table and `acquire_coordinator_lease` / `refresh_coordinator_lease` / `release_coordinator_lease` methods. An agent calls `acquire_coordinator_lease` on startup — if it gets the lock it becomes the active coordinator; if not it knows it's a replica.

### Files to create/modify

| File | Change |
|------|--------|
| `coordinationhub/db.py` | Add `coordinator_leases` table schema (migration v14), index |
| `coordinationhub/leases.py` | **NEW** — zero-deps lease primitives: `acquire_lease`, `refresh_lease`, `release_lease`, `get_lease_holder` |
| `coordinationhub/core_leases.py` | **NEW** — `LeaseMixin` host class, delegates to `leases.py` |
| `coordinationhub/core.py` | Add `LeaseMixin` to inheritance, add `acquire_coordinator_lease`, `refresh_coordinator_lease`, `release_coordinator_lease`, `is_leader`, `get_leader` methods |
| `coordinationhub/schemas.py` | Add `acquire_coordinator_lease`, `refresh_coordinator_lease`, `release_coordinator_lease`, `get_leader` schemas |
| `coordinationhub/dispatch.py` | Add dispatch entries for lease tools |
| `coordinationhub/cli.py` | Add CLI: `acquire-coordinator-lease`, `refresh-coordinator-lease`, `release-coordinator-lease`, `get-leader` |
| `coordinationhub/cli_leases.py` | **NEW** — CLI handlers for lease commands |

### Schema

```sql
CREATE TABLE coordinator_leases (
    lease_name    TEXT PRIMARY KEY,
    holder_id     TEXT NOT NULL,
    acquired_at   REAL NOT NULL,
    ttl           REAL NOT NULL,
    expires_at    REAL NOT NULL
)
```

### CLI

```bash
coordinationhub acquire-coordinator-lease --ttl 10
coordinationhub refresh-coordinator-lease
coordinationhub release-coordinator-lease
coordinationhub get-leader
```

---

## Stage 2: HA Coordinator — Read Replica Mode

### Objective
Add a `CoordinationEngine.read_only()` context manager that opens the SQLite connection in read-only mode (`?mode=ro`). Read replica agents can query `list_agents`, `get_notifications`, `get_lock_status`, `get_agent_tree` without round-tripping through the writer.

### Files to modify

| File | Change |
|------|--------|
| `coordinationhub/_storage.py` | Add `read_only()` context manager using `?mode=ro` URI |
| `coordinationhub/core.py` | Add `read_only_engine()` method that returns a read-only view |
| `coordinationhub/cli.py` | Add `--replica` flag to commands that support read-only mode |

### Behavior

- Commands that don't modify state (`list-agents`, `get-agent-status`, `get-lock-status`, `dashboard`, etc.) can be called with `--replica` to read directly from the WAL
- Write commands always go through the current leaseholder

---

## Stage 3: HA Coordinator — Leadership Transfer on Lease Expiry

### Objective
If the leader fails to refresh its lease within the TTL, any replica can call `claim_leadership()` — it attempts to acquire the `COORDINATOR_LEADER` lease. If successful, it becomes the new leader and must rebuild any in-memory state from the DB.

### Files to modify

| File | Change |
|------|--------|
| `coordinationhub/leases.py` | Add `claim_leadership()` function that uses `BEGIN IMMEDIATE` for atomic acquisition |
| `coordinationhub/core_leases.py` | Add `claim_leadership()` method to `LeaseMixin` |
| `coordinationhub/schemas.py` | Add `claim_leadership` schema |
| `coordinationhub/dispatch.py` | Add dispatch entry |
| `coordinationhub/cli.py` | Add `claim-leadership` CLI command |

### Safety

- Leadership transfer uses `BEGIN IMMEDIATE` so two replicas racing to claim leadership are serialized
- Old leader's lease is not explicitly revoked — it expires naturally via TTL, and the new leader's `acquired_at` > old leader's `expires_at` means the old lease is stale

---

## Stage 4: HA Coordinator — CLI Commands + Assessment

### Objective
Add `coordinationhub leader-status` and `coordinationhub ha-dashboard` commands showing lease state, replica count, and last-seen leader heartbeat.

### Files to modify

| File | Change |
|------|--------|
| `coordinationhub/cli_leases.py` | Add `cmd_leader_status`, `cmd_ha_dashboard` |
| `coordinationhub/cli_commands.py` | Re-export new handlers |
| `coordinationhub/assessment.py` | Add `leader_stability` metric: scores whether the coordinator lease is consistently held by one agent vs flapping between multiple |

---

## Stage 5: Spawner — Pending Sub-Agent Registry

### Objective
Add a `pending_spawner_tasks` table (similar pattern to `pending_subagent_tasks`) and primitives to track a parent agent's intent to spawn a sub-agent before Claude Code fires `SubagentStart`.

### Files to create/modify

| File | Change |
|------|--------|
| `coordinationhub/db.py` | Add `pending_spawner_tasks` table (migration v15) |
| `coordinationhub/spawner.py` | **NEW** — zero-deps spawner primitives: `stash_pending_spawn`, `consume_pending_spawn`, `prune_stale_spawns` |
| `coordinationhub/core_spawner.py` | **NEW** — `SpawnerMixin` with `spawn_subagent`, `get_pending_spawns`, `await_subagent_registration` methods |
| `coordinationhub/core.py` | Add `SpawnerMixin` to inheritance |

### Schema

```sql
CREATE TABLE pending_spawner_tasks (
    id                TEXT PRIMARY KEY,
    parent_agent_id  TEXT NOT NULL,
    subagent_type    TEXT,
    description      TEXT,
    prompt           TEXT,
    created_at       REAL NOT NULL,
    consumed_at      REAL,
    status           TEXT DEFAULT 'pending'  -- pending | registered | expired
)
```

### MCP Tools

| Tool | Description |
|------|-------------|
| `spawn_subagent` | Register intent to spawn a sub-agent; returns sub-agent ID and awaits auto-registration |
| `get_pending_spawns` | Get pending spawn requests for this parent agent |
| `await_subagent_registration` | Poll until a pending spawn is consumed (sub-agent registered) or timeout |

---

## Stage 6: Spawner — Auto-Registration Hook Integration

### Objective
Extend `handle_subagent_start` in the Claude Code hook to check for a pending spawn from the same parent agent, and auto-populate the `pending_spawner_tasks` status.

### Files to modify

| File | Change |
|------|--------|
| `coordinationhub/hooks/claude_code.py` | In `handle_subagent_start`: after registering the agent, call `consume_pending_spawn` to mark the pending spawn as `registered` |

### Correlation

The existing `PreToolUse[Agent] → SubagentStart` correlation uses `tool_use_id`. The spawner correlation uses `session_id` + `parent_agent_id` (the spawning agent's ID). When a `SubagentStart` event fires, the hook checks if there's a pending spawn for that `(session_id, parent_agent_id)` and marks it consumed.

---

## Stage 7: Spawner — CLI Commands

### Objective
Add CLI commands for spawner visibility and control.

### Files to modify

| File | Change |
|------|--------|
| `coordinationhub/cli_spawner.py` | **NEW** — CLI: `spawn-subagent`, `list-pending-spawns`, `cancel-spawn` |
| `coordinationhub/cli_commands.py` | Re-export spawner handlers |
| `coordinationhub/schemas.py` | Add `spawn_subagent`, `get_pending_spawns`, `cancel_spawn` schemas |
| `coordinationhub/dispatch.py` | Add dispatch entries |

### CLI

```bash
coordinationhub spawn-subagent <parent_id> --type Explore --description "Analyze X"
coordinationhub list-pending-spawns <parent_id>
coordinationhub cancel-spawn <spawn_id>
```

---

## Stage 8: Spawner — Health Polling + Deregistration Requests

### Objective
Allow a spawner parent to request deregistration of a stale child, and poll for the child's heartbeat to detect when it's truly stopped.

### Files to modify

| File | Change |
|------|--------|
| `coordinationhub/spawner.py` | Add `request_deregistration`, `await_agent_stopped` |
| `coordinationhub/core_spawner.py` | Add `request_subagent_deregistration`, `await_subagent_stopped` methods |
| `coordinationhub/schemas.py` | Add `request_subagent_deregistration`, `await_subagent_stopped` schemas |
| `coordinationhub/dispatch.py` | Add dispatch entries |
| `coordinationhub/cli_spawner.py` | Add CLI: `request-subagent-deregistration`, `await-subagent-stopped` |

### CLI

```bash
coordinationhub request-subagent-deregistration <parent_id> <child_id>
coordinationhub await-subagent-stopped <child_id> --timeout 30
```

---

## Verification

```bash
# Full test suite must pass
python -m pytest tests/ -v

# HA: leader election
coordinationhub acquire-coordinator-lease --ttl 10
# (in another terminal) coordinatorhub get-leader
# (wait 15s) coordinatorhub get-leader  # should show expired or new leader

# HA: read replica
coordinationhub list-agents --replica

# HA: leadership transfer
# (lease expires) coordinatorhub claim-leadership
# coordinatorhub get-leader  # should show new leader

# Spawner: spawn and register
coordinationhub spawn-subagent hub.cc.main --type Explore --description "Analyze X"
# (Claude Code spawns Explore sub-agent)
coordinationhub list-pending-spawns hub.cc.main  # should show registered

# Spawner: health polling
coordinationhub await-subagent-stopped hub.cc.main.Explore.0 --timeout 30
```

---

## Counts (expected at completion)

| Item | Current | After Stage 8 |
|------|---------|----------------|
| MCP tools | 61 | 71 (+10) |
| CLI commands | 64 | 75 (+11) |
| Schema version | 13 | 15 |
| New modules | — | 5 (`leases.py`, `core_leases.py`, `spawner.py`, `core_spawner.py`, `cli_leases.py`, `cli_spawner.py`) |

---

## Open Questions — RESOLVED

- [x] Q1: HA read replica connection path? → **Option A**: Direct SQLite URI (`?mode=ro`). Clean separation, WAL reader never interferes with writer pool.
- [x] Q2: Spawner correlation key? → **Option B**: `(parent_agent_id, subagent_type)`. Same pattern as PreToolUse[Agent]→SubagentStart, avoids cross-type collisions.
- [x] Q3: Spawner deregistration? → **Option C**: Hybrid. Graceful `request_stop` flag first, `deregister_agent` hard fallback after timeout. SIGTERM→SIGKILL convention.

---

### Original deliberations (retained for reference)

- [ ] Should HA read replica mode bypass the connection pool and open a direct SQLite URI? (Pool is thread-local so read-replica needs a different connection path)
I go with Option A. I'm willing to accept the tradeoff here. So this will do. Below:
  Question 1: HA Read Replica — Separate Connection vs Pool Bypass?
                                                                                                        
  Current state: The ConnectionPool gives each thread one SQLite connection, reused. All writes and
  reads go through this thread-local connection.

  Option A — Open a direct SQLite URI for read replicas

  conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

  Pros:
  - Clean separation — writer pool and read replica are completely independent
  - No pool pollution — read-only connections never enter the thread-local slot                         
  - SQLite WAL readers read from the WAL file directly, no writer coordination needed
  - Thread-safe by SQLite design — WAL mode allows concurrent reads without locking the writer          
                                                                  
  Cons:
  - Two different connection patterns in the codebase (pool for writes, direct URI for reads)
  - The read replica can't see uncommitted writer changes (but WAL is flushed regularly so this is fine
  for coordination purposes)
  - If the WAL hasn't been checkpointed recently, a read replica might see slightly stale state — but   
  for coordination visibility this is acceptable

- [ ] Should the spawner correlate on `parent_agent_id` alone, or `(parent_agent_id, subagent_type)` to avoid cross-type collisions?
I go with Option B, especially due to Agents having tendency to spawn their own sub agent. I suspect all future version of LLMs, like yourself will be doing it frequently:
  Option B — Correlate on (parent_agent_id, subagent_type)                                              
                                                                  
  Pros:
  - Parallel spawns of different types (Explore + Plan + general-purpose) don't collide — each
  subagent_type bucket is independent                                                         
  - Same pattern as the existing PreToolUse[Agent] → SubagentStart correlation which uses (session_id, 
  subagent_type) specifically to avoid cross-type collisions                                           
  - Preserves semantic distinction: "parent.Explore.0" and "parent.Plan.0" are tracked separately even
  if spawned simultaneously                                                                           
  - More robust — if a parent spawns two Explore agents in parallel, they get separate IDs anyway, so
  the correlation naturally separates
                                                                                                        
  Cons:
  - If a parent spawns two Explore agents of the same type in parallel, the first SubagentStart still   
  resolves both — but this is an inherent limitation of the event correlation problem, not the key
  design

- [ ] Should spawner deregistration request be a hard kill (`deregister_agent`) or a graceful request (`request_stop`)?
  Option C — Hybrid: graceful request + hard kill fallback        

  The spawner calls request_stop(child_id) which sets the flag. After a configurable timeout (default
  30s), if the child hasn't called deregister_agent itself (implying it saw the flag and acted on it),
  the spawner escalates to deregister_agent.

  Pros:
  - Best of both worlds — tries graceful first, falls back to authoritative
  - The parent agent controls the timeout via await_subagent_stopped(child_id, timeout=30)
  - Most robust — works for cooperative agents (graceful exit) and uncooperative ones (hard kill after
  timeout)                                                                                              
   
  Cons:                                                                                                 
  - Most complex — requires state machine in the spawner: pending → stop_requested → (deregistered | 
  timeout → force_deregistered)
  - The child needs to actively poll get_stop_request — if it's running a tool that blocks for minutes,
  it won't respond until that tool finishes
