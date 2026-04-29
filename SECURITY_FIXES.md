# Security and Correctness Fixes — opus_review_5 audit

**Audit conducted:** 2026-04-23 (against commit `92f008f`, ~22K LOC)
**Cleanup completed:** 2026-04-27
**Method:** 9 parallel subagent audits across non-overlapping subsystems (DB, locking/leases, agent/identity/spawner, tasks, messaging/events, CLI, MCP server, hooks/schemas/plugins, tests).

This file is the **public record of which audit items have been fixed**. Each entry names the item code, a one-line description of the issue category, and (where present) the schema-version or test-suite milestone the fix landed at. Reproduction notes, exploit paths, and per-line scenarios live in the local `findings/opus_review_5_ultrareview/audit_findings.md` (gitignored — see *Visibility note* below).

<!--
Counts in the scoreboard line below are GEN-managed by scripts/gen_docs.py.
If you find drift here, the pre-commit hook didn't run. Re-run
`python scripts/gen_docs.py` to refresh; do not edit the numbers by hand.
The narrative parentheses around the test delta are prose and stay hand-edited.
-->
**Scoreboard at close-out:** <!-- GEN:audit-closed-count -->153<!-- /GEN --> / 168 tier items closed. <!-- GEN:test-count-baseline -->633<!-- /GEN --> → <!-- GEN:test-count -->806<!-- /GEN --> tests (+121 over the cleanup pass; +43 from the post-audit dispatch-coverage follow-up — first 26 added the zero-coverage tools, second 17 came from the audit-of-the-audit pass that found `await_subagent_registration` had only a docstring mention; remainder from the post-self-review follow-ups including the `core.py` facade-shape invariant). Schema at v<!-- GEN:schema-version -->27<!-- /GEN -->.

---

## Visibility note

The full audit doc stays gitignored under `findings/`. The categories below are deliberately abstract — enough to verify "yes, this item was addressed" without restating the pre-fix exploit detail. If you are a security reviewer with a legitimate need for the full audit, ask the project owner directly.

The deliberate-deferral and `NOTED, NO CHANGE` items are listed below in full, since their rationale is what a downstream consumer most needs to know.

---

## Tier 0 — Currently broken in prod (4 / 4 closed)

Bugs that shipped broken on every invocation.

| Item | Category |
|------|----------|
| T0.1 | CLI handler / parser argument-name mismatch |
| T0.2 | CLI dashboard read of wrong response shape |
| T0.3 | SQLite lease primitive unrecoverable after first IntegrityError |
| T0.4 | Migration re-applies vestigial column on every fresh DB |

## Tier 1 — Data corruption under concurrency (20 / 20 closed)

Multi-agent / multi-process correctness bugs. Mostly transaction-boundary fixes, TOCTOU windows, and event-ordering invariants. Schema v22 added the `broadcast_targets` snapshot table to fix the dynamic-swarm ack accounting.

| Item | Category |
|------|----------|
| T1.1 | Lock primitive opened nested transaction mid-tx (force-steal path) |
| T1.2 | Agent-id sequence: lex-sort wrap + cross-process collision (partial — see deferrals) |
| T1.3 | Lock-cache rewarm raced with concurrent acquire |
| T1.4 | `document_locks` duplicate-row accumulation on re-acquire (partial — see deferrals) |
| T1.5 | Lease timestamp sampled before `BEGIN IMMEDIATE` wait |
| T1.6 | `reap_stale_agents` TOCTOU against concurrent heartbeat |
| T1.7 | Task-failure record race + max_retries source-of-truth bug |
| T1.8 | Retry-from-DLQ exhausted retry budget after one re-failure |
| T1.9 | Spawn-id `COUNT(*)` race produced duplicate ids |
| T1.10 | Event publish: in-mem fired before DB journal commit |
| T1.11 | Broadcast ack accounting wrong for siblings registered after broadcast (partial — see deferrals) |
| T1.12 | Two dependency systems (task-level + agent-level) not consulted together |
| T1.13 | Task status transitions had no state-machine guard (partial — see deferrals) |
| T1.14 | Subtask cycle crashed the tree walker |
| T1.15 | Handoff state machine had no transition guards |
| T1.16 | Work-intent restricted to one file per agent |
| T1.17 | `reap_stale_agents` required manual trigger (now scheduled) |
| T1.18 | Heartbeat after `deregister` silently no-op'd |
| T1.19 | Handoff publish fired phantom completion events |
| T1.20 | Lineage write not atomic with `register_agent` |

## Tier 2 — Security (9 / 9 closed)

| Item | Category |
|------|----------|
| T2.1 | Dashboard exposed prompt content with no auth gate |
| T2.2 | `scan_project` had no scan-root validation (path traversal) |
| T2.3 | Exception text leaked through MCP responses (partial — see deferrals) |
| T2.4 | No `caller_agent_id` check on cross-agent operations |
| T2.5 | Plugin loader had no trust model (path-only resolution) |
| T2.6 | SSE endpoint had no client cap (slow-loris exposure) (partial — see deferrals) |
| T2.7 | `cmd_init` clobbered user settings without backup |
| T2.8 | `cmd_assess --output` followed symlinks |
| T2.9 | `session_id` used as raw SQL key without sanitization |

## Tier 3 — HIGH correctness bugs (26 / 27 closed; T3.24 deferred)

Hooks, dispatch, MCP server, scoring, CLI exit codes. Most are mechanical fixes; the larger ones (T3.6 MCP compliance, T3.8 SSE polling → events) were schema-and-protocol work.

T3.24 (`normalize_path` hits disk in hot path) — deferred pending a path-handling review; T3.26's retry-with-jitter reduced the amplification.

## Tier 4 — Schema integrity (7 / 9 closed; T4.1 + T4.8 deferred)

| Item | Category |
|------|----------|
| T4.2 | Task status no CHECK constraint |
| T4.3 | `document_locks` no UNIQUE (covered by T1.4) |
| T4.4 | `task_failures` no UNIQUE (covered by T1.7) |
| T4.5 | `pending_tasks` status/source no CHECK |
| T4.6 | Index coverage gaps (partial — see deferrals) |
| T4.7 | `coordination_events` unbounded growth |
| T4.9 | Migration transaction safety |

## Tier 5 — Test infrastructure (4 / 8 closed; 4 deferred for a future test-refactor pass)

| Item | Category |
|------|----------|
| T5.1 | Concurrency tests now use `threading.Barrier` for true overlap |
| T5.2 | `test_log_error_never_raises` no longer writes to real `$HOME` |
| T5.4 | Server-readiness polling replaces `time.sleep(0.1)` assumption |
| T5.8 | `test_tool_count.py` assertion stabilised |

## Tier 6 — MEDIUM design / perf (40 / 42 closed; T6.4 + T6.37 deferred)

Highlights: T6.11 made JSON schemas an actual validation gate (was display-only); T6.14 added string-length caps; T6.13 introduced schema versioning; T6.22 collapsed the 12-mixin engine into a composed subsystems pattern across 12 zero-regression commits.

## Tier 7 — LOW nits (45 / 49 closed; T7.18 + T7.22 + T7.23 + T7.33 + T7.34 + T7.39 explicitly NOTED)

Small bugs and polish: copy-paste typos, misleading return values, hard-coded TTLs, schema default mismatches, etc. Schema-package items T7.44–T7.49 close out the JSON-schema validation surface.

---

## Explicitly re-deferred with rationale (5 items)

These were reviewed during the cleanup pass and kept deferred — listed here so downstream consumers know why.

- **T4.1 — full FK rollout.** Live-DB orphan audit confirmed zero orphans across all 30 conventional FK edges (tight app-layer guards). 12 table rebuilds for defence-in-depth is high churn for low payoff. Phased v1 (3 highest-value edges) identified as a future option.
- **T4.8 — schema rebaseline.** Pure cleanup, zero correctness benefit. Wait for a major-version bump.
- **T6.4 — async event-bus writer.** Would break T1.10's durable-before-publish contract. Current synchronous path is correct and fast enough on a solo-process hub.
- **T6.37 — `query_tasks(query_type=...)` dispatch split.** Breaking API change for aesthetics. T6.11 validates the enum at the boundary; correctness is fine.
- **T7.23 — `send_message` + `manage_messages(action="send")` dedup.** Both are on the MCP surface by design and share T2.4's `caller_agent_id` check. Deduping is breaking API for zero correctness benefit.

## Explicitly "noted, no change" (5 items)

- **T7.18** — `leases.py` `INSERT OR REPLACE` is correct (no triggers on `coordinator_leases`).
- **T7.22** — `event_bus.py` `sub_id` wraparound. IDs are in-memory only; persistence adds complexity without operational benefit.
- **T7.33** — `assessment_scorers.py` set iteration order. Iteration order doesn't leak into scoring results.
- **T7.34** — `assessment.py` microsecond offsets. Float precision fine at current scale; revisit if assessments hit 1M events.
- **T7.39** — `mcp_server.py:147-165 _read_json_body`. Truncated-body → 400 is already correct.

## Test-infrastructure items deferred for a future refactor (4 items)

- **T5.3** — short-TTL sleep races (needs a pluggable clock on `CoordinationEngine`).
- **T5.5** — multi-process and migration-failure coverage (needs a subprocess-based harness).
- **T5.6 / T5.7** — test smells / shared-fixture extraction (test-quality hygiene).

## Bundled with broader review (1 item)

- **T3.24** — `normalize_path` hits disk in the hot path. Logical normalization risks changing scope/ownership semantics; bundle with a path-handling review. T3.26's retry-with-jitter reduced the amplification.

---

## Post-audit follow-ups

A separate set of forward-looking concerns surfaced during the cleanup itself (test-coverage gaps in the dispatch surface, LOC-discipline reconciliation, layer-count review, audit visibility). Those are tracked under `findings/post_opus_review_5_followups/` (also gitignored) and resolved as of 2026-04-27. The visible artefacts of those resolutions in this repo are:

- `tests/test_dispatch_coverage.py` — meta-test that strips docstrings and triple-quoted strings before scanning, then prefers structured callsite shapes (`engine.<tool>(` or `dispatch_tool(..., "<tool>" ...)`) over bare substring mentions. Fails if any `TOOL_DISPATCH` entry has no call-site references in `tests/`.
- `AGENTS.md` "LOC Policy" + "Layering Discipline" sections.
- `scripts/gen_docs.py` `STALE_PHRASES` linter + `largest-files` GEN block.
- This file (`SECURITY_FIXES.md`).
