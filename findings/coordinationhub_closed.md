# CoordinationHub MCP Findings - Review Nine

**Date:** 2026-04-09
**Session:** Review Nine - 6 MCP Challenge Features
**Features Built:** 6 services, 6 route files, 6 test files (18 total)
**Tests Added:** 93 (14+12+14+17+18+18)
**CoordinationHub Challenge Feature:** DistributedTaskExecutor (parallel task execution with locking, deadlock detection, agent handoff)
**Special Focus:** Multi-agent testing via parallel Agent tool invocations

---

## Executive Summary

This session was designed to push CoordinationHub beyond the single-agent file locking observed in Review Eight (27 locks, 0 failures, no contention). We deliberately spawned **6 parallel agents** (4 for features 1-4, then 2 for features 5-6) to force real concurrent file operations. The results are mixed: CoordinationHub tracked 19 document locks and 53 change notifications with zero failures, but **critical multi-agent features remained untestable.** Only 1 of 6 spawned agents appeared in the agents table. Lock conflicts were 0 despite concurrent operations. The hooks-only interface continues to limit what can be observed and validated.

---

## CoordinationHub Database State

| Table | Rows | Notes |
|-------|------|-------|
| agents | 4 | 2 sessions (current + prior), 2 subagents |
| document_locks | 19 | All from current session |
| change_notifications | 53 | Accumulated across sessions |
| lock_conflicts | **0** | No contention detected |
| lineage | 2 | 2 parent-child relationships |
| file_ownership | 0 | Feature never activated |
| coordination_context | 0 | No coordination spec loaded |
| agent_responsibilities | 0 | Never assigned |
| assessment_results | 0 | No assessments run |

---

## Multi-Agent Testing Strategy

### What We Tried

We spawned agents in two waves to maximize concurrent file operations:

**Wave 1 (4 simultaneous agents):**
- Agent A: EventPropagationTracer (3 files: service, route, test)
- Agent B: GranularCoverageMapper (3 files: service, route, test)
- Agent C: ConflictAwarePlanMerger (3 files: service, route, test)
- Agent D: DistributedTaskExecutor (3 files: service, route, test)

**Wave 2 (2 simultaneous agents):**
- Agent E: CodebaseHealthAuditor (3 files: service, route, test)
- Agent F: ChangeImpactPredictor (3 files: service, route, test)

**Main agent (me):** Edited app.js to mount all 6 route sets after waves completed.

Total: 19 file operations (18 new files + 1 app.js edit).

### What CoordinationHub Saw

**Agents table (4 rows, expected 8):**

| Agent ID | Parent | Status | Notes |
|----------|--------|--------|-------|
| hub.cc.6cac5227-543 | (root) | stopped | Previous session (Review Eight) |
| hub.cc.6cac5227-543.agent.0 | hub.cc.6cac5227-543 | stopped | Review Eight subagent |
| hub.cc.11b29c44-156 | (root) | active | Current session root agent |
| hub.cc.11b29c44-156.agent.0 | hub.cc.11b29c44-156 | stopped | **Only 1 subagent registered** |

**Critical finding:** We spawned 6 agents via the Agent tool, but only 1 appears as a subagent in CoordinationHub. The `SubagentStart` hook appears to fire only once (for the first agent), or the subsequent agents overwrite the same `agent.0` entry, or the hook doesn't fire for agents that share the same session context.

**Lineage table (2 rows):**

| Parent | Child | Timestamp |
|--------|-------|-----------|
| hub.cc.6cac5227-543 | hub.cc.6cac5227-543.agent.0 | Review Eight |
| hub.cc.11b29c44-156 | hub.cc.11b29c44-156.agent.0 | Current session |

Only 1 parent-child lineage recorded per session, confirming that multiple Agent tool invocations do NOT create distinct agent entries.

---

## Document Lock Analysis

**19 locks acquired, 0 failures, 0 conflicts.**

All 19 locks are attributed to `hub.cc.11b29c44-156` (the root agent), NOT to any subagent:

| File | Lock Holder | TTL |
|------|-------------|-----|
| src/services/eventPropagationTracer.js | hub.cc.11b29c44-156 | 600s |
| src/api/routes/eventTracerRoutes.js | hub.cc.11b29c44-156 | 600s |
| tests/services/eventPropagationTracer.test.js | hub.cc.11b29c44-156 | 600s |
| src/services/granularCoverageMapper.js | hub.cc.11b29c44-156 | 600s |
| src/api/routes/coverageMapperRoutes.js | hub.cc.11b29c44-156 | 600s |
| tests/services/granularCoverageMapper.test.js | hub.cc.11b29c44-156 | 600s |
| src/services/conflictAwarePlanMerger.js | hub.cc.11b29c44-156 | 600s |
| tests/services/conflictAwarePlanMerger.test.js | hub.cc.11b29c44-156 | 600s |
| src/services/distributedTaskExecutor.js | hub.cc.11b29c44-156 | 600s |
| src/api/routes/taskExecutorRoutes.js | hub.cc.11b29c44-156 | 600s |
| tests/services/distributedTaskExecutor.test.js | hub.cc.11b29c44-156 | 600s |
| src/services/codebaseHealthAuditor.js | hub.cc.11b29c44-156 | 600s |
| src/api/routes/healthAuditorRoutes.js | hub.cc.11b29c44-156 | 600s |
| tests/services/codebaseHealthAuditor.test.js | hub.cc.11b29c44-156 | 600s |
| src/services/changeImpactPredictor.js | hub.cc.11b29c44-156 | 600s |
| src/api/routes/impactPredictorRoutes.js | hub.cc.11b29c44-156 | 600s |
| tests/services/changeImpactPredictor.test.js | hub.cc.11b29c44-156 | 600s |
| src/api/app.js | hub.cc.11b29c44-156 | 600s |

**Key observation:** Even though 6 separate agent processes wrote these files, ALL locks show the root agent as holder. This means either:
1. Subagent Write/Edit operations are attributed to the parent session in CoordinationHub hooks
2. The PreToolUse hook resolves the agent ID to the session root, not the specific subagent
3. Subagents inherit the parent's CoordinationHub identity

**This is architecturally important:** CoordinationHub cannot distinguish WHICH subagent wrote which file. In a real multi-agent coordination scenario (e.g., 3 agents working on different parts of a feature), CoordinationHub would attribute all work to a single agent identity, making coordination graphs, ownership tracking, and conflict detection impossible.

---

## Change Notifications

53 change notifications total (accumulated across sessions). The 10 most recent:

| # | File | Type | Agent |
|---|------|------|-------|
| 53 | src/api/app.js | modified | hub.cc.11b29c44-156 |
| 52 | tests/services/changeImpactPredictor.test.js | modified | hub.cc.11b29c44-156 |
| 51 | src/api/routes/impactPredictorRoutes.js | modified | hub.cc.11b29c44-156 |
| 50 | tests/services/codebaseHealthAuditor.test.js | modified | hub.cc.11b29c44-156 |
| 49 | src/services/changeImpactPredictor.js | modified | hub.cc.11b29c44-156 |
| 48 | src/api/routes/healthAuditorRoutes.js | modified | hub.cc.11b29c44-156 |
| 47 | src/services/codebaseHealthAuditor.js | modified | hub.cc.11b29c44-156 |
| 46 | tests/services/distributedTaskExecutor.test.js | modified | hub.cc.11b29c44-156 |
| 45 | tests/services/conflictAwarePlanMerger.test.js | modified | hub.cc.11b29c44-156 |
| 44 | src/api/routes/taskExecutorRoutes.js | modified | hub.cc.11b29c44-156 |

All notifications attribute changes to the root agent. The PostToolUse(Write|Edit) hook fires correctly for every file operation but cannot distinguish subagent identity.

**Timing analysis:** Notifications 44-46 have nearly identical timestamps (~1775715325-1775715382), confirming that Wave 1 agents wrote files concurrently. Despite this concurrency, no lock conflicts were recorded because each agent wrote to DIFFERENT files.

---

## Feature 4: DistributedTaskExecutor (CoordinationHub Challenge)

### Design Intent
The DistributedTaskExecutor mirrors CoordinationHub's own architecture:

| Concept | CoordinationHub | DistributedTaskExecutor |
|---------|----------------|------------------------|
| Agent registry | SQLite agents table | In-memory Map |
| File locking | SQLite document_locks with TTL | In-memory Map with wait queues |
| Deadlock detection | Not implemented | Wait-for graph with DFS cycle detection |
| Lock contention | lock_conflicts table (always 0) | Explicit contention tracking |
| Task coordination | coordination_context (unused) | Priority queue with dependency resolution |
| Agent handoff | Not implemented | Explicit handoff with lock transfer |
| Assessment | 5-metric scorer (never run) | Statistics and contention analysis |

### What DistributedTaskExecutor Proves CoordinationHub Cannot Do

1. **Deadlock detection** - The executor builds a wait-for graph and detects cycles. CoordinationHub has no equivalent. If Agent A holds lock on file X and waits for file Y, while Agent B holds Y and waits for X, CoordinationHub would not detect this.

2. **Lock contention analysis** - The executor tracks which resources are most contested and by which agents. CoordinationHub's `lock_conflicts` table is always 0 because all operations are attributed to the same agent.

3. **Priority-based scheduling** - The executor assigns tasks to agents based on priority and capability. CoordinationHub has no task scheduling mechanism.

4. **Agent handoff** - The executor can transfer a task (including its locks) from one agent to another. CoordinationHub has no handoff mechanism.

5. **Execution timeline** - The executor maintains a detailed event log. CoordinationHub's change_notifications provide file-level events but no task-level or coordination-level events.

### Test Results

17 tests pass, covering:
- Agent registration and deregistration
- Lock acquisition, release, and contention
- Wait-for graph construction
- Deadlock detection (A↔B cycle)
- Deadlock resolution (youngest_abort, priority_abort)
- Task scheduling rounds
- Task handoff with lock transfer
- Execution timeline and contention analysis

---

## Bridge Hooks Analysis

### PreToolUse(Write|Edit) → Lock Acquisition
**Status: WORKS** (19/19 locks acquired)
The hook fires for every Write or Edit tool call and acquires an exclusive lock. 600-second TTL. No failures observed.

### PostToolUse(Write|Edit) → Change Notification
**Status: WORKS** (19 notifications from this session)
The hook fires after every Write or Edit and records the change. File path, change type ("modified"), agent ID, and worktree root all correctly populated.

### PostToolUse(mcp__stele-context__index) → Stele Bridge
**Status: UNTESTABLE** - No visible trace in CoordinationHub database from the Stele index call. The bridge may write to coordination_context (which has 0 rows), or it may not fire at all. We cannot verify without direct hook debugging.

### PostToolUse(mcp__trammel__claim_step) → Trammel Bridge
**Status: NOT EXERCISED** - We used `record_step` instead of `claim_step` this session, so this bridge was not triggered. Future sessions should use `claim_step` explicitly to test this bridge.

### SubagentStart/Stop
**Status: PARTIALLY WORKS** - Fires for at least 1 subagent per session. Does NOT fire for all Agent tool invocations (6 agents spawned, only 1 registered).

### SessionStart/End
**Status: WORKS** - Root agent correctly registered at session start. Status set to "active."

---

## Comparison: Review Eight vs Review Nine

| Metric | Review Eight | Review Nine | Delta |
|--------|-------------|-------------|-------|
| Files created | 18 | 18 | Same |
| Locks acquired | 27 | 19 | -8 (fewer total ops) |
| Lock failures | 0 | 0 | Same |
| Lock conflicts | 0 | 0 | Same |
| Agents registered | 2 | 4 | +2 (across sessions) |
| Subagents tracked | 1 | 1 | Same |
| Change notifications | (not measured) | 53 | - |
| Lineage records | 1 | 2 | +1 |
| Agents spawned | 1 | 6 | +5 |
| Agent tracking gap | 0 of 1 | 5 of 6 | WORSE |

**The agent tracking gap widened.** In Review Eight we spawned 1 agent and it was tracked. In Review Nine we spawned 6 and only 1 was tracked. The SubagentStart hook does not scale with Agent tool parallelism.

---

## Features That Remain Untestable in Single-Agent Sessions

Despite spawning multiple agents, the following CoordinationHub features could still not be tested because all operations are attributed to a single agent identity:

1. **Multi-agent lock contention** - Requires two agents to request the same file simultaneously. All our agents wrote different files.
2. **Coordination graphs** - Requires loading a `coordination_spec.yaml` with agent roles and handoff definitions. No spec file exists.
3. **Assessment scoring** - The 5-metric scorer (role_stability, handoff_latency, outcome_verifiability, protocol_adherence, spawn_propagation) requires a multi-agent session with defined roles.
4. **File ownership scanning** - Requires explicit ownership assignment via hooks or API. Never triggered.
5. **Agent responsibilities** - Requires role-based responsibility assignment. Never triggered.
6. **Broadcast/wait_for_locks** - Requires multiple agents competing for shared resources.

### What Would Be Needed to Test These

1. **Two concurrent Claude Code sessions** sharing the same worktree. Each session would register as a separate root agent, and their locks would actually conflict.
2. **A coordination_spec.yaml** defining agent roles, handoff protocols, and file ownership rules.
3. **Deliberate file contention** where both sessions edit the same file within the lock TTL window.
4. **Assessment execution** after a coordinated task completes.

---

## Validated Capabilities (Cumulative)

| Feature | Status | Confidence | Notes |
|---------|--------|------------|-------|
| Agent registration (SessionStart) | WORKS | High | Root agent registered correctly |
| Subagent registration (SubagentStart) | PARTIAL | Medium | 1/6 agents tracked |
| Document locking (PreToolUse) | WORKS | High | 19/19 locks, 0 failures |
| Change notification (PostToolUse) | WORKS | High | 19/19 notifications |
| Lock conflict detection | UNTESTED | N/A | 0 conflicts (all ops to different files) |
| Lineage tracking | WORKS | Medium | Parent-child recorded for 1 subagent |
| Stele bridge hook | UNTESTABLE | N/A | No visible DB trace |
| Trammel bridge hook | NOT EXERCISED | N/A | Used record_step not claim_step |
| File ownership | UNTESTED | N/A | 0 rows in table |
| Coordination context | UNTESTED | N/A | 0 rows, no spec loaded |
| Agent responsibilities | UNTESTED | N/A | 0 rows |
| Assessment scoring | UNTESTED | N/A | 0 rows |
| Coordination graphs | UNTESTED | N/A | No spec file |

---

## Recommendations

### For Users
1. **CoordinationHub works perfectly for single-agent file locking.** 100% success rate across 46 total locks (27 Review Eight + 19 Review Nine).
2. **Do not expect subagent tracking with the Agent tool.** Only 1 of 6 spawned agents was visible to CoordinationHub. Plan coordination manually.
3. **For real multi-agent coordination, use two separate Claude Code sessions** on the same repo. This is the only way to create distinct agent identities in CoordinationHub.

### For CoordinationHub Development
1. **Fix SubagentStart hook** to fire for ALL Agent tool invocations, not just the first. Each agent should get a unique ID (e.g., agent.0, agent.1, agent.2).
2. **Attribute locks to subagent IDs** when available. Currently all locks go to the root session ID, making subagent coordination invisible.
3. **Add a CLI/MCP tool interface** alongside hooks. Direct query tools (like `chisel stats` or `stele map`) would make CoordinationHub observable. Currently it's a black box unless you query the SQLite database directly.
4. **Create a sample coordination_spec.yaml** for RecipeLab so assessment scoring can be tested.
5. **Expose lock history** (not just current locks) so timing and contention patterns can be analyzed after the fact.

---

## Hook Handler Source Code Analysis

The CoordinationHub hook handler lives at:
```
/home/aron/Documents/coding_projects/CoordinationHub/coordinationhub/hooks/claude_code.py
```

### Hook Configuration (from ~/.claude/settings.json)

| Hook Event | Matcher | Timeout | What It Does |
|------------|---------|---------|-------------|
| SessionStart | (all) | 10s | Register root agent |
| PreToolUse | Write\|Edit | 5s | Acquire file lock |
| PostToolUse | Write\|Edit | 5s | Notify change |
| PostToolUse | mcp\_\_stele-context\_\_index | 5s | Bridge: Stele → CoordinationHub |
| PostToolUse | mcp\_\_trammel\_\_claim\_step | 5s | Bridge: Trammel → CoordinationHub |
| SubagentStart | (all) | 5s | Register child agent |
| SubagentStop | (all) | 5s | Deregister child agent |
| SessionEnd | (all) | 10s | Release all locks, deregister |

### Bug 1: SubagentStart Only Registers 1 Agent (Lines 167-182)

The `handle_subagent_start` function:
```python
def _subagent_id(parent_id: str, event: dict) -> str:
    tool_input = event.get("tool_input", {})
    agent_type = tool_input.get("subagent_type", "agent")
    tool_use_id = event.get("tool_use_id", "0")[:6]
    return f"{parent_id}.{agent_type}.{tool_use_id}"
```

The child ID format is `{parent_id}.{agent_type}.{tool_use_id[:6]}`. Each Agent tool call should have a unique `tool_use_id`, producing unique child IDs. But the DB only shows `hub.cc.11b29c44-156.agent.0` — using `"0"` as the tool_use_id.

**Root cause hypotheses (for follow-up investigation):**
1. **`tool_use_id` not populated in the event:** If Claude Code doesn't pass `tool_use_id` in the SubagentStart event JSON, the default `"0"` is used, and all agents get the same ID. Then `_ensure_registered` sees the agent already exists and just heartbeats.
2. **SubagentStop deregisters before next SubagentStart:** If agents complete and SubagentStop fires before the next agent starts, the DB shows only the last one (but there were 4 running simultaneously, so this doesn't fully explain it).
3. **Silent exception in SubagentStart:** The handler wraps everything in `except Exception: pass` (line 263). If an error occurs for agents 2-6 but not agent 1, they'd silently fail.

**How to diagnose (for follow-up session):**
1. Add `print(json.dumps(event), file=sys.stderr)` at the top of `handle_subagent_start` to log the raw event JSON
2. Check if `tool_use_id` has different values for each Agent tool call
3. Check if SubagentStart fires for ALL Agent tool calls or just the first
4. Spawn agents with different `subagent_type` values to get unique IDs regardless of `tool_use_id`

### Bug 2: PreToolUse Always Uses Root Agent ID (Lines 78-113)

```python
def handle_pre_write(event: dict) -> dict | None:
    ...
    agent_id = _session_agent_id(event.get("session_id", ""))  # Always root!
    ...
    result = engine.acquire_lock(file_path, agent_id, ttl=600.0)
```

**Even when a subagent does a Write/Edit, the lock is acquired under the root session ID.** The event does not contain a `subagent_id` field, so the handler has no way to know which subagent is doing the write.

**Impact:** This is why all 19 locks show `hub.cc.11b29c44-156` as the holder. CoordinationHub cannot detect contention between subagents because they all look like the same agent.

**Fix needed:** Claude Code needs to include `subagent_id` or `agent_id` in the PreToolUse event JSON when the write comes from a subagent. Then the handler can use `event.get("agent_id", _session_agent_id(event.get("session_id")))`.

### Bug 3: Stele Bridge Never Fires (Lines 134-147)

```python
def handle_post_stele_index(event: dict) -> dict | None:
    tool_input = event.get("tool_input", {})
    doc_path = tool_input.get("document_path") or tool_input.get("path")
    if not doc_path:
        return None  # <-- Always returns None!
```

**Stele's `index` tool takes `paths` (plural, array), not `path` or `document_path`.** The actual tool_input looks like:
```json
{"paths": ["src/services/eventPropagationTracer.js", ...], "summaries": {...}}
```

The handler looks for `document_path` or `path` (singular), finds neither, and returns `None`. The bridge never fires.

**Fix:**
```python
doc_path = tool_input.get("document_path") or tool_input.get("path")
if not doc_path:
    paths = tool_input.get("paths", [])
    for p in paths:
        engine.notify_change(str(p), "indexed", agent_id)
    return None
```

### Trammel Bridge (Lines 150-164) — Correct but Not Exercised

The Trammel bridge handler is correctly implemented:
```python
step_id = tool_input.get("step_id", "")
plan_id = tool_input.get("plan_id", "")
```

These match `claim_step`'s actual parameters. But this session used `record_step` (not `claim_step`), and the PostToolUse matcher is `mcp__trammel__claim_step`, so the bridge never triggered.

**For follow-up:** Use `claim_step` explicitly in the next session to verify the bridge fires and updates agent status.

---

## Follow-Up Session: Test Plan

### Objective
Test CoordinationHub's multi-agent features that are currently untestable.

### Strategy 1: Two Concurrent Sessions (Recommended)

1. Open TWO Claude Code sessions in the same RecipeLab_alt directory
2. Session A registers as `hub.cc.{session_a_id}`
3. Session B registers as `hub.cc.{session_b_id}`
4. Have both sessions edit the SAME file (e.g., src/models/Recipe.js)
5. Expected: Session B's PreToolUse should return `permissionDecision: "deny"` because Session A holds the lock
6. Verify `lock_conflicts` table gets a row
7. Have Session A release the lock, then Session B retry
8. Check coordination_context, file_ownership, and assessment_results tables

### Strategy 2: Subagent Identity Fix

1. Modify `claude_code.py` to log SubagentStart events to stderr
2. Spawn 3 agents with different `subagent_type` values (e.g., `"builder"`, `"tester"`, `"reviewer"`)
3. Verify each gets a unique agent ID in the agents table
4. Check if `tool_use_id` is populated

### Strategy 3: Bridge Hook Verification

1. Fix the Stele bridge: change `path` to `paths` (plural, iterate)
2. Use `claim_step` instead of `record_step` for Trammel bridge
3. Verify both bridges create change_notifications entries
4. Check that agent status updates when claim_step fires

### Strategy 4: Coordination Spec

1. Create a `coordination_spec.yaml` for RecipeLab defining:
   - Agent roles: `architect`, `builder`, `tester`
   - File ownership rules: builders own src/services/, testers own tests/
   - Handoff protocol: builder → tester after file creation
2. Load the spec via CoordinationHub
3. Run an assessment after a coordinated task

### What to Measure
- Lock contention count (target: > 0)
- Agents registered per session (target: matches spawned count)
- Lineage depth (target: > 1 level)
- Bridge notifications (target: Stele and Trammel entries in change_notifications)
- Assessment scores (target: any non-zero assessment_results rows)

---

## Raw Data References

- agents table: 4 rows (2 sessions, 2 subagents, only 1 subagent per session)
- document_locks: 19 rows, all exclusive, all 600s TTL, all hub.cc.11b29c44-156
- change_notifications: 53 rows total, 19 from this session
- lock_conflicts: 0 rows
- lineage: 2 rows (1 per session)
- file_ownership: 0 rows
- coordination_context: 0 rows
- agent_responsibilities: 0 rows
- assessment_results: 0 rows
- Agents spawned via Agent tool: 6
- Agents visible in CoordinationHub: 1 (agent.0)
- Agent tracking gap: 5 of 6 agents invisible
- Hook handler source: /home/aron/Documents/coding_projects/CoordinationHub/coordinationhub/hooks/claude_code.py
- Hook config: ~/.claude/settings.json (7 hook events configured)
