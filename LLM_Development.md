# LLM_Development.md — CoordinationHub

**Version:** <!-- GEN:version -->0.7.5<!-- /GEN -->
**Last updated:** 2026-04-15

## Change Log

All significant changes to the CoordinationHub project are documented here in reverse chronological order.

---

## 2026-04-15 — v0.7.5 Dashboard Pan/Zoom + JS Bug Fix + Opt-In Auto-Dashboard / Monitor Skill

### Motivation

A latent dashboard JavaScript bug — a missing closing paren in a `html.push(...)` call — was introduced during the v0.7.2 dashboard clarity pass and shipped through v0.7.2, v0.7.3, and v0.7.4. Every dashboard load against a database with at least one active lock crashed the JS at parse time with `Uncaught SyntaxError: missing ) after argument list`, leaving the page stuck on "Loading…" forever. The bug was caught by a browser console error during a screenshot session against the RecipeLab_alt swarm. While fixing it, the agent tree was also extended with full pan + zoom controls and the agent nodes were widened so realistic task descriptions don't truncate.

### Fix 1 — Dashboard JavaScript syntax bug (plugins/dashboard/dashboard_html.py)

**Bug**: the boundary-crossing marker block in `renderLocks` read

```
html.push('<span class="lock-path">' + escapeHTML(path);
```

— missing the closing `)` on the `push()` call. The intent was to push only the opener, then conditionally append the boundary marker and the closer, but the typo left `push(` unterminated. JavaScript's parser failed at the next semicolon, killing the entire IIFE before any render function ran. Three releases shipped this; the dashboard worked with empty databases (no `renderLocks` rendering needed) but died the moment any lock existed.

**Fix**: add the missing `)`:

```
html.push('<span class="lock-path">' + escapeHTML(path));
```

**Regression guard**: new `tests/test_dashboard_html.py` with three checks:

1. `test_single_script_block` — exactly one `<script>` and one `</script>` in `DASHBOARD_HTML` (catches the v0.7.1-style premature `</script>` regression too).
2. `test_balanced_panel_markup` — at least six `class="panel"` containers exist.
3. `test_js_parses_with_node` — extracts the `<script>` body and runs it through `node --check`. Skipped when Node isn't on `PATH` so this doesn't make Node a hard CI dependency. Verified to FAIL when the v0.7.2 typo is restored.

### Fix 2 — Agent tree pan + zoom

**Gap**: the agent tree SVG used `viewBox` set to its content's bounding box, with `overflow: auto` on the container — meaning the user could only browse-scroll a clipped region with no way to zoom out. With realistic swarms (6+ agents on a project with deep lineage), nodes routinely fell off-screen.

**Fix**: render all node + edge SVG inside a `<g class="tree-root" transform="translate(panX,panY) scale(zoom)">` group. The SVG's outer `viewBox` is fixed to the panel's pixel size; only the inner group transforms. Three event handlers update the transform:

- `wheel` on the SVG — zoom with anchor at cursor position. `factor = 1.15` per notch. Clamped to `[0.2, 4.0]`.
- `mousedown` + window-level `mousemove` + `mouseup` — click-and-drag pan, with a `panning` CSS class flipping the cursor to `grabbing`.
- Three buttons (`−` / `⌖` fit-to-view / `+`) in a top-right overlay div, plus a live percentage label.

Pan + zoom state is module-level (`var treeState = {zoom, panX, panY}`) and persists across the 5-second SSE refreshes. `fitTreeToView` computes the largest zoom that fits the rendered content's bounding box (with 5% margin) and centers it; called automatically on the very first render and whenever the user clicks the fit button.

**Hoisting bug caught during dev**: the first attempt declared `treeState` near the bottom of the IIFE, alongside the helper functions. Function declarations are hoisted, but `var treeState = {...}` only hoists the declaration (to `undefined`), not the initialization. Because `startSSE()` runs immediately at IIFE top-level and SSE delivers the first event almost instantly, `renderAgentTree` was firing BEFORE the `treeState = {...}` line executed, raising `TypeError: cannot read properties of undefined`. Moved the initialization to the top of the IIFE.

### Fix 3 — Wider agent nodes

**Gap**: nodes were 240×58 with 56-char task and 30-char id truncation. Realistic task descriptions like "Verify paprika/csv/json round-trip parity on the 200-recipe corpus" lost their second half.

**Fix**: bumped `NODE_W` 240→320, `NODE_H` 58→70, `H_GAP` 60→90, `V_GAP` 18→24, `PADDING` 24→28, `MAX_TASK` 56→76, `MAX_ID` 30→38. Internal text positions adjusted to use the larger box (status dot 12,14→14,16; id text 24,19→28,21; task text 10,38→12,44; lock count y=51→61). Tasks like "Migrate allergenDatabase.js to v2 schema (label-set normalization)" now display in full.

### Side benefit

Because the dashboard JS was syntax-erroring before any render, every "demo screenshot" in v0.7.2 / v0.7.3 / v0.7.4 either showed an empty Loading state or worked only because the seeded scenario happened to have no active locks at screenshot time. The new `test_dashboard_html.py::test_js_parses_with_node` would have caught the regression before it shipped. All future dashboard edits run through `node --check` automatically (when Node is on PATH).

### New: opt-in turnkey integrations

Two opt-in flags on `coordinationhub init` make multi-agent monitoring zero-touch:

#### --auto-dashboard

Adds a `SessionStart` matcher block to `~/.claude/settings.json` whose command is `coordinationhub auto-start-dashboard`. The new `auto-start-dashboard` CLI subcommand:

1. Probes `127.0.0.1:9898` (configurable via `--host`/`--port`).
2. If the port is bound, returns 0 immediately — something is already serving (could be a previous coordinationhub session, or an unrelated service that the user wants left alone).
3. If the port is free, opens `~/.coordinationhub/dashboard.log` for append, then spawns `python -m coordinationhub serve-sse --no-browser` via `subprocess.Popen(stdin=DEVNULL, start_new_session=True)`. The detached process survives the hook's quick exit; its logs go to the dashboard.log file.
4. Always returns 0 — a slow or failed dashboard launch must never block a Claude Code session start.

The `_install_auto_dashboard_hook` helper merges the new hook into `settings.json` carefully:

- Doesn't touch the existing `coordinationhub.hooks.claude_code` SessionStart entry (the one that registers the root agent and stamps its current_task).
- Identifies its own hook by the substring `auto-start-dashboard` and updates the python interpreter path in place if already installed (so re-running `init --auto-dashboard` after a venv switch does the right thing).
- Otherwise appends a fresh matcher block with a 5 s timeout and a `Starting CoordinationHub dashboard` status message.

Tests in `tests/test_setup.py::TestAutoStartDashboard` cover both the port-bound path (using a live `socket.socket` to actually occupy the port and asserting `Popen` was NOT called) and the port-free path (asserting the spawned argv contains `serve-sse`, `--no-browser`, and the chosen port). `TestInitOptInFlags` covers the JSON-merge helper for hook installation, idempotence, and python-path updates.

#### --monitor-skill

Copies a markdown file from `coordinationhub/data/monitor_skill.md` into `~/.claude/skills/coordinationhub-monitor/SKILL.md`. The skill is exposed via `[tool.setuptools.package-data] coordinationhub = ["py.typed", "data/*.md"]` in `pyproject.toml` so a fresh `pip install coordinationhub` carries it.

The skill body follows Claude Code's standard frontmatter format:

```
---
name: coordinationhub-monitor
description: Monitor a multi-agent CoordinationHub swarm — surface boundary-crossing locks, blocked tasks, stale agents, and unconverted work intents. Use when watching multiple Claude Code subagents on a shared codebase, or when the user asks you to "watch the swarm" / "monitor the agents" / "tail the dashboard".
---
```

The body instructs an LLM acting on the skill to:

1. Treat itself as a **read-only** observer. Hard rules forbid writing code, calling MCP coordination tools (`acquire_lock`, `register_agent`, `create_task`, `notify_change`, …), or analysing code quality.
2. Poll `http://127.0.0.1:9898/api/dashboard-data` every 30 seconds. The endpoint returns a single JSON document with the same six panels the dashboard shows.
3. Surface five priority signals, in priority order: boundary-crossing locks (where `owner_agent_id` ≠ `locked_by`), blocked tasks, stale agents (heartbeat > 5 min behind the swarm's most recent), long-lived work intents with no follow-through (declared > 2 min ago and the agent doesn't hold any matching lock), and unsatisfied dependencies past their wait window.
4. Output one short bulletted report per cycle, no more than 5 lines. Drop cadence to 60 s after two consecutive empty cycles. Exit when the swarm has converged or the dashboard becomes unreachable.

The skill is invoked when the user says "watch the swarm" / "monitor the agents" / "tail the dashboard", or when running multiple Claude Code subagents in parallel. `TestInitOptInFlags::test_install_monitor_skill_writes_skill_file` covers the install path (frontmatter present, read-only mandate present, dashboard URL present).

#### CLI surface

The new `auto-start-dashboard` subcommand brings the CLI from 74 to 75 commands. The `init` subparser gained `--auto-dashboard` and `--monitor-skill` flags; both default off, so existing users get no surprise process or skill on their next session. The `EXPECTED_COMMANDS` set in `tests/test_cli.py` was updated to include `auto-start-dashboard`.

### Verification

- 403 tests pass (was 394; +3 dashboard-HTML guards, +6 opt-in flag tests), 1 skipped.
- `python scripts/gen_docs.py --check` clean across all five doc targets.
- `node --check` of the served `<script>` body is clean.
- Pan + zoom verified live against the RecipeLab_alt 6-agent swarm.
- `coordinationhub init --auto-dashboard` end-to-end: hook appears in `~/.claude/settings.json` SessionStart array; running `coordinationhub auto-start-dashboard` once on a free port spawns a detached `serve-sse` daemon (visible in `ps -ef` until killed); running it again with the port now bound returns 0 without spawning.
- `coordinationhub init --monitor-skill` end-to-end: SKILL.md appears at `~/.claude/skills/coordinationhub-monitor/SKILL.md` with the expected frontmatter and body.
- Updated `screenshots/dashboard.png` shows the live RecipeLab_alt swarm with realistic file paths, full-text tasks, and zoom controls visible.
- README rewritten for human-readability — 60-second quickstart at the top including the new opt-in flags, panel-layout table tied to the screenshot, and full technical reference (MCP tool table, CLI command reference, architecture, hook contract) pushed below the fold.

### Counts

| Version | Tools | CLI Commands | Schema |
|---------|-------|--------------|--------|
| v0.7.5 | 50 | 75 | 20 |
| v0.7.4 | 50 | 74 | 20 |

---

## 2026-04-15 — v0.7.4 Drop Server Self-Registration + python -m coordinationhub

### Motivation

Two items I had flagged as deferred after the v0.7.3 ship: the server-self-registration ghost-agent leak on SIGKILL, and the missing `__main__.py`.

### Fix 1 — Drop server-side agent registration in mcp_server.py and mcp_stdio.py

**Bug**: Both `CoordinationHubMCPServer.start()` and `mcp_stdio._run_server()` called `engine.generate_agent_id()` + `engine.register_agent(server_agent_id)` at startup, then ran a heartbeat loop (`threading.Thread` for HTTP, `asyncio.ensure_future` for stdio) to keep the row alive every 30s. The server-agent had no consumer — its only effect was that a `hub.<PID>.0` row appeared in the agents table on startup and leaked there whenever the server was killed before `deregister_agent` could run (SIGKILL, OOM, host crash, container restart). The dashboard surfaced these ghosts as standalone root agents until the 600s stale-timeout reap.

**Fix**: Remove the self-registration entirely. The HTTP and stdio servers are coordination middleware — they expose tools that swarm members call, but they are not themselves participants. The change deletes:

- `_server_agent_id` attribute and its `register_agent` call in `CoordinationHubMCPServer.__init__` / `start`.
- The `_heartbeat_thread` and `_heartbeat_loop` method in `mcp_server.py`.
- The `deregister_agent` call in `CoordinationHubMCPServer.stop`.
- The `server_agent_id` parameter and its registration in `mcp_stdio.create_server`.
- The `heartbeat_loop` async task in `mcp_stdio._run_server`.

`create_server` in `mcp_stdio` now returns `(server, engine)` instead of `(server, engine, server_agent_id)`. Internal-only API, no documented consumers.

Regression guard `test_server_does_not_register_self_as_agent` added in `tests/test_integration.py` — asserts that after starting the HTTP server with one explicitly-registered test agent, `list_agents(active_only=False)` returns exactly that one agent and nothing else.

**Side benefit**: the removed heartbeat thread had been blocking `CoordinationHubMCPServer.stop()` for up to 5 seconds per test (`heartbeat_thread.join(timeout=5)`). With ~15 integration tests stopping the server each, the full pytest suite ran in 87s. After the removal it runs in 13s — a 6.5x speedup that fell out of the lifecycle simplification.

### Fix 2 — Add coordinationhub/__main__.py

**Gap**: `python -m coordinationhub` failed with `'coordinationhub' is a package and cannot be directly executed` because the package had no `__main__.py`. Users could only invoke the CLI via the installed `coordinationhub` console script, which isn't always on `PATH` (tox envs, CI runners, fresh venvs without re-install).

**Fix**: 13-line `coordinationhub/__main__.py` that imports `from .cli import main` and runs `sys.exit(main() or 0)`. `python -m coordinationhub status --help` now works identically to `coordinationhub status --help`.

### Verification

- 394 tests pass (was 393; +1 regression test in test_integration.py), 1 skipped. Full suite runs in 13s (was 87s).
- `python scripts/gen_docs.py --check` clean across all five doc targets.
- `python -m coordinationhub` lists subcommands. `python -m coordinationhub status --help` produces the same help text as `coordinationhub status --help`.
- `list_agents(active_only=False)` against a freshly-started HTTP server returns only the agent the test registered explicitly — no `hub.<PID>.0` self-agent.

### Counts

| Version | Tools | CLI Commands | Schema |
|---------|-------|--------------|--------|
| v0.7.4 | 50 | 74 | 20 |
| v0.7.3 | 50 | 74 | 20 |

---

## 2026-04-15 — v0.7.3 Four Latent Bug Fixes

### Motivation

Four latent bugs surfaced while building the v0.7.2 demo screenshot. None are behavioural changes to the MCP surface; each fixes a specific fragility that only shows up in corner cases.

### Fix 1 — context.py:76 NULL-responsibilities crash

**Bug**: `build_context_bundle` used
```
responsibilities = json.loads(resp.get("responsibilities", "[]")) if resp else []
```
`dict.get(key, default)` returns `None` (NOT the default) when the key is present with value `None`. `agent_responsibilities.responsibilities` is a nullable column, and `update_agent_status_tool` inserts rows with only `(agent_id, current_task, updated_at)` — leaving `responsibilities` at `NULL`. Any subsequent `register_agent` on the same id (including a simple re-registration on a dirty DB, which happens every hook restart) then hit `json.loads(None)` and crashed with `TypeError: the JSON object must be str, bytes or bytearray, not NoneType`.

**Fix**: `resp.get("responsibilities") or "[]"` — the pattern already used in `agent_status.py` at three other sites. Added regression test `test_register_agent_tolerates_null_responsibilities_row` in `test_agent_lifecycle.py`; confirmed it passes with the fix and fails without it.

### Fix 2 — cli_sse.py dropped root/storage/namespace flags

**Bug**: `cmd_serve_sse` constructed `CoordinationHubMCPServer(host=host, port=port)` without forwarding `args.project_root`, `args.storage_dir`, `args.namespace`. `coordinationhub serve-sse --project-root /foo` silently ignored `--project-root` and the server defaulted to the CWD's project root — meaning the dashboard would show the wrong project's state on any machine that ran the command from outside the target repo.

**Fix**: forward all three via `getattr(args, "<flag>", default)`, matching the pattern in `cmd_serve`.

### Fix 3 — mcp_server.py __import__("pathlib")

**Bug (cosmetic)**: `CoordinationHubMCPServer.__init__` did
```
storage_dir=storage_dir and __import__("pathlib").Path(storage_dir),
project_root=project_root and __import__("pathlib").Path(project_root),
```
No reason — `pathlib` is pure stdlib with no import side effects. The lazy-import pattern was just ugly.

**Fix**: top-level `from pathlib import Path` plus a normal ternary `Path(x) if x else None` in both branches.

### Fix 4 — gen_docs.py greedy regex foot-gun

**Bug**: `BLOCK_RE` accepted any HTML-comment marker token as an opener and used a non-greedy `.*?` body. When a prose description mentioned a marker token without pairing it with a matching closer, the body extended greedily to the NEXT real marker's closer — destructively wiping everything in between when `gen_docs.py` rewrote the file. Encountered once while drafting the v0.7.1 changelog (a prose mention of the CLI-count marker matched against a real marker's closer ~200 lines later, corrupting the changelog).

**Fix**: add a negative lookahead on the body so it stops at any further opener or closer token. When the body can't contain another marker, the regex engine backtracks from a bad prose opener and the match fails instead of corrupting the file. Verified with a synthetic doc containing both a prose mention and a real marker — prose is preserved, only the real marker updates. See `scripts/gen_docs.py` for the exact regex.

### Verification

- 393 tests pass (was 392 — the only added test is the `context.py` regression), 1 skipped.
- `python scripts/gen_docs.py --check` clean across all five doc targets.
- Every file in `coordinationhub/` remains ≤ 500 code LOC.
- Both workflows (`Tests` on push, `Publish to PyPI` on release) continue to run with zero third-party GitHub Actions.

### Counts

| Version | Tools | CLI Commands | Schema |
|---------|-------|--------------|--------|
| v0.7.3 | 50 | 74 | 20 |
| v0.7.2 | 50 | 74 | 20 |

---

## 2026-04-15 — v0.7.2 Dashboard Fix + Clarity Pass + Zero-Dep CI

### Motivation

Three concurrent issues surfaced after v0.7.1 shipped:

1. The web dashboard rendered a wall of raw JavaScript source under "Active Locks" every time it loaded. Every screenshot of the dashboard in the wild was broken.
2. The dashboard panels gave a human reader no indication of what they represented or when they would populate.
3. The CI workflows carried a Node.js 20 deprecation warning on `actions/checkout@v4`, `actions/setup-python@v5`, and `stefanzweifel/git-auto-commit-action@v5` that was scheduled to break by September 2026.

### Change 1 — Dashboard script-tag bug (plugins/dashboard/dashboard.py)

**Bug**: A stray `})();` followed by `</script>` at line 272–273 of the embedded dashboard HTML prematurely closed both the IIFE and the `<script>` block. Every render function below that point (`escapeHTML`, `ageAgo`, `renderAgentTree`, `renderTasks`, `renderIntents`, `renderHandoffs`, `renderDependencies`, `renderLocks`) became plain HTML text inside the document body instead of being declared on window. The data-fetching IIFE above those lines ran fine, then called undefined renderers, so every panel except the headers was empty; meanwhile the function source leaked as visible text beneath "Active Locks".

**Fix**: delete the two stray lines so the script spans from its opening `<script>` on line 220 to a single closing `</script>` at the end. One IIFE, one script block.

### Change 2 — Dashboard clarity pass (plugins/dashboard/dashboard.py + dashboard_html.py + get_dashboard_data)

**Gap**: empty panels read "No handoffs recorded" / "No active work intents" / "No dependencies declared" without any indication of what those sections meant, what tool call would populate them, or how to interpret the status badges and lock types.

**Fix**:

- Header gains a one-line description of what the dashboard represents ("Live view of the multi-agent swarm working on this project — who is alive, what they are doing, which files they hold, and where they are blocked.").
- Every panel gets a blurb below the title explaining when it populates, using concrete references to the MCP tool calls (e.g. "An agent calls `manage_work_intents(action='declare')` to reserve a file for a short TTL").
- Empty-state messages replaced with actionable hints describing the exact tool call that would fill each section.
- Agent-tree nodes widen from 160×44 to 240×58; ID truncation grows from 20 to 30 chars; `current_task` truncation grows from 26 to 56 chars; a small `N lock(s)` badge appears in the bottom-right of each node when the agent holds any locks.
- `get_dashboard_data` now `LEFT JOIN`s `agent_responsibilities` so the tree receives `current_task` values. Previously `current_task` was always `NULL` in the dashboard payload because it lives in the responsibilities table, not `agents`. The agent-tree panel showed only agent IDs with no task text even though the CLI `agent-tree` command rendered tasks fine.
- `get_dashboard_data` also `LEFT JOIN`s `file_ownership` so the locks payload carries `owner_agent_id`. The dashboard uses this to render a `⚠ owned by <agent>` marker whenever a lock owner differs from the file owner, matching the CLI `agent-tree` tree output.
- Active Locks panel promoted to full-width; groups by path; each row shows "held Xs ago · TTL Ys".
- Legends under the Agent Tree header (green/grey/red status dots) and under the Active Locks header (exclusive / shared chips) explain the colour coding.

### Change 3 — dashboard_html.py split

**Gap**: the clarity pass took `plugins/dashboard/dashboard.py` from ~483 to 552 code LOC, breaking the project's 500-code-LOC-per-file rule (CLAUDE.md, GLOBAL_AGENTS.md).

**Fix**: the `DASHBOARD_HTML` constant (~478 code LOC of pure CSS + HTML + JS template) moved into a sibling `dashboard_html.py`. `dashboard.py` now contains only the Python logic (data aggregator + `_serve_dashboard` / `_serve_api_dashboard` handlers) and re-exports `DASHBOARD_HTML` via `from .dashboard_html import DASHBOARD_HTML`. Net result: `dashboard.py` is 82 code LOC, `dashboard_html.py` is 478 code LOC; every existing `from .dashboard import DASHBOARD_HTML` continues to work unchanged.

### Change 4 — Zero-dep CI (.github/workflows/test.yml + publish.yml)

**Gap**: GitHub flagged three marketplace actions as Node.js 20 deprecated with removal scheduled Sept 16 2026: `actions/checkout@v4`, `actions/setup-python@v5`, and `stefanzweifel/git-auto-commit-action@v5`. `pypa/gh-action-pypi-publish@release/v1` was not flagged but did not match the project's zero-third-party-dep ethos either.

**Fix**: replaced every marketplace action with an inline shell step using tools pre-installed on `ubuntu-latest`:

- `actions/checkout@v4` → `git init`, `git remote add origin`, `git -c protocol.version=2 fetch --depth=1 origin $SHA`, `git checkout --force FETCH_HEAD`. Works for push events, same-repo PRs, and fork PRs (GitHub syncs `refs/pull/N/head` into the base repo).
- `actions/setup-python@v5` → a short shell step that globs `/opt/hostedtoolcache/Python/<ver>.*/x64` (runner pre-installs 3.10 / 3.11 / 3.12 / 3.13 / 3.14) and appends `$PY_DIR/bin` to `$GITHUB_PATH`.
- `stefanzweifel/git-auto-commit-action@v5` → inline `git diff --quiet` gate, `git add` / `commit` / `push`, with `origin` rewritten to `https://x-access-token:${GITHUB_TOKEN}@github.com/${REPO}.git` and `permissions: contents: write` granted at job scope.
- `pypa/gh-action-pypi-publish@release/v1` → curl-based OIDC JWT exchange against `https://pypi.org/_/oidc/mint-token` + `pip install twine` + `twine upload`. Trusted publishing is preserved; no PyPI API token secret is added.

Runtime tool chain now uses only what `ubuntu-latest` ships (git, curl, python, pip), plus `build` and `twine` pip-installed at job time. Both workflows ran green end-to-end after the switch.

### Change 5 — README showcase + screenshots tracked

**Gap**: no visual of the dashboard in the README or on PyPI. `screenshots/` was blanket-excluded from git by `.gitignore`.

**Fix**: dropped the blanket `screenshots/` rule in `.gitignore` and replaced it with narrow patterns that ignore only ad-hoc scratch drops (`screenshots/tmp/`, `screenshots/pasted*`, `screenshots/screencapture*`, `*.xcf`, `*.psd`). Added a curated `screenshots/dashboard.png` of the refreshed dashboard in its empty state — which reads as a visual tour of every primitive the hub exposes — and embedded it near the top of `README.md`. Because `pyproject.toml` sets `readme = "README.md"`, the image propagates into the PyPI long description too.

### Verification

- 392 tests pass, 1 skipped (same baseline as v0.7.1).
- `python scripts/gen_docs.py --check` clean across all five doc targets.
- Every file in `coordinationhub/` is ≤ 500 code LOC (largest: `core_locking.py` at 496, `dashboard_html.py` at 478).
- Schemas ↔ dispatch ↔ CLI handlers all in sync.
- Both workflows (`Tests` on push, `Publish to PyPI` on release) run without any third-party GitHub Actions.

### Counts

| Version | Tools | CLI Commands | Schema |
|---------|-------|--------------|--------|
| v0.7.2 | 50 | 74 | 20 |
| v0.7.1 | 50 | 74 | 20 |

---

## 2026-04-15 — v0.7.1 Polish Pass (File-Size Rule Enforcement + Doc Parity)

### Motivation

v0.7.0 shipped with two files that violated the project's 500-code-LOC-per-file rule (`schemas.py` at 1260, `db.py` at 565). Kimi's fill-in changelogs for v0.6.6 / v0.6.7 / v0.7.0 also carried several wrong count-table numbers. In addition, stale tool-name references lingered in CLAUDE.md, README.md, and the wiki after the v0.7.0 consolidation.

### Change 1 — `schemas.py` split into a `schemas/` package

`schemas.py` was already internally organized into 14 `TOOL_SCHEMAS_*` sub-dicts. Each moved into its own sibling module:

- `schemas/identity.py`, `locking.py`, `coordination.py`, `messaging.py`, `change.py`, `audit.py`, `visibility.py`, `tasks.py`, `intent.py`, `handoffs.py`, `deps.py`, `dlq.py`, `leases.py`, `spawner.py`
- `schemas/__init__.py` aggregates them into `TOOL_SCHEMAS` using `dict | dict | ...` (identical shape to the old dict).

External callers (`mcp_server.py`, `mcp_stdio.py`, `scripts/gen_docs.py`, tests) continue to `from .schemas import TOOL_SCHEMAS` — no import sites changed. The `_REGION_PROPS` shared region-start/region-end property block is inlined into `schemas/locking.py`.

Largest new module: `tasks.py` at 220 code LOC.

### Change 2 — `db.py` split into `db.py` + `db_schemas.py` + `db_migrations.py`

- `db_schemas.py` (287 code LOC) — pure data. Holds `_SCHEMAS` (dict of `CREATE TABLE` statements) and `_INDEXES` (list of `CREATE INDEX` statements).
- `db_migrations.py` (222 code LOC) — every `_migrate_v*_to_v*` function, `_CURRENT_SCHEMA_VERSION`, `_get_schema_version`, the `_MIGRATIONS` dict, and the `init_schema()` driver.
- `db.py` (93 code LOC) — `ConnectionPool`, `_create_connection`, module-level pool helpers (`set_pool` / `clear_pool` / `connect`), and re-exports of the schema and migration names used by `_storage.py`, `mcp_stdio.py`, and tests.

No-cycle import graph: `db_schemas` has no internal deps; `db_migrations` imports `_SCHEMAS` / `_INDEXES` from `db_schemas`; `db` imports from both.

### Change 3 — Deleted `scripts/update_schemas.py`

296-line one-shot Phase 1 consolidation script with no callers anywhere in the codebase, CI, or docs. Rerunning it would destructively overwrite the current `schemas/` package, so leaving it in the repo was an active hazard.

### Change 4 — Deleted the `_context_bundle` back-compat shim

The 7-line deprecated wrapper in `core.py` had a single caller (`tests/test_notifications.py::test_context_bundle_has_url`). Updated the test to call the canonical `_build_context_bundle(agent_id, None)` and removed the shim.

### Change 5 — Dropped `pytest-asyncio`

No async tests exist. Removed `pytest-asyncio>=0.21` from `[project.optional-dependencies].dev` and dropped `asyncio_mode = "auto"` from `[tool.pytest.ini_options]`. This eliminates the `Unknown config option: asyncio_mode` warning that fired on every pytest run.

### Change 6 — `cli-count` generator

Added a `get_cli_count()` generator to `scripts/gen_docs.py` (computes `len(coordinationhub.cli._COMMANDS)`). Wrapped the "CLI Commands (74)" header in `README.md` in a matching `GEN` marker. Future drift in CLI surface is now caught by `python scripts/gen_docs.py --check`.

### Change 7 — Doc parity fixes

- CLAUDE.md tool manifest rewritten to match current `TOOL_DISPATCH`. Previous listing referenced `get_lineage`, `get_siblings`, `suggest_task_assignments`, `acknowledge_handoff`, `complete_handoff`, `cancel_handoff`, `declare_dependency`, `check_dependencies`, `satisfy_dependency`, `wait_for_dependency`, `get_blockers`, `assert_can_start`, `get_all_dependencies`, `get_messages`, `mark_messages_read`, `prune_notifications`, `release_agent_locks`, `reap_expired_locks`, `reap_stale_agents`, `validate_graph`, `assess_current_session` — all consolidated into meta-tools in v0.7.0.
- CLAUDE.md `_CURRENT_SCHEMA_VERSION = 3` design note updated to 20 with the full migration-chain summary (v2 document_locks restructure through v20 pending-table merge).
- Stale count strings corrected across CLAUDE.md, README.md, COMPLETE_PROJECT_DOCUMENTATION.md, and wiki: "currently 35" → 50, "19 test files" → 23, "all 30 tool schemas" → 50, "55 coordination tool methods" → generic wording.
- README.md tool manifest table rewritten and its CLI count header wrapped in a `GEN` marker.
- Glossary entries for Agent Lineage, Notification Pruning, Reaping, and Stale Agent updated to point at the current MCP tool names (`get_agent_relations`, `admin_locks`, etc.).
- `wiki-local/spec-project.md` MCP tools section rewritten against the current surface; stdlib-deps table updated to include `db_schemas`, `db_migrations`, and the `schemas/` package.
- `coordinationhub/cli.py` module docstring — dropped the stale "all 55 coordination tool methods" count.

### Change 8 — Fixed count drift in Kimi's v0.6.6 / v0.6.7 / v0.7.0 changelog tables

Git-verified historical counts (`grep -c 'sub.add_parser(' cli.py` + `grep -c '":' dispatch.py` at each tagged commit):

| Version | Real (tools / CLI / schema) | Kimi's fill-in |
|---------|------------------------------|----------------|
| v0.7.0  | 50 / 74 / 20                 | 50 / ~55 / 20  |
| v0.6.7  | 79 / 84 / 20                 | 83 / 79 / 20   |
| v0.6.6  | 79 / 84 / 20                 | 79 / 79 / 20   |

Also fixed the prose "grown to 83 tools" → "grown to 79 tools" and "down from 83" → "down from 79" in the v0.7.0 changelog.

### Verification

- 392 tests pass, 1 skipped (same as v0.7.0 baseline). No test changes beyond the `_build_context_bundle` rename in `test_notifications.py`.
- `scripts/gen_docs.py --check` is clean across all five doc targets.
- Every file in `coordinationhub/` is ≤ 500 code LOC. Largest: `core_locking.py` (496), `dashboard.py` (483), `cli.py` (398).
- Schemas ↔ dispatch ↔ CLI handlers are all in sync.

### Counts

| Version | Tools | CLI Commands | Schema |
|---------|-------|--------------|--------|
| v0.7.1 | 50 | 74 | 20 |
| v0.7.0 | 50 | 74 | 20 |

---

## 2026-04-15 — v0.7.0 Multi-Agent Consolidation (Tool Surface Reduction + Cross-Process Event Sync)

### Motivation

With 79 MCP tools, discovery and usability had become a bottleneck for multi-agent workflows. Worse, the in-memory `EventBus` meant that every `wait_for_task`, `await_handoff_completion`, and `wait_for_notifications` call timed out when talking to a remote `coordinationhub serve` process. This release consolidates duplicated APIs, fixes cross-process synchronization, and hardens DB connection patterns.

### Change 1 — Tool consolidation from 79 → 50 (dispatch.py + all core_/cli_ modules)

**Gap**: Seven separate dependency tools, six task query tools, and scattered DLQ/lock-admin/broadcast utilities made the surface overwhelming.

**Fix**: Collapsed overlapping tools into unified commands:
- `manage_dependencies` — replaces `declare_dependency`, `check_dependencies`, `satisfy_dependency`, `wait_for_dependency`, `get_blockers`, `assert_can_start`, `get_all_dependencies`
- `query_tasks` — replaces `get_task`, `get_child_tasks`, `get_tasks_by_agent`, `get_all_tasks`, `get_subtasks`, `get_task_tree`
- `task_failures` — replaces `retry_task`, `get_dead_letter_tasks`, `get_task_failure_history`
- `admin_locks` — replaces `release_agent_locks`, `reap_expired_locks`, `reap_stale_agents`
- `wait_for_broadcast_acks` — absorbs `get_broadcast_status` and `await_broadcast_acks`
- `wait_for_handoff` — absorbs `get_handoffs`, `await_handoff_acks`, `await_handoff_completion`
- `get_agent_relations` — absorbs `get_lineage` and `get_siblings`
- `run_assessment` — now optionally takes `suite_path`; when omitted, synthesizes assessment from live DB state (old `assess_current_session` behavior)

### Change 2 — Cross-process event synchronization (core.py + event_bus.py + db.py)

**Gap**: `EventBus` is pure in-memory. A task completed in one process never reached a waiter in another process.

**Fix**: Dual-write event journal.
- New `coordination_events` table with `topic`, `payload_json`, `created_at`
- `_publish_event()` writes to both the in-memory bus (fast same-process path) and the SQLite journal (cross-process fallback)
- `_hybrid_wait()` first tries the memory bus, then polls the journal
- Two critical bugs were fixed during validation:
  - `_publish_event` was missing `conn.commit()`, so journal rows were invisible to other connections
  - `_hybrid_wait` constructed parameters as `topics + (since,)` where `topics` is a `list`, causing a silent `TypeError` that was swallowed by a broad `except` and always returning timeout

### Change 3 — Connection safety hardening (tasks.py + dependencies.py + handoffs.py + broadcasts.py)

**Gap**: Several modules performed `fetchone()`/`fetchall()` inside a `with connect() as conn:` block and then accessed `sqlite3.Row` columns after the block exited. This can raise `ProgrammingError` or return stale data.

**Fix**: Mechanical refactor — every query result is converted to `dict(row)` or `[dict(r) for r in rows]` before the `with` block ends. Added `tests/test_db_safety.py` which monkey-patches `sqlite3.Row.__getitem__` to raise on post-close access.

### Change 4 — Implicit graph fallback (plugins/graph/graphs.py + core_visibility.py)

**Gap**: `scan_project` and `run_assessment` failed hard if no `coordination_spec.yaml` was present.

**Fix**: `build_implicit_graph(connect)` derives a minimal `CoordinationGraph` from the live `agents` table (root orchestrator + inferred child roles + inferred handoffs). `scan_project` and `run_assessment` now call `_effective_graph()` which returns the loaded graph if present, otherwise the implicit one.

### Change 5 — Naming cleanup (core_tasks.py + core_spawner.py)

**Fix**: Updated docstrings to disambiguate:
- `tasks` table → "task registry / work board"
- `pending_tasks` table → "spawn queue"

### New Tests

- `tests/test_tool_count.py` — regression guard asserting `len(TOOL_DISPATCH) <= 50`
- `tests/test_multiprocess_sync.py` — spawns `coordinationhub serve` in a subprocess and validates that `wait_for_task` returns successfully across processes
- `tests/test_db_safety.py` — 14 connection-safety torture tests
- `tests/test_visibility.py` — implicit graph scan without spec file

### Counts

| Version | Tools | CLI Commands | Schema |
|---------|-------|--------------|--------|
| v0.7.0 | 50 | 74 | 20 |
| v0.6.7 | 79 | 84 | 20 |

---

## 2026-04-15 — v0.6.7 Phase 14 Critical Fixes (Scope Normalization + Connection Robustness)

### Motivation

Phase 14 DistributedRecipeCurationSwarm stress test exercised every major primitive under heavy multi-agent contention. Six issues were identified and fixed.

### Change 1 — Scope/path normalization (core_locking.py)

**Bug**: Absolute scopes (e.g., `/home/user/project/src/`) failed to match relative lock paths (e.g., `src/services/file.js`).

**Fix**: `_check_scope_violation()` now normalizes scope prefixes with `normalize_path()` before comparing against the lock path.

### Change 2 — SQLite connection robustness (db.py + core_locking.py)

**Bug**: Closed DB connections under lock contention caused `Cannot operate on a closed database`.

**Fix**: `ConnectionPool.connect()` now validates connections with a health-check `SELECT 1` and recreates them if closed. The `acquire_lock()` retry loop no longer closes the pool connection.

### Change 3 — Dependency auto-satisfaction (dependencies.py)

**Bug**: `check_dependencies()` reported completed tasks as unsatisfied.

**Fix**: `check_dependencies()` now auto-satisfies dependencies whose conditions are already met (completed tasks, stopped agents, active agents) and performs all queries inside the connection context.

### Change 4 — wait_for_dependency helper (dependencies.py + core_dependencies.py)

**Gap**: Callers had to poll manually for dependency satisfaction.

**Fix**: New `wait_for_dependency(dependent_agent_id, timeout_s)` helper that polls until a dependency is satisfied or timeout expires.

### Change 5 — assess_current_session without graph (core_visibility.py)

**Gap**: Ad-hoc swarms with no `coordination_spec.yaml` couldn't be scored.

**Fix**: Removed the hard error when no graph is loaded. `assess_current_session` now scores from live DB state; graph-dependent metrics return 0.0 when no graph is present.

### Change 6 — Task assignment hints (tasks.py + core_tasks.py)

**Gap**: No automated way to match idle agents with available tasks.

**Fix**: New `suggest_task_assignments()` method returns available tasks matched with idle agents (agents with no pending/in_progress tasks).

### Change 7 — Handoff completion tracking (core_handoffs.py + core_locking.py)

**Gap**: Multi-recipient handoffs required polling `get_handoffs` to detect completion.

**Fix**: `acknowledge_handoff` and `complete_handoff` now publish `handoff.ack` / `handoff.completed` events. New helpers: `await_handoff_acks(handoff_id, expected_agents, timeout_s)` and `await_handoff_completion(handoff_id, timeout_s)`.

### Counts

| Version | Tools | CLI Commands | Schema |
|---------|-------|--------------|--------|
| v0.6.7 | 79 | 84 | 20 |
| v0.6.6 | 79 | 84 | 20 |

---

## 2026-04-14 — v0.6.6 Hook Abstraction + Plugin Architecture

### Motivation

Standalone modules (`assessment.py`, `graphs.py`, `dashboard.py`) and IDE-specific hooks were tightly coupled and hard to extend. This release introduces a plugin registry and a shared hook abstraction.

### Change 1 — Plugin architecture (plugins/)

**Gap**: Assessment, graph, and dashboard code lived at the project root with no extension point.

**Fix**: Created `plugins/` package:
- `plugins/assessment/` — moved `assessment.py` and `assessment_scorers.py`
- `plugins/dashboard/` — moved `dashboard.py`
- `plugins/graph/` — moved `graphs.py`
- `plugins/registry.py` — runtime plugin discovery and registration
- `plugins/__init__.py` — `register_plugin()` / `list_plugins()` helpers

### Change 2 — Hook abstraction (hooks/base.py)

**Gap**: `hooks/claude_code.py` was the only hook and was monolithic.

**Fix**: Introduced `BaseHook` abstract class with lifecycle methods (`on_agent_start`, `on_agent_stop`, `on_task_created`, etc.). Refactored `claude_code.py` to inherit from `BaseHook`. Added `hooks/kimi_cli.py` and `hooks/cursor.py` for Kimi CLI and Cursor integrations.

### Change 3 — Performance foundations (event_bus.py + lock_cache.py)

**Gap**: Every coordination event required a DB round-trip.

**Fix**: Added in-memory `EventBus` for fast intra-process pub-sub. Added `LockCache` so engine startup warms lock state from the DB once, then serves hot reads from memory.

### New Tests

- `tests/test_event_bus.py`
- `tests/test_lock_cache.py`
- `tests/test_hooks_base.py`
- `tests/load_test.py`

### Counts

| Version | Tools | CLI Commands | Schema |
|---------|-------|--------------|--------|
| v0.6.6 | 79 | 84 | 20 |
| v0.6.5 | 79 | 84 | 19 |

---

## 2026-04-14 — v0.6.5 Phase 13 Stress Test Fixes (Broadcast + Lock Notifications)

### Motivation

Phase 13 MultiAgentLockStorm stress test (`findings/kimi_review_3/coordinationhub.md`) validated CoordinationHub's core primitives under heavy contention. All core features passed (region locks, shared/exclusive semantics, agent tree, task deps, handoffs). Three minor gaps were fixed:

1. **Broadcast auto-ack ambiguity** — when `require_ack=False`, `acknowledged_by` was incorrectly populated with live siblings
2. **No lock event notifications** — `acquire_lock` and `release_lock` did not emit change notifications, forcing agents to poll `get_lock_status`
3. **Pending acks never resolved** — non-interactive agents never called `acknowledge_broadcast`, leaving explicit-ack broadcasts dangling indefinitely

### Change 1 — Fix broadcast auto-ack ambiguity (core_locking.py)

**Bug**: `broadcast(require_ack=False)` returned `acknowledged_by` filled with all live sibling IDs, even though no acknowledgments were actually requested or received.

**Fix**: When `require_ack=False`, `acknowledged_by` is now empty. Conflict detection still uses the live sibling list correctly.

### Change 2 — Lock event notifications (core_locking.py)

**Gap**: Agents had no push signal for lock releases; they had to poll `get_lock_status`.

**Fix**: Successful `acquire_lock` now calls `notify_change(..., change_type="locked")` and successful `release_lock` calls `notify_change(..., change_type="unlocked")` immediately after the DB transaction commits.

### Change 3 — Auto-acknowledge broadcast requests on message read (core_messaging.py)

**Gap**: Non-interactive agents (e.g., background workers) poll `get_messages` but never explicitly call `acknowledge_broadcast`, so `require_ack=True` broadcasts showed pending acks forever.

**Fix**: `get_messages` now auto-acknowledges any `broadcast_ack_request` messages it returns. As soon as an agent polls its mailbox, outstanding broadcast acks are resolved automatically.

### Change 4 — Broadcast expected_count tracking (broadcasts.py + db.py)

**Improvement**: `broadcasts` table now stores `expected_count` (schema v19). `get_broadcast_status` returns `expected_count` and `pending_acks` so callers can see acknowledgment progress.

### Schema Changes

- v18 → v19 migration adds `expected_count INTEGER DEFAULT 0` to `broadcasts` table.

### Counts

| Version | Tools | CLI Commands | Schema |
|---------|-------|--------------|--------|
| v0.6.5 | 79 | 79 | 19 |
| v0.6.4 | 79 | 79 | 18 |

---

## 2026-04-14 — v0.6.4 Agnostic Spawner + Broadcast Acknowledgments

### Motivation

Review Nineteen identified two design-level gaps:
1. `spawn_subagent` was tightly coupled to Claude Code hooks — other IDE/CLIs had no way to report sub-agent spawns back to CoordinationHub
2. `broadcast` without `handoff_targets` had no delivery confirmation mechanism

### Change 1 — P0 Agnostic Sub-Agent Spawning

**Gap**: `spawn_subagent` created a pending record, but only the Claude Code hook could consume it via `SubagentStart`. Kimi CLI, Cursor, and other environments had no integration path.

**Fix**: Added a generic `report_subagent_spawned` tool that any external system can call after spawning an agent via its native mechanism:
```python
def report_subagent_spawned(
    connect, parent_agent_id, subagent_type, child_agent_id, source="external"
):
    ...
```

- Added `source` column to `pending_spawner_tasks` to track which system performed the spawn
- `spawn_subagent` now accepts an optional `source` parameter
- The Claude Code hook was updated to use `engine.report_subagent_spawned(...)` internally, unifying the code path
- New CLI command: `coordinationhub report-subagent-spawned <parent> <child> [--subagent-type TYPE] [--source SOURCE]`

This makes CoordinationHub a **coordination layer that complements native spawn tools** instead of trying to replace them.

### Change 2 — P0 Broadcast Delivery Confirmation

**Gap**: `broadcast` without `handoff_targets` returned `acknowledged_by: []` with no actual acknowledgment tracking. Only the handoff variant created persistent state.

**Fix**: Added `broadcasts` and `broadcast_acks` tables, plus optional `require_ack` on `broadcast`:
```python
engine.broadcast(agent_id, require_ack=True, message="hello")
# Returns: {"broadcast_id": 1, "pending_acks": [...], "acknowledged_by": []}

engine.acknowledge_broadcast(1, recipient_agent_id)
# Returns: {"acknowledged": True}
```

- New MCP tools: `acknowledge_broadcast`, `get_broadcast_status`, `await_broadcast_acks`
- New CLI commands: `acknowledge-broadcast`, `broadcast-status`
- Legacy `broadcast` behavior (no ack required) is unchanged for backward compatibility
- When `require_ack=True`, each live sibling receives a `broadcast_ack_request` message via the existing mailbox system

### Schema Changes

- v17 → v18 migration adds:
  - `source` column to `pending_spawner_tasks`
  - `broadcasts` table
  - `broadcast_acks` table

### Counts

| Version | Tools | CLI Commands | Schema |
|---------|-------|--------------|--------|
| v0.6.4 | 68 | 71 | 16 |
| v0.6.3 | 64 | 67 | 14 |
| v0.6.2 | 64 | 67 | 13 |

---

## 2026-04-14 — v0.6.3 Scope Column Migration + Agent State Sync

### Motivation

Review Nineteen (`findings/coordinationhub.md`) tested CoordinationHub under a live 3-agent swarm and identified a critical bug: `acquire_lock` failed with "no such column: scope" on legacy databases. It also noted that `current_task` was not auto-updated when tasks were assigned.

### Change 1 — P0 Scope Column Migration (db.py)

**Bug**: `acquire_lock` failed on every call for DBs created before the `scope` column existed in `agent_responsibilities`.

**Root cause**: migration v6 was a no-op:
```python
6: lambda conn: None,  # scope column added via CREATE TABLE IF NOT EXISTS
```
`CREATE TABLE IF NOT EXISTS` is a no-op for existing tables, so legacy databases never got the `scope` column.

**Fix**: added proper v16→v17 migration:
```python
def _migrate_v16_to_v17(conn: sqlite3.Connection) -> None:
    cols = [row[1] for row in conn.execute("PRAGMA table_info(agent_responsibilities)").fetchall()]
    if "scope" in cols:
        return
    conn.execute("ALTER TABLE agent_responsibilities ADD COLUMN scope TEXT")
```

On the next `init_schema` call, all legacy databases get the column and `acquire_lock` works correctly.

### Change 2 — P1 Agent State Sync on Task Assignment (tasks.py)

**Gap**: `assign_task` updated the `tasks` table but did not sync `current_task` to `agent_responsibilities`, so `get_agent_tree` showed empty task descriptions.

**Fix**: `assign_task` now looks up the task description and upserts it into `agent_responsibilities`:
```python
row = conn.execute("SELECT description FROM tasks WHERE id=?", (task_id,)).fetchone()
if row:
    conn.execute("""
        INSERT INTO agent_responsibilities (agent_id, current_task, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(agent_id) DO UPDATE SET
            current_task = excluded.current_task,
            updated_at = excluded.updated_at
    """, (assigned_agent_id, row["description"], now))
```

### Counts

| Version | Tools | CLI Commands | Schema |
|---------|-------|--------------|--------|
| v0.6.3 | 64 | 67 | 14 |
| v0.6.2 | 64 | 67 | 13 |
| v0.6.1 | 61 | 64 | 13 |

---

## 2026-04-13 — v0.6.2 Lock Bug Fix + wait_for_task + wait_for_notifications

### Motivation

Review Eighteen (`findings/Kimi_review_1/kimi_findings.md`) tested CoordinationHub under a live 3-agent coordinated implementation. Key findings:

| Issue | Priority | Status |
|-------|----------|--------|
| `acquire_lock` spurious "cannot rollback" error | P0 | **Fixed** — scope check moved before COMMIT |
| `depends_on` inert — no wait primitive | P0 | **Fixed** — added `wait_for_task` |
| No long-poll for notifications | P1 | **Fixed** — added `wait_for_notifications` |
| MCP config discovery gap (Kimi CLI) | P1 | **Documented** — ~/.kimi/mcp.json required |
| Stale locks on crash | P2 | Already handled — `deregister_agent` releases locks |
| Opaque lock conflicts | P2 | Already handled — returns conflict metadata |

### Change 1 — P0 Lock Transaction Bug Fix (core_locking.py)

**Bug**: `acquire_lock` returned "cannot rollback - no transaction is active" on every lock acquisition. The lock WAS acquired successfully despite the error.

**Root cause**: After inserting the lock and committing (line 105), the scope violation check ran (line 108). If scope was violated, the code attempted `ROLLBACK` (line 110) — but there was no active transaction to roll back.

**Fix**: Moved the scope check BEFORE the COMMIT:
```python
conn.execute("INSERT INTO document_locks ...")
# Check scope BEFORE commit so ROLLBACK is valid if violated
scope_result = self._check_scope_violation(conn, norm_path, agent_id)
if scope_result is not None:
    conn.execute("ROLLBACK")  # Now this works
    return {"acquired": False, "error": "scope_violation", ...}
conn.execute("COMMIT")
```

Also fixed the exception handler to catch "no transaction is active" gracefully.

### Change 2 — `wait_for_task` (tasks.py + core_tasks.py)

Added polling primitive so agents can wait for task dependencies to complete:
- `wait_for_task(task_id, timeout_s=60, poll_interval_s=2)` — blocks until task reaches `completed` or `failed`
- Returns `{"waited": True, "status": "...", "timed_out": False}` or `{"waited": False, "timed_out": True}`

### Change 3 — `get_available_tasks` (tasks.py + core_tasks.py)

Returns tasks whose `depends_on` are all satisfied and are not currently claimed:
- `get_available_tasks(agent_id=None)` — filters to unassigned or specific agent
- A task is "available" if status is `pending` and all deps have status `completed`

### Change 4 — `wait_for_notifications` (notifications.py + core_change.py)

Long-poll primitive for change notifications:
- `wait_for_notifications(agent_id, timeout_s=30, poll_interval_s=2, exclude_agent=None)`
- Returns `{"notifications": [...], "timed_out": False}` when new notifications arrive
- Returns `{"notifications": [], "timed_out": True}` on timeout

### New Tools Added

- `wait_for_task` — poll until task reaches terminal state
- `get_available_tasks` — find tasks with satisfied dependencies
- `wait_for_notifications` — long-poll for change notifications

### New CLI Commands

- `coordinationhub wait-for-task <task_id> [--timeout S]`
- `coordinationhub get-available-tasks [--agent-id <id>]`
- `coordinationhub wait-for-notifications <id> [--timeout S] [--exclude-agent <agent>]`

### Test Coverage

- All 340 tests pass (1 skipped)
- Updated `test_cli.py` expected commands list to include new commands

---

## 2026-04-13 — v0.6.1 Task Priority + Dead Letter Queue

### Motivation

Review Seventeen (`MCP_Findings/Review_Seventeen/coordinationhub.md`) evaluated CoordinationHub under a `MultiAgentTaskDistributor` workload and identified 6 gaps. Investigation confirmed:

| Gap | Status |
|-----|--------|
| Lock Safety | Already implemented (TTL locks, BEGIN IMMEDIATE, smart reap) |
| Inter-Agent Messaging | Already implemented (`messages` table + send_message/get_messages) |
| Task Priority Ignored | **Genuine gap** — priority not in schema |
| No Failure Recovery / DLQ | **Genuine gap** — no retry/DLQ for tasks |
| Centralized Bottleneck | Design-level (HA/replication) — deferred |
| Agent Spawning External | Design-level (elastic scaling) — deferred |

### Change 1 — Task Priority

Tasks can now be created with a `priority` integer (higher = executes first). All task-list queries order by `priority DESC, created_at ASC`.

- **Schema**: `priority INTEGER DEFAULT 0` added to `tasks` table
- **Migration v12**: `ALTER TABLE tasks ADD COLUMN priority INTEGER DEFAULT 0`
- **New index**: `idx_tasks_priority ON tasks(priority DESC, created_at ASC)`
- **`create_task`**: accepts `priority` param (default 0)
- **`create_subtask`**: accepts `priority` param (default 0)
- All getter functions order by priority (FIFO within same priority)
- CLI: `--priority N` flag on `create-task` and `create-subtask`

### Change 2 — Dead Letter Queue

When a task is marked `failed`, the failure is recorded with attempt count. After `max_retries` failures (default 3), the task enters `dead_letter` status.

- **New table**: `task_failures` (schema v13)
  - `task_id`, `error`, `attempt`, `max_retries`, `first_attempt_at`, `last_attempt_at`, `dead_letter_at`, `status`
- **New module**: `task_failures.py` (~105 LOC, zero internal deps)
  - `record_task_failure(connect, task_id, error, max_retries=3)` — called automatically by `update_task_status(status='failed')`
  - `get_dead_letter_tasks(connect, limit=50)` — returns dead letter queue
  - `retry_from_dead_letter(connect, task_id)` — resets task to `pending` and marks DLQ entry as `retried`
  - `get_task_failure_history(connect, task_id)` — full retry history for a task
- **New MCP tools**: `retry_task`, `get_dead_letter_tasks`, `get_task_failure_history`
- **New CLI commands**: `retry-task`, `dead-letter-queue`, `task-failure-history`
- `update_task_status(status='failed', error=...)` now auto-records the failure

### Change 3 — Inter-Agent Messaging Surfaced

Messaging was fully implemented in v0.5.0 but not prominently highlighted. The Review Seventeen "No Inter-Agent Communication" gap was incorrect — no code changes, documentation update only.

### Counts

| Version | Tools | CLI Commands | Schema |
|---------|-------|--------------|--------|
| v0.6.1 | 61 (+3 DLQ) | 64 (+3 DLQ) | 13 |
| v0.6.0 | 58 | 61 | 11 |

Schema version: 11 → 13

---

## 2026-04-13 — v0.6.0 Refactor + Swarm Scale

### Motivation

core.py was 573 lines with 40+ methods spanning 8 unrelated domain groups. Adding tools required touching core.py + dispatch + schemas + CLI — per-feature cost was high. The deferred swarm-scale items also needed addressing.

### Phase 1 — core.py Split into Mixins

core.py replaced with a thin `CoordinationEngine` host class that inherits from 9 focused mixins:

| Mixin | File | Methods |
|-------|------|---------|
| `IdentityMixin` | `core_identity.py` | register_agent, heartbeat, deregister_agent, list_agents, get_lineage, get_siblings, find_agent_by_claude_id, generate_agent_id |
| `MessagingMixin` | `core_messaging.py` | await_agent, send_message, get_messages, mark_messages_read |
| `TaskMixin` | `core_tasks.py` | create_task, assign_task, update_task_status, get_task, get_child_tasks, get_tasks_by_agent, get_all_tasks, create_subtask, get_subtasks, get_task_tree |
| `WorkIntentMixin` | `core_work_intent.py` | declare_work_intent, get_work_intents, clear_work_intent |
| `HandoffMixin` | `core_handoffs.py` | acknowledge_handoff, complete_handoff, cancel_handoff, get_handoffs |
| `DependencyMixin` | `core_dependencies.py` | declare_dependency, check_dependencies, satisfy_dependency, get_blockers, assert_can_start, get_all_dependencies |
| `ChangeMixin` | `core_change.py` | notify_change, claim_file_ownership, get_notifications, prune_notifications, get_conflicts, get_contention_hotspots, status |
| `VisibilityMixin` | `core_visibility.py` | load_coordination_spec, validate_graph, scan_project, get_agent_status, get_agent_tree, get_file_agent_map, update_agent_status, run_assessment, assess_current_session |
| `LockingMixin` | `core_locking.py` | (already separate) acquire_lock, release_lock, refresh_lock, get_lock_status, list_locks, release_agent_locks, reap_expired_locks, reap_stale_agents, broadcast, wait_for_locks |

All mixins follow the host-provided contract: `self._connect()` and `self._storage.project_root`. Cross-mixin calls resolved via MRO (e.g. `deregister_agent` calls `self.release_agent_locks` which is on the host inherited from `LockingMixin`).

### Phase 2 — Dependency Satisfaction Auto-Trigger

When `update_task_status` is called with `status='completed'`, the engine now automatically marks all `agent_dependencies` with `depends_on_task_id=task_id` as satisfied.

New function in `dependencies.py`: `satisfy_dependencies_for_task(connect, task_id)` — `UPDATE agent_dependencies SET satisfied=1, satisfied_at=? WHERE depends_on_task_id=? AND satisfied=0`.

### Phase 3 — SSE Dashboard

`GET /events` — Server-Sent Events stream of dashboard data every 5 seconds, replacing polling-based dashboard. Dashboard HTML updated to use `EventSource('/events')` with automatic fallback to polling if SSE is unavailable.

New CLI: `coordinationhub serve-sse --port 9878` — starts MCP server with SSE dashboard.

### Counts

| Version | Tools | CLI Commands |
|---------|-------|--------------|
| v0.6.0 | 58 | 61 (+serve-sse) |
| v0.5.1 | 58 | 60 |

Schema version: 11 (unchanged)

---

## 2026-04-13 — v0.5.1 Task Hierarchy (Subtasks)

### Motivation

After shipping v0.5.0 (5 Phase 11 features), one remaining item from the original plan was straightforward to layer on top: task hierarchy (parent task → subtasks). The `depends_on` column already tracked prerequisite task IDs but not true parent-child subtask relationships.

### Change

Added `parent_task_id` column to `tasks` table, enabling nested task trees with compression chains.

### New Tools

- `create_subtask(task_id, parent_task_id, parent_agent_id, description, depends_on=None)` — create a subtask under an existing parent task
- `get_subtasks(parent_task_id)` — get all direct subtasks of a task
- `get_task_tree(root_task_id)` — get a task with all subtasks recursively as nested tree

### Schema

- Migration v11: `ALTER TABLE tasks ADD COLUMN parent_task_id TEXT`
- New index: `idx_tasks_parent_task ON tasks(parent_task_id)`

### Counts

| Version | Tools | CLI Commands |
|---------|-------|--------------|
| v0.5.1 | 58 | 60 |
| v0.5.0 | 55 | 57 |

Schema version: 10 → 11

---

## 2026-04-13 — v0.5.0 Phase 11 Findings: Multi-Agent Swarm Extensions

### Motivation

Phase 11 findings (`findings/minimax_review_4/coordinationhub.md`) evaluated CoordinationHub under a complex MultiAgentSubprojectOrchestrator workload. The review identified 5 gaps that were reclassified from "future features" to concrete implementation requests by the user. All were implemented in this release.

### Features Added

#### 1. Task Registry
Shared task registry with dependency tracking. Agents can create tasks, assign them, and update status. Task completion summaries enable compression chains for sub-agent result reporting.

- `create_task`, `assign_task`, `update_task_status`, `get_task`, `get_child_tasks`, `get_tasks_by_agent`, `get_all_tasks`
- New table: `tasks` (schema v7)

#### 2. Work Intent Board
Cooperative "I'm working on this" board that signals intent before lock attempts. Proximity warnings appear in lock responses when another agent has declared intent for the same file.

- `declare_work_intent`, `get_work_intents`, `clear_work_intent`
- `_check_work_intent_conflict()` integrated into `acquire_lock` — returns `proximity_warning`, not denial
- New table: `work_intent` (schema v7)

#### 3. One-to-Many Handoffs
Extended `broadcast` with `handoff_targets` parameter. When provided, records a formal handoff to multiple recipients with acknowledgment tracking.

- `handoff_targets: list[str]` on `broadcast` — sends `handoff` messages to each target
- `acknowledge_handoff`, `complete_handoff`, `cancel_handoff`, `get_handoffs`
- New tables: `handoffs`, `handoff_acks` (schema v9)

#### 4. Cross-Agent Dependencies
Declarative dependency graph between agents. Blocks agent startup until dependencies are satisfied.

- `declare_dependency`, `check_dependencies`, `satisfy_dependency`, `get_blockers`, `assert_can_start`, `get_all_dependencies`
- Conditions: `task_completed`, `agent_registered`, `agent_stopped`
- New table: `agent_dependencies` (schema v10)

#### 5. Web Dashboard
Self-contained HTML dashboard with zero external dependencies (no CDN, no Mermaid.js, no D3). Pure SVG agent tree rendering via custom layout algorithm.

- `GET /` — HTML dashboard with polling every 5 seconds
- `GET /api/dashboard-data` — aggregated JSON of all tables
- Agent tree, task board, work intent heat map, handoff list, dependency graph, lock list

### Implementation Notes

- All sub-modules (tasks, work_intent, handoffs, dependencies) follow zero-dependency pattern: receive `connect: ConnectFn` from caller
- Schema migrations are idempotent — `CREATE TABLE IF NOT EXISTS` handles fresh installs; migration lambdas check `PRAGMA table_info` before running ALTER
- Work intent is cooperative: `proximity_warning` in lock response, not a denial. Agents opt in by checking intents before locking.
- Web dashboard SVG tree uses custom BFS layout — no external rendering libraries

### Tool Count

| Version | Tools | CLI Commands |
|---------|-------|--------------|
| v0.4.11 | 35 | 38 |
| v0.5.0 | 55 | 57 |

### Schema Version

v0.4.11: schema v6 → v0.5.0: schema v10 (+4 tables, +12 indexes)

---

## 2026-04-13 — v0.4.11 Phase 11 Findings: MultiAgentSubprojectOrchestrator Review

### Motivation

Phase 11 findings (`findings/minimax_review_4/coordinationhub.md`) evaluated CoordinationHub's multi-agent coordination under a complex MultiAgentSubprojectOrchestrator workload. The system demonstrated strong fundamentals but exposed gaps at scale:

1. **Agent ID Propagation Through Hierarchies** — deep parent-child chains require careful manual tracking
2. **Concurrent Lock Acquisition** — retry mechanism works but no guaranteed ordering, exponential backoff causes long waits at contention hotspots
3. **Scope Boundary Enforcement** — reactive (caught at lock time), not proactive
4. **Handoff Protocol Complexity** — point-to-point only; no broadcast or chain handoffs
5. **Subproject Dependencies** — no native support for declaring inter-agent/subproject dependencies
6. **Agent Failure Cascade** — children orphaned but locks and work not automatically reassigned

### Assessment

CoordinationHub validated positively on all core primitives:
- Agent registration and lifecycle tracking work correctly
- Heartbeat tracking functions as expected
- Basic file locking (exclusive) operates correctly
- Scope checking via `_check_scope_violation` enforces declared scope at lock time
- Agent tree hierarchy visualization renders correctly

The identified gaps are design-level limitations, not bugs:
- **No broadcast/chain handoff** — handoff protocol is intentionally one-to-one; multi-agent workflows use broadcast + polling
- **No subproject/group concept** — agents are individual; groups are a convention enforced by caller
- **Failure cascade handled via existing primitives** — `deregister_agent` orphans children, `reap_stale_agents` cleans up; reassignment is a caller responsibility

### Confirmed Working

| Feature | Status | Notes |
|---------|--------|-------|
| Agent registration | ✅ Good | Basic lifecycle works |
| Heartbeat tracking | ✅ Good | Agents stay alive |
| File locking (basic) | ✅ Good | Simple exclusive locks work |
| Scope enforcement | ✅ Reactive | Check happens at lock time |
| Agent tree | ✅ Good | Hierarchy visualization works |
| Concurrent lock retry | ✅ Good | Exponential backoff functional |
| Region locking | ✅ Good | Multi-lock per file on non-overlapping regions |
| Inter-agent messaging | ✅ Good | messages table, send/get/mark_read |

### What's Design-Not-Bug

The following Phase 11 "challenges" are working as designed and require architectural changes beyond bug fixes:

- **Deep hierarchies with manual parent tracking** — `parent_id` is explicit by design; complex hierarchies need convention layers
- **No broadcast handoffs** — `broadcast` exists for announcement; complex workflows use the existing primitives
- **No subproject dependency declarations** — this is a future feature, not a gap in existing functionality
- **Agent failure leaves children orphaned** — `deregister_agent` handles this; reassignment is caller responsibility per design

### No Code Changes

v0.4.11 ships with no source changes. The Phase 11 review confirmed the system is working correctly; identified gaps are intentional design limitations or future feature candidates. Version bumped to sync pyproject.toml and __init__.py with prior changelog.

---

## 2026-04-12 — v0.4.10 Phase 10 Findings: Retry, Scope Enforcement, Messaging, Await

### Motivation

Phase 10 findings (`findings/minimax_review_3/coordinationhub.md`) identified several gaps in CoordinationHub's coordination primitives:

1. **Lock contention is binary** — agents could only succeed or force-steal, no retry with backoff
2. **Scope enforcement was warning-only** — agents could lock outside their declared scope
3. **No inter-agent messaging** — only broadcast notifications via polling
4. **No sequential dependency tracking** — no way to wait for an agent to complete

### Added

**Retry with exponential backoff for `acquire_lock`** (`core_locking.py`):
- New parameters: `retry`, `max_retries`, `backoff_ms`, `timeout_ms`
- When `retry=True`, polls with exponential backoff (100ms → 200ms → 400ms → ...) up to max_retries or timeout
- Returns `attempts` count in response to show how many tries were needed

**Scope enforcement** (`core_locking.py`, `agent_status.py`, `db.py`):
- New `scope` column in `agent_responsibilities` table (JSON array of path prefixes)
- `_check_scope_violation()` checks if agent's declared scope covers the file path
- If scope violated, lock acquisition is **denied** (not just warned) with `error: "scope_violation"`
- `update_agent_status` tool now accepts optional `scope` parameter

**Agent dependency tracking** (`core.py`):
- New `await_agent(agent_id, timeout_s)` method
- Polls agent status until agent is deregistered (completed) or timeout expires
- Returns `{awaited, agent_id, status, waited_s}` or `{awaited: False, status: "timeout", timeout_s}`

**Inter-agent messaging** (`messages.py`, `core.py`, `db.py`):
- New `messages` table: `(id, from_agent_id, to_agent_id, message_type, payload_json, created_at, read_at)`
- New tools: `send_message`, `get_messages`, `mark_messages_read`
- Supports direct query/response patterns between agents

### Schema Changes

- Version: 4 → 5 (messages table added via `CREATE TABLE IF NOT EXISTS`)
- Version: 5 → 6 (scope column added to `agent_responsibilities`)

### Design Decisions

- **Retry backoff is multiplicative**: 100ms × 2^n per retry — avoids thundering herd
- **Scope enforcement is strict**: denied, not warned — prevents accidental boundary crossings
- **Messaging is fire-and-forget**: no delivery guarantees, polling-based retrieval
- **await_agent is polling-based**: no native event subscription, simple implementation

### Counts

- Version: 0.4.9 → 0.4.10
- Schema version: 4 → 6
- Tests: 340 passing (+5 new tests for CLI commands)
- New module: `messages.py` (~80 LOC)
- Tool count: 31 → 35 (+4 new tools)

---

## 2026-04-12 — v0.4.9 Descendant Status in Context Bundle (Multi-Level Hierarchy)

### Motivation

Phase 9 findings and subsequent discussion identified a gap in multi-level agent hierarchies. When an agent spawns a sub-agent, and that sub-agent spawns its own sub-agent (grandchild), the root agent has no efficient way to see what its descendants are doing without making additional API calls. For LLM-based agents, every extra call is a token cost and latency hit. The efficient approach is to embed descendant state in the context bundle returned on every `register_agent` call — no polling, no extra calls.

### Added

**`descendant_registry` table** in `db.py._SCHEMAS` — `(ancestor_id, descendant_id, depth, registered_at)` with primary key on `(ancestor_id, descendant_id)`. Index on `ancestor_id` for fast lookup. Schema version 3 → 4 with no-op migration (table added via `CREATE TABLE IF NOT EXISTS`).

**`_record_descendant_relationship(conn, agent_id, parent_id)` in `agent_registry.py`** — called from `register_agent` when a `parent_id` is present. Walks the ancestor chain upward from the parent, inserting `(ancestor, agent_id)` pairs at each level. Uses `INSERT OR IGNORE` so re-registrations are idempotent. When B registers with parent A → inserts `(A, B)`. When C registers with parent B → inserts `(B, C)` AND `(A, C)` — A immediately knows about its grandchild C.

**`get_descendants_status(connect, ancestor_id)` in `agent_registry.py`** — single JOIN query returning all descendants (all depths) with `depth`, `agent_id`, `status`, `last_heartbeat`, `current_task`. Ordered by depth then agent_id. Includes stopped agents so callers can detect when descendants died.

**`descendants_status` in context bundle (`context.py` → `build_context_bundle`)** — new `descendants_fn` parameter. When provided, calls it and injects the result as `descendants_status` in the bundle. `_context_bundle` in `core.py` passes `descendants_fn=lambda: _ar.get_descendants_status(self._connect, agent_id)` so every `register_agent` response includes live descendant state.

### Design Decisions

- **Write-once, query-many**: `descendant_registry` written only at first registration, not on heartbeats. Keeps writes minimal.
- **Ancestor walk at registration time**: All ancestors get the relationship recorded immediately. Parent knows about grandchildren from birth, not lazily.
- **No staleness threshold in response**: `last_heartbeat` is included; staleness is computed by the LLM/client. Hardcoding a threshold would be wrong for some use cases.
- **Zero new MCP tools**: The feature works through the existing `register_agent` response. No new tool surface.
- **Depth column reserved for future use**: Enables depth-limiting later (e.g., "only show direct children"). No behavior change initially.
- **Still zero external dependencies**: Only `sqlite3`, standard library.

### Counts

- Version: 0.4.8 → 0.4.9
- Schema version: 3 → 4
- Tests: 335 passing (unchanged)
- LOC: `db.py` +2 lines; `agent_registry.py` +~55 lines; `context.py` +2 lines; `core.py` +1 line

---

## 2026-04-12 — v0.4.8 Lock Release on PostToolUse (Findings Phase 9 Fix)

### Motivation

Phase 9 findings (`findings/coordinationhub.md_closed`) identified a critical gap: `PostToolUse(Write/Edit)` was only *refreshing* the lock TTL after a write completed, causing locks to persist for up to 10 minutes (300s TTL). This blocked other agents from working on the same file well after the write operation finished.

The correct behavior (enforcement, not just detection) is: lock acquired before write → write completes → lock released immediately. This allows other agents to acquire the file for their own work without waiting for TTL expiry.

### Changed

**`handle_post_write` in `coordinationhub/hooks/claude_code.py`**:
- Replaced `engine.refresh_lock(file_path, agent_id, ttl=300.0)` with `engine.release_lock(file_path, agent_id)`.
- Lock is now released immediately after successful Write/Edit, not left hanging until TTL expiry.

### Verification

- Full test suite: 335 passed, 1 skipped
- `test_hooks.py`: 66 passed (all PostToolUse contract and functional tests passing)

### Counts

- Version: 0.4.7 → 0.4.8
- Tests: unchanged at 335 passing
- Source LOC: `hooks/claude_code.py` unchanged (the fix is a one-line method call change)
- Hook events handled: unchanged at 8
- MCP tools: unchanged at 31

---

## 2026-04-11 — v0.4.7 Sub-agent Task Correlation (Fake Fixture → Real Event Shape)

### Motivation

Trying to demonstrate v0.4.6's sub-agent task visibility live surfaced three related bugs, all from the same root cause. When asked "what does the task ID look like when assigned to a sub-agent?" I queried the DB and found every sub-agent's `current_task` column was NULL, and every sub-agent ID had the same `.agent.N` suffix regardless of whether it was an Explore, Plan, or general-purpose agent. Event capture (`COORDINATIONHUB_CAPTURE_EVENTS=1`) on two separate sub-agent spawns confirmed the problem: our test fixture for `SubagentStart.json` carried a fabricated event shape that real Claude Code events never match.

**Real `SubagentStart` event (captured live 2026-04-11):**
```json
{
  "hook_event_name": "SubagentStart",
  "session_id": "046b7ee2-26a2-4925-a861-fed6f766072a",
  "cwd": "/home/aron/Documents/coding_projects/CoordinationHub",
  "agent_id": "abb48821c843ed534",
  "agent_type": "Explore",
  "transcript_path": "..."
}
```

**Fake fixture we had been shipping since v0.3.7:**
```json
{
  "hook_event_name": "SubagentStart",
  "session_id": "...",
  "subagent_id": "a537efcb034928888",
  "tool_input": {
    "subagent_type": "agent",
    "description": "Build feature module"
  }
}
```

Real events use `agent_id` + `agent_type` at the top level. Our fixture invented `subagent_id` + `tool_input.subagent_type` + `tool_input.description`. Three symptoms from one root:

| Bug | Root cause | Production impact |
|---|---|---|
| A | `_subagent_id` read `tool_input.subagent_type` (absent) and fell through to the default `"agent"` | All sub-agent IDs collapsed to `.agent.N`; no type discrimination between Explore/Plan/general-purpose |
| B | `handle_subagent_start` read `tool_input.description` (absent) | Sub-agent `current_task` was always NULL despite a description being passed |
| C | `SubagentStart.json` contract fixture fabricated the shape | Tests passed against a reality-fiction gap; contract check was vacuous |

The description *does* exist in the preceding `PreToolUse` event for `tool_name == "Agent"` — that event carries `tool_input.description`, `tool_input.prompt`, `tool_input.subagent_type`, and `tool_use_id`. But `handle_subagent_start` never saw those fields because the events are separate hook invocations.

v0.4.6's claim of "symmetry between root and sub-agent task visibility" was therefore half-true: root agents worked (v0.4.6's `UserPromptSubmit` handler), sub-agents didn't.

### Added

**`pending_tasks` table and `coordinationhub/pending_tasks.py` module** — a tiny FIFO queue for correlating the two events. Replaces the legacy `pending_subagent_tasks` and `pending_spawner_tasks` tables.

- Schema: `(tool_use_id PRIMARY KEY, session_id, subagent_type, description, prompt, created_at, consumed_at)` in `db.py._SCHEMAS`.
- Index on `(session_id, subagent_type, consumed_at)` for fast FIFO lookup.
- `stash_pending_task(connect, tool_use_id, session_id, subagent_type, description, prompt)` — called by `PreToolUse[Agent]`. Also prunes rows older than 10 minutes on every insert so orphaned rows (Agent tool calls that error before SubagentStart) don't accumulate.
- `consume_pending_task(connect, session_id, subagent_type)` — pops the oldest unconsumed row for the bucket. Returns `None` if nothing is pending.
- `prune_consumed_pending_tasks(connect, max_age_seconds)` — housekeeping for consumed rows.
- Zero internal dependencies; same pattern as `notifications.py` and `conflict_log.py`.

**`handle_pre_agent(event)` in `hooks/claude_code.py`** — reads `tool_input.description`, `tool_input.prompt`, `tool_input.subagent_type`, and `tool_use_id` from the event and calls `stash_pending_task`. No-ops if either `tool_use_id` or `subagent_type` is missing (nothing to correlate on). Wired into `main()` dispatch alongside the existing `PreToolUse[Write|Edit]` handler.

**`Agent` matcher in `_HOOKS_CONFIG["PreToolUse"]`** in `cli_setup.py` — default hook config now registers both `Write|Edit` and `Agent` for PreToolUse. `coordinationhub init` merges the new matcher into `~/.claude/settings.json` via the existing `_merge_hooks` path without clobbering user hooks.

**`_subagent_type(event)` helper** — reads top-level `agent_type` (real shape) with fallback to `tool_input.subagent_type` (legacy fixtures). Used by both `_subagent_id` and `handle_subagent_start` so the agent type is consistent across ID generation and pending-task lookup.

**Fixed fixtures:**
- `tests/fixtures/claude_code_events/SubagentStart.json` — rewritten to real shape with `agent_id` and `agent_type` at the top level, `transcript_path` included, no `tool_input`. The fixture is now a real-capture reference, not a fabrication.
- `tests/fixtures/claude_code_events/PreToolUse_Agent.json` — new fixture for `handle_pre_agent` with the real `tool_input.description` / `.prompt` / `.subagent_type` + `tool_use_id`. Picked up automatically by the parametrized `TestEventContract` class.

**Fixed contract test:**
- `test_subagent_id_is_hex_string` now reads `event["agent_id"]` instead of `event["subagent_id"]` and additionally asserts that `agent_type` is present at the top level.
- `_FIXTURE_HANDLERS` entry for `SubagentStart` now requires `["hook_event_name", "session_id", "agent_id", "agent_type"]`.
- New `PreToolUse_Agent` entry requires `["hook_event_name", "session_id", "tool_name", "tool_use_id", "tool_input.description", "tool_input.subagent_type"]`.

**6 new functional tests** in `TestPreAgentAndSubagentShape`:
- `test_pre_agent_stashes_description_for_subagent_start` — happy path, asserts `current_task` is set and the generated sub-agent ID contains `.Explore.` (real type, not default `.agent.`).
- `test_subagent_start_without_pre_agent_leaves_task_null` — graceful no-op when no pending task exists (unit-test-style isolated SubagentStart).
- `test_fifo_ordering_two_spawns_same_type` — two PreToolUse[Agent] → two SubagentStart → first-in first-out.
- `test_pending_task_bucketed_by_subagent_type` — Explore and Plan don't consume each other's pending tasks even when interleaved.
- `test_subagent_id_uses_real_agent_type` — verifies `hub.cc.*.general-purpose.N` instead of `hub.cc.*.agent.N`.
- `test_subagent_type_helper_prefers_real_shape` — unit test for `_subagent_type` with real/legacy/both/neither inputs.

### Changed

- `handle_subagent_start` rewritten to read `agent_id` first (real shape) with `subagent_id` fallback (legacy), call `consume_pending_task` for the description, and fall back to `event["tool_input"]["description"]` only if no pending task exists (keeps legacy unit-test fixtures passable during transition).
- `main()` dispatch adds a new branch: `hook_event == "PreToolUse" and tool_name == "Agent"` → `handle_pre_agent`.
- The new `_subagent_type` helper is exported so tests can verify it directly; `_subagent_id` delegates to it.

### Live validation

After `coordinationhub init` and spawning an Explore agent with `description="LIVE-TEST-validate-v047-fix"`:

```
agent_id                        status    current_task
------------------------------  --------  ---------------------------
hub.cc.046b7ee2-26a.Explore.0   stopped   LIVE-TEST-validate-v047-fix
hub.cc.046b7ee2-26a.agent.3     stopped
hub.cc.046b7ee2-26a.agent.2     stopped
hub.cc.046b7ee2-26a.agent.1     stopped
hub.cc.046b7ee2-26a.agent.0     stopped
```

The last four rows are pre-fix sub-agents from earlier in the session — all typed `.agent.` with null tasks. The first row is post-fix — typed `.Explore.` with the real description. Same DB, same Claude Code session, same project. The fix lands cleanly on a live swarm.

### Why this escaped earlier reviews

The test suite's `TestEventContract` *was* meant to catch exactly this kind of drift — the docstring in `test_hooks.py` even says "Replace these with real captured events (COORDINATIONHUB_CAPTURE_EVENTS=1) to catch schema drift." Nobody ever used the capture mechanism on the `SubagentStart` fixture. The fixture was written to an imagined shape when the hook was first added (v0.3.7 adoption-friction fixes), tests were built against the fixture, and the "contract" check has been a self-referential loop for months. CLAUDE.md now carries a warning in the "Contract test fixtures" key-design-decision to never write fixtures without live capture.

### Counts

- Version: 0.4.6 → 0.4.7
- Tests: 328 → 336 collected (335 passing + 1 skipped). `test_hooks.py`: 58 → 66 (+6 functional + 2 parametrized contract invocations for the new fixture).
- Source LOC: `coordinationhub/pending_tasks.py` NEW (~105 LOC). `hooks/claude_code.py`: 378 → 438 LOC. `cli_setup.py`: 269 → 272 LOC. `db.py`: 243 → 255 LOC.
- Hook events handled: 7 → 8 (`PreToolUse[Agent]` added).
- MCP tools: unchanged at 31.

---

## 2026-04-11 — v0.4.6 UserPromptSubmit Hook — Root Agent Task Visibility

### Motivation

After v0.4.5 shipped the live-session assessment runner, an audit question surfaced: does the main Claude Code agent actually show *what it's working on* in `get_agent_tree` / `coordinationhub watch`? The answer was no. Sub-agents had their `current_task` column populated automatically by `handle_subagent_start` from the Agent tool's `description` field, but the root session agent had no equivalent path. Its task column stayed `NULL` even while it held file locks and fired `notify_change` events. A user running `coordinationhub watch` in a sidecar terminal would see "the root agent exists and is touching these files" but not "here's what the user asked it to do."

The fix is a new `UserPromptSubmit` hook. Claude Code fires this event every time the user submits a prompt, with the prompt text in the event JSON. A handler that stamps the root agent's `current_task` from the prompt makes the two paths symmetric: sub-agents get their task from the Agent spawn description, the root agent gets its task from the user prompt.

This is passive and automatic — no behavioral dependency on me remembering to call `update_agent_status`. Just install the hook once and every future session populates itself.

### Added

**`handle_user_prompt_submit(event)` in `hooks/claude_code.py`**:

- Reads `event["prompt"]`, strips whitespace, and bails on empty/missing prompts (so the hook stays a no-op if Claude Code ever sends an empty submit event or changes the field name).
- Truncates to 120 characters with an ellipsis suffix to keep the agent tree rendering compact.
- Collapses multi-line whitespace so multi-line prompts render as a single line in `text_tree` output.
- Resolves the root agent via `_session_agent_id(session_id)`, calls `_ensure_registered` (so the hook self-heals if SessionStart was somehow missed), then `engine.update_agent_status(agent_id, current_task=summary)`.
- Wired into `main()` dispatch between SessionStart and PreToolUse.

**`_HOOKS_CONFIG["UserPromptSubmit"]` entry in `cli_setup.py`**:

- New matcher block with `statusMessage: "Stamping current task"`. Picked up by `coordinationhub init` via the existing `_merge_hooks` path, so re-running `init` installs the new hook into `~/.claude/settings.json` without clobbering other hooks.
- `_check_hooks_config()` (the doctor check) now includes `UserPromptSubmit` in its required set, so setups missing the hook show up as FAIL in `coordinationhub doctor`.

**Contract fixture `tests/fixtures/claude_code_events/UserPromptSubmit.json`** with the minimum shape the handler reads: `hook_event_name`, `session_id`, `cwd`, `prompt`. Picked up by the parametrized `TestEventContract` class, which now runs two additional invocations per test (required-fields check + handler-does-not-crash).

**6 new functional tests** in `test_hooks.py::TestUserPromptSubmit`:
- `test_sets_current_task_from_prompt` — happy path, verifies `current_task` equals the prompt text
- `test_truncates_long_prompts` — 500-char prompt → truncated with `...` suffix, ≤120 chars
- `test_collapses_multiline_whitespace` — multi-line prompt renders as a single line
- `test_empty_prompt_is_noop` — empty/whitespace prompt does not overwrite the previous task
- `test_latest_prompt_overwrites_previous` — second prompt replaces the first
- `test_registers_root_when_session_start_missed` — hook self-heals if SessionStart didn't fire

### Note on existing sessions

Claude Code reads `~/.claude/settings.json` at session start. Running `coordinationhub init` installs the hook into the on-disk config but does not retroactively add it to sessions that are already open. The session this commit was authored in showed `current_task: null` for the root agent because the session predated the hook. New sessions (and any existing session that restarts) will populate on the first user prompt.

### Counts

- Version: 0.4.5 → 0.4.6
- Tests: 320 → 328 collected (327 passing + 1 skipped). `test_hooks.py`: 50 → 58 (+6 functional + 2 parametrized contract invocations for the new fixture).
- Source LOC: `hooks/claude_code.py` 352 → 378 (+26 for the new handler). `cli_setup.py` 268 → 269 (+1 for the hook config entry).
- Hook events handled: 6 → 7.

---

## 2026-04-11 — v0.4.5 Live-Session Assessment + Dead-Table Cleanup

### Motivation

Post-v0.4.4 audit surfaced two real issues (and correctly discarded one false alarm):

1. **`run_assessment` had no production consumer.** The engine method, 5 metric scorers, 33 unit tests, MCP tool schema, and `coordinationhub assess --suite <file>` CLI command all existed and worked. But no suite JSON files existed anywhere in the repo, nothing called `run_assessment` automatically, and using it required a user to hand-author a trace suite describing a session after the fact. The v0.4.4 pipeline test had to manually construct a trace to exercise the scorers. The feature was a complete implementation missing exactly one piece: "how do you get from a live session to a scoreable trace?"
2. **`coordination_context` table was dead.** Defined in `db.py._SCHEMAS` since the v0.2.0 initial commit (`git log -S"coordination_context"` returns one result — that commit). Zero reads, zero writes, ever. The only reference outside `db.py` was `test_db_migration.py` asserting the table exists on a fresh DB — a test enforcing the deadness rather than exercising a live use.

`load_coordination_spec` and the graph system were audited at the same time and confirmed to be actively used: `CoordinationEngine.start()` auto-loads the spec on every engine creation (which every hook call triggers), the loaded graph populates `agent_responsibilities` via `_populate_agent_responsibilities_from_graph`, and that table feeds `get_agent_tree`, `scan_project` responsibility inheritance, context bundles, and boundary warnings. Not dead — the opposite of dead.

### Added

**`build_trace_from_db(connect, trace_id, worktree_root=None)` in `assessment.py`** — synthesizes an assessment trace from live DB state. Reads three tables:

- **`agents`** LEFT JOIN `agent_responsibilities` → `register` events carrying `agent_id`, `graph_id` (when a role is mapped), and `parent_id`.
- **`change_notifications`** where `change_type='modified'` → synthetic `lock → modified → unlock` triples so `score_outcome_verifiability` has data to work with. Hooks never emit explicit unlock events, so the converter fabricates them from the fact that each write completed. Microsecond offsets keep the triple in-order even when merged with register events that share a timestamp.
- **`lineage`** where parent and child have distinct graph roles → `handoff` events. Children that inherit the same role as their parent are correctly suppressed.

Events are sorted by timestamp with internal `_ts` sort keys stripped before return, so the output only contains fields documented in the scorer interface.

**`build_suite_from_db(connect, suite_name, worktree_root=None)`** — one-call wrapper that returns `{"name": ..., "traces": [<one trace>]}`.

**`CoordinationEngine.assess_current_session(format, graph_agent_id, scope)`** — scores the live session from live DB state. Works even when no coordination graph is loaded — ad-hoc agent swarms are scored directly. Graph-dependent metrics return 0.0 when no graph is present. `scope="project"` (default) filters to the engine's worktree; `scope="all"` scores every agent in the DB.

**`assess_current_session` MCP tool** — new dispatch entry, schema, and `assess-session` CLI subcommand with `--format`, `--graph-agent-id`, `--scope`, and `--output` flags. Tool count 30 → 31.

### Removed

- `coordination_context` table removed from `db.py._SCHEMAS`. Existing DBs keep the empty table (no drop migration — it would be pure churn, and a dropped table in a migration is harder to reason about than a lingering empty one). `test_db_migration.py` updated to no longer assert its existence.

### Added Tests

- **9 new unit tests** in `test_assessment.py::TestBuildTraceFromDB` — empty DB, single agent with no writes, graph_id + parent_id propagation, lock/modified/unlock triples, `indexed` change type is ignored, handoffs from lineage with distinct roles, no handoffs for same-role children, worktree_root filter, suite wrapping.
- **2 new scenario tests** in `test_scenario.py::TestHookLevelMultiAgentScenario`:
  - `test_assess_current_session_from_live_db` — drives multi-agent hooks, tags sub-agents with graph roles, then calls `assess_current_session` with no hand-built suite. Asserts all 5 metrics scored, `outcome_verifiability > 0` (the synthesized lock/modify pairs are not vacuous), and results persisted.
  - `test_assess_current_session_without_graph_returns_error` — verifies the no-graph path scores the session with `graph_loaded=False` instead of returning an error.

### Fixed (incidental)

- `coordinationhub/__init__.py` `__version__` had stayed at `0.4.3` through v0.4.4 (the CI version-consistency check caught this). Now synced to `0.4.5`.
- Two integration tests (`test_get_tools_returns_all_30_tools`, `test_status_via_http`) hardcoded the tool count at 30. Replaced with `len(TOOL_DISPATCH)` so they track the dispatch table automatically.

### Counts

- Version: 0.4.4 → 0.4.5
- Tests: 309 → 320 collected (319 passing + 1 skipped). `test_assessment.py`: 24 → 33. `test_scenario.py`: 11 → 13.
- MCP tools: 30 → 31 (`assess_current_session` added).
- CLI subcommands: 34 → 35 (`assess-session` added).

---

## 2026-04-11 — v0.4.4 Close Review Thirteen (Assessment + Coordination Graph Pipeline Test)

### Context

Review Thirteen (2026-04-11, RecipeLab, 15 sub-agents across two parallel waves) flagged six gaps. Four were code bugs that had already been fixed in earlier releases; two were untested feature-integration paths that only had unit coverage:

| # | Gap | Closed by | Status at v0.4.3 |
|---|-----|-----------|------------------|
| 1 | SubagentStop did not transition agents to `stopped` | v0.3.8 (`_resolve_agent_id` in `handle_subagent_stop`) | Code + `test_subagent_stop_sets_status_stopped_via_claude_id` |
| 2 | `run_in_background` agents registered twice | v0.3.8 (`find_agent_by_claude_id` dedup in `handle_subagent_start`) | Code + `test_background_agent_dedup` |
| 3 | No same-file lock contention test | v0.4.1 (`test_concurrent_contention_on_same_file` in `test_scenario.py`) | Test |
| 4 | `file_ownership` table not populated | v0.4.0 (`handle_post_write` → `claim_file_ownership`) | Code + `test_file_ownership_first_write_wins` + `test_wave_of_subagents_full_lifecycle` |
| 5 | Assessment scoring never exercised end-to-end | v0.4.4 (`test_coordination_graph_and_assessment_pipeline`) | Covered this release |
| 6 | Coordination graph integration never exercised end-to-end | v0.4.4 (same test) | Covered this release |

Gaps 5 and 6 had comprehensive unit coverage (`test_assessment.py` × 24, `test_graphs.py` × 22, `test_visibility.py` graph loading × multiple), but no single scenario test walked the entire pipeline — load a spec, drive sub-agent activity through the real hook entry points, resolve `hub.cc.*` IDs for the resulting agents, then feed a trace through `run_assessment` with results persisted to SQLite.

### Added

**`test_coordination_graph_and_assessment_pipeline`** in `tests/test_scenario.py`:

1. Writes a two-agent `coordination_spec.json` (planner + builder with a handoff) into the project root.
2. Fires `handle_session_start`, then calls `engine.load_coordination_spec(spec_path)` — asserts `loaded=True` and both agents appear in the returned manifest.
3. Spawns two sub-agents via `handle_subagent_start`, has each call `handle_post_write` on a unique file — exercises the full hook path including `claim_file_ownership`.
4. Resolves the `hub.cc.*` IDs via `find_agent_by_claude_id` **before** deregistering (the lookup filters by `status = 'active'`).
5. Deregisters both via `handle_subagent_stop`.
6. Builds a trace suite mirroring the session (`register`, `lock`, `modified`, `handoff` events) and calls `engine.run_assessment(suite_path, format="json")`.
7. Asserts the result has `graph_loaded=True`, all five metrics scored, overall score in (0, 1], and that `assessment_results` rows were persisted (one per metric).

This is a single test, but it traverses: `load_coordination_spec` → hook handlers → `find_agent_by_claude_id` → `run_assessment` → `store_assessment_results`. Any regression in that pipeline will now fail locally instead of only showing up in a live RecipeLab review.

### Files Changed

- `tests/test_scenario.py`: +1 test (`TestHookLevelMultiAgentScenario` now has 6 tests, up from 5).
- `pyproject.toml`: 0.4.3 → 0.4.4.

### Counts

- Tests: 308 → 309 collected (308 passing + 1 skipped).
- Files: unchanged.

---

## 2026-04-11 — v0.4.3 Review Fourteen Root Cause

### Investigation

Review Fourteen (conducted on RecipeLab_alt, 2026-04-11) reported three
symptoms on a live swarm test:

1. `agent-tree` errored with `no such column: region_start`.
2. Parallel general-purpose sub-agent writes to the same file silently
   overwrote each other; neither sub-agent appeared in the CoordinationHub
   registry. The reviewer concluded SubagentStart was not firing for
   general-purpose sub-agents (only for Explore).
3. `list-agents` and `dashboard` disagreed on agent status.

The initial fix assumed symptom (2) was a Claude Code hook-coverage gap
and added an `auto_register` fallback in `_resolve_agent_id`. Post-fix
investigation invalidated that assumption:

* `~/.coordinationhub/hook.log` showed **SubagentStart and SubagentStop
  events firing for general-purpose sub-agents** on 2026-04-11 — they
  just crashed inside `register_agent` with `table agents has no column
  named claude_agent_id`. Earlier the same day, 300+ PreToolUse calls
  had crashed with `no such column: region_start`. Every hook call had
  been silently failing for hours.
* The DB was in a "stuck-version" state: `schema_version=3` stamped by
  an earlier buggy `init_schema` path that ran a no-op fresh-install
  branch on existing tables, then recorded the version. The actual
  v1→v2 and v2→v3 migrations never ran, so tables stayed at v1 while
  the recorded version advanced.
* A live test after the DB fix (spawning a general-purpose sub-agent
  from Claude Code) registered correctly as `hub.cc.{session}.agent.0`
  with `claude_agent_id` populated and `parent_id` linked. Proving
  SubagentStart fires for every sub-agent type.

All three symptoms were downstream of the DB bug. The `auto_register`
fallback was reverted — it was solving a problem that didn't exist.

### Fixed

**`db.py init_schema` is now idempotent and self-healing.** Every
call:

1. Creates `schema_version` if missing.
2. Runs `CREATE TABLE IF NOT EXISTS` for every table in the latest
   shape (no-op on existing tables, adds any missing tables for
   legacy DBs).
3. Runs **every** migration in version order unconditionally — each
   migration checks `PRAGMA table_info` and skips work already applied,
   so stuck DBs stamped at v3 but still shaped like v1 get their
   `document_locks` restructured and `agents.claude_agent_id` added
   on the next call.
4. Creates all indexes after migrations, so index DDL always references
   the latest column set.
5. Overwrites `schema_version` with `_CURRENT_SCHEMA_VERSION`.

This is the root-cause fix. Once hooks stop crashing, sub-agent
registration, lock acquisition, file ownership, and SubagentStop all
work without further intervention.

**`cmd_list_agents` and `cmd_dashboard` both call `reap_stale_agents`
before querying** so their output converges on the same DB state.
Previously a stale agent with `status='active'` and an old heartbeat
would render as `active (STALE)` in list-agents but `[stopped]` in
dashboard (or vice versa) depending on which command last ran.

### Added

- `tests/test_db_migration.py` — 7 tests for legacy, stuck-version, and
  fresh-install schema paths. Includes the exact broken DB state found
  in the project (`schema_version=3` with `document_locks` in v1 shape).
- `TestListAgentsDashboardConsistency` in `tests/test_cli.py` — 3 tests
  that seed a stale agent, run each CLI command, and verify the DB is
  left in the same state.

### Reverted from the intermediate fix

An earlier iteration of this commit added `_looks_like_raw_claude_id`,
an `auto_register` parameter to `_resolve_agent_id`, and 5 tests in
`TestAutoRegisterUnmappedSubagent`. All of it was removed once the DB
fix turned out to be sufficient. The hook file matches v0.4.2 exactly.

### Test Count

297 → 308 tests (+11, across 17 files). All passing.

---

## 2026-04-11 — v0.4.2 Auto-Generated Doc Sections

### Motivation

Documentation drift was compounding: file inventory tables, directory trees, test counts, and tool counts appeared in 5 docs and needed manual updating on every change. Three times in this session I caught drift after-the-fact.

### Added

**`scripts/gen_docs.py`** — stdlib-only script (~230 LOC) that scans `coordinationhub/` and rewrites marker blocks in target docs. Six generators:
- `file-inventory` — Markdown table with path, LOC, and module docstring first-line
- `directory-tree` — ASCII tree grouped by directory with per-file LOC
- `mcp-tools` — Table of all MCP tools with descriptions (auto-extracted from `TOOL_SCHEMAS`)
- `test-count` — Integer count from `pytest --collect-only`
- `tool-count` — Integer count from `len(TOOL_SCHEMAS)`
- `version` — Version string from `pyproject.toml`

Modes:
- `python scripts/gen_docs.py` — rewrite in place
- `python scripts/gen_docs.py --check` — exit 1 on drift (CI mode)

**CI drift check** in `.github/workflows/test.yml` — runs `gen_docs.py --check` and fails the build if any doc is out of date.

### Marker conventions

Block markers for multi-line content:
```markdown
<!-- GEN:file-inventory -->
| Path | LOC | Purpose |
|------|-----|---------|
| `coordinationhub/__init__.py` | 14 | CoordinationHub — multi-agent swarm coordination MCP server |
| `coordinationhub/__main__.py` | 10 | ``python -m coordinationhub`` entry point — delegates to :mod:`cli` |
| `coordinationhub/_storage.py` | 113 | Storage backend for CoordinationHub — SQLite pool, path resolution, lifecycle |
| `coordinationhub/agent_registry.py` | 292 | Agent lifecycle: register, heartbeat, deregister, lineage management |
| `coordinationhub/agent_status.py` | 277 | Agent status and file-map query helpers for CoordinationHub |
| `coordinationhub/broadcasts.py` | 106 | Broadcast acknowledgment primitives for CoordinationHub |
| `coordinationhub/cli.py` | 98 | CoordinationHub CLI — command-line interface for all coordination tool methods |
| `coordinationhub/cli_agents.py` | 121 | Agent identity and lifecycle CLI commands |
| `coordinationhub/cli_commands.py` | 98 | CoordinationHub CLI command handlers |
| `coordinationhub/cli_deps.py` | 77 | CLI commands for cross-agent dependency declarations |
| `coordinationhub/cli_intent.py` | 45 | CLI commands for the work intent board |
| `coordinationhub/cli_leases.py` | 150 | CLI commands for HA coordinator lease management |
| `coordinationhub/cli_locks.py` | 323 | Document locking and coordination CLI commands |
| `coordinationhub/cli_parser.py` | 356 | Argument parser for the CoordinationHub CLI |
| `coordinationhub/cli_setup.py` | 255 | CLI commands for setup and diagnostics: ``init``, ``doctor``, ``watch`` |
| `coordinationhub/cli_setup_doctor.py` | 147 | Diagnostic checks for ``coordinationhub doctor`` |
| `coordinationhub/cli_spawner.py` | 115 | CLI commands for HA coordinator spawner — sub-agent registry management |
| `coordinationhub/cli_sse.py` | 35 | CLI commands for SSE dashboard server |
| `coordinationhub/cli_tasks.py` | 239 | CLI commands for the task registry |
| `coordinationhub/cli_utils.py` | 31 | Shared CLI helper functions used by all cli_* sub-modules |
| `coordinationhub/cli_vis.py` | 292 | Change awareness, audit, graph, and assessment CLI commands |
| `coordinationhub/conflict_log.py` | 44 | Conflict recording and querying for CoordinationHub |
| `coordinationhub/context.py` | 93 | Context bundle builder for CoordinationHub agent registration responses |
| `coordinationhub/core.py` | 165 | CoordinationEngine — thin host class that inherits all mixins |
| `coordinationhub/core_broadcasts.py` | 184 | BroadcastMixin — broadcast, handoff dispatch, and cross-agent waits |
| `coordinationhub/core_change.py` | 182 | ChangeMixin — change notifications, file ownership, conflict audit, status |
| `coordinationhub/core_dependencies.py` | 120 | DependencyMixin — cross-agent dependency declarations and checks |
| `coordinationhub/core_handoffs.py` | 117 | HandoffMixin — one-to-many handoff acknowledgment and lifecycle |
| `coordinationhub/core_identity.py` | 95 | IdentityMixin — agent lifecycle and lineage management |
| `coordinationhub/core_leases.py` | 146 | LeaseMixin — HA coordinator lease management |
| `coordinationhub/core_locking.py` | 334 | Locking methods for CoordinationEngine |
| `coordinationhub/core_messaging.py` | 121 | MessagingMixin — inter-agent messages and await |
| `coordinationhub/core_spawner.py` | 193 | SpawnerMixin — HA coordinator sub-agent spawn management |
| `coordinationhub/core_tasks.py` | 193 | TaskMixin — shared task registry with hierarchy support |
| `coordinationhub/core_visibility.py` | 127 | VisibilityMixin — coordination graph, project scan, agent status, assessment |
| `coordinationhub/core_work_intent.py` | 45 | WorkIntentMixin — cooperative work intent board |
| `coordinationhub/db.py` | 93 | SQLite connection pool and public re-exports for CoordinationHub |
| `coordinationhub/db_migrations.py` | 222 | Schema-version tracking, migration functions, and the ``init_schema`` driver |
| `coordinationhub/db_schemas.py` | 287 | Canonical SQLite schema definitions for CoordinationHub |
| `coordinationhub/dependencies.py` | 140 | Cross-agent dependency declaration and satisfaction tracking |
| `coordinationhub/dispatch.py` | 57 | Tool dispatch table for CoordinationHub |
| `coordinationhub/event_bus.py` | 73 | Lightweight thread-safe in-memory pub-sub event bus for CoordinationHub |
| `coordinationhub/handoffs.py` | 96 | Handoff recording and acknowledgement primitives for CoordinationHub |
| `coordinationhub/hooks/__init__.py` | 1 | Hooks package — Claude Code integration via stdin/stdout event protocol |
| `coordinationhub/hooks/base.py` | 238 | Base hook abstraction for CoordinationHub |
| `coordinationhub/hooks/claude_code.py` | 270 | CoordinationHub hook for Claude Code |
| `coordinationhub/hooks/cursor.py` | 99 | CoordinationHub hook adapter for Cursor |
| `coordinationhub/hooks/kimi_cli.py` | 100 | CoordinationHub hook adapter for Kimi CLI |
| `coordinationhub/leases.py` | 197 | Zero-deps lease primitives for HA coordinator leadership |
| `coordinationhub/lock_cache.py` | 180 | In-memory lock cache for CoordinationHub |
| `coordinationhub/lock_ops.py` | 191 | Shared lock primitives used by both local locks and coordination locks |
| `coordinationhub/mcp_server.py` | 234 | HTTP-based MCP server for CoordinationHub — zero external dependencies |
| `coordinationhub/mcp_stdio.py` | 133 | Stdio-based MCP server for CoordinationHub using the ``mcp`` Python package |
| `coordinationhub/messages.py` | 90 | Inter-agent messaging primitives for CoordinationHub |
| `coordinationhub/notifications.py` | 136 | Change notification storage and retrieval for CoordinationHub |
| `coordinationhub/paths.py` | 38 | Path normalization and project-root detection utilities |
| `coordinationhub/pending_tasks.py` | 106 | Pending sub-agent task storage for CoordinationHub |
| `coordinationhub/plugins/__init__.py` | 8 | CoordinationHub plugin system |
| `coordinationhub/plugins/assessment/__init__.py` | 27 | Assessment plugin for CoordinationHub |
| `coordinationhub/plugins/assessment/assessment.py` | 322 | Assessment runner for CoordinationHub coordination test suites |
| `coordinationhub/plugins/assessment/assessment_scorers.py` | 258 | Assessment metric scorers for CoordinationHub |
| `coordinationhub/plugins/dashboard/__init__.py` | 15 | Dashboard plugin for CoordinationHub |
| `coordinationhub/plugins/dashboard/dashboard.py` | 82 | Web dashboard for CoordinationHub — zero external dependencies |
| `coordinationhub/plugins/dashboard/dashboard_css.py` | 91 | CSS for the CoordinationHub dashboard |
| `coordinationhub/plugins/dashboard/dashboard_html.py` | 98 | Self-contained HTML for the CoordinationHub dashboard |
| `coordinationhub/plugins/dashboard/dashboard_js.py` | 437 | Client-side JavaScript for the CoordinationHub dashboard |
| `coordinationhub/plugins/graph/__init__.py` | 31 | Graph plugin for CoordinationHub |
| `coordinationhub/plugins/graph/graphs.py` | 309 | Declarative coordination graph: loader, validator, in-memory representation |
| `coordinationhub/plugins/registry.py` | 41 | Plugin registry for CoordinationHub |
| `coordinationhub/scan.py` | 198 | File ownership scan for CoordinationHub |
| `coordinationhub/schemas/__init__.py` | 56 | Tool schemas for CoordinationHub — all MCP tools |
| `coordinationhub/schemas/audit.py` | 61 | Audit & Status tool schemas for CoordinationHub |
| `coordinationhub/schemas/change.py` | 41 | Change Awareness tool schemas for CoordinationHub |
| `coordinationhub/schemas/coordination.py` | 145 | Coordination Actions tool schemas for CoordinationHub |
| `coordinationhub/schemas/deps.py` | 29 | Cross-Agent Dependencies tool schemas for CoordinationHub |
| `coordinationhub/schemas/dlq.py` | 23 | Dead Letter Queue tool schemas for CoordinationHub |
| `coordinationhub/schemas/handoffs.py` | 23 | Handoffs tool schemas for CoordinationHub |
| `coordinationhub/schemas/identity.py` | 112 | Identity & Registration tool schemas for CoordinationHub |
| `coordinationhub/schemas/intent.py` | 20 | Work Intent Board tool schemas for CoordinationHub |
| `coordinationhub/schemas/leases.py` | 35 | HA Coordinator Leases tool schemas for CoordinationHub |
| `coordinationhub/schemas/locking.py` | 193 | Document Locking tool schemas for CoordinationHub |
| `coordinationhub/schemas/messaging.py` | 41 | Messaging tool schemas for CoordinationHub |
| `coordinationhub/schemas/spawner.py` | 193 | Spawner tool schemas for CoordinationHub |
| `coordinationhub/schemas/tasks.py` | 220 | Task Registry tool schemas for CoordinationHub |
| `coordinationhub/schemas/visibility.py` | 159 | Graph & Visibility tool schemas for CoordinationHub |
| `coordinationhub/spawner.py` | 318 | Zero-deps spawner primitives for HA coordinator sub-agent registry |
| `coordinationhub/task_failures.py` | 95 | Task failure tracking and dead letter queue for CoordinationHub |
| `coordinationhub/tasks.py` | 289 | Task registry primitives for CoordinationHub (work board) |
| `coordinationhub/work_intent.py` | 77 | Work intent board primitives for CoordinationHub |
<!-- /GEN -->
```

Inline markers for single values (render invisibly in Markdown):
```markdown
This project has <!-- GEN:test-count -->404<!-- /GEN --> tests.
```

Unknown marker names raise an error during rewrite (catches typos).

### What stays human

- README.md prose, quickstart, feature pitch — hand-maintained
- CLAUDE.md "Module Design" and "Key Design Decisions" narrative sections
- LLM_Development.md changelog entries
- All "why" discussions, trade-off notes, and examples

### Files changed

- New: `scripts/gen_docs.py`
- Modified: `.github/workflows/test.yml` (added drift check), all 5 doc targets (markers added), `coordinationhub/hooks/__init__.py` (added docstring so auto-gen shows summary)

### Counts

- Version: 0.4.1 → 0.4.2
- Tests: unchanged (298 collected, 297 passing + 1 skipped)

---

## 2026-04-11 — v0.4.1 Close Validation Gap: Hook-Level Integration Tests, Contract Test Hardening

### Motivation

Post-v0.4.0 assessment identified three weak points: contract tests were still synthetic (never validated against real events), `reap_expired_locks` had misleading semantics after the smart-reap addition, and the fundamental validation gap (unit tests vs. real concurrent agents) remained unaddressed.

### Fixed

**Hook-level integration test (`tests/test_scenario.py`):** New `TestHookLevelMultiAgentScenario` class (4 tests) drives the real Claude Code hook handlers in end-to-end concurrent workflows:
- `test_wave_of_subagents_full_lifecycle` — 11 sub-agents register, write, stop with full attribution (mirrors Review Thirteen batch 2)
- `test_concurrent_contention_on_same_file` — two hook handlers race on same file via threading; exactly one wins
- `test_smart_reap_survives_long_model_call` — verifies smart reap refreshes instead of deleting when agent has recent heartbeat
- `test_crashed_agent_locks_reaped` — verifies smart reap still deletes locks held by stale agents

**Contract tests strengthened (`tests/test_hooks.py`):**
- Per-event-type required-field checks via dotted-path (`tool_input.file_path`, `tool_input.subagent_type`)
- Hex-format assertion on `SubagentStart.subagent_id` (Claude Code hex string format)
- New `TestEventCapture` class (2 tests) validates `_save_event_snapshot` writes real files and fails open on I/O errors

**`reap_expired_locks` semantics clarified (`coordinationhub/core_locking.py`):** Engine method now carries a docstring explicitly noting that `agent_grace_seconds > 0` refreshes instead of deleting. Name not changed (would break MCP tool contract and require schema migration for cosmetic fix).

### Audit finding (no code change)

**`hooks/claude_code.py` at ~450 LOC is not a structural problem.** Every function in the file was audited: 1 error logger, 1 event-capture helper, 5 shared helpers, 8 event handlers, 1 dispatch. No dead code, clear section headers. Splitting would create artificial file boundaries that v0.4.0 explicitly removed. Flag withdrawn.

### Counts

- Tests: 290 → 297 (+7: 4 integration, 2 capture, 1 contract format)
- Version: 0.4.0 → 0.4.1

---

## 2026-04-11 — v0.4.0 Architectural Cleanup: Consolidation, Smart Reap, File Ownership, Contract Tests

### Motivation

Post-Review-Thirteen assessment identified 5 architectural issues:
1. **Module count too high** — 13 files existed solely as re-export aggregators or artificial splits driven by the 500 LOC rule.
2. **Version numbering drift** — `__init__.py` and `pyproject.toml` fell out of sync across releases.
3. **No integration tests against real Claude Code events** — synthetic event dicts didn't validate the actual hook contract.
4. **TTL locks expired mid-operation** — 120s TTL was too short for slow model calls, with no refresh mechanism.
5. **File ownership table was dead** — populated only by manual `scan_project` calls, never by hooks.

### Changes

**Module consolidation (13 files deleted):**
- `registry_ops.py` + `registry_query.py` → merged into `agent_registry.py` (~290 LOC)
- 6 `schemas_*.py` files → merged into `schemas.py` (~590 LOC, pure data with group headers)
- `graph.py` + `graph_validate.py` + `graph_loader.py` → merged into `graphs.py` (~330 LOC; also fixes missing `Any` import)
- `responsibilities.py` → inlined into `scan.py`; `visibility.py` (pure re-export) removed, `core.py` imports `agent_status` and `scan` directly
- Net: 42 → 29 Python files in `coordinationhub/`. No external consumers depended on the deleted files.

**Version consistency CI check:**
- Added step to `.github/workflows/test.yml` that extracts version from both `pyproject.toml` and `__init__.py` and fails if they differ.

**Smart lock reaping (`lock_ops.py`, `core_locking.py`, `hooks/claude_code.py`):**
- `reap_expired_locks(agent_grace_seconds=N)` implicitly refreshes expired locks held by agents with a recent heartbeat instead of deleting them. The TTL becomes a fallback for crashed agents, not a hard deadline.
- Hook PreToolUse now passes `agent_grace_seconds=120.0` to `reap_expired_locks`.
- Hook PreToolUse bumps acquired TTL from 120s to 300s.
- Hook PostToolUse refreshes the lock with TTL=300s after `notify_change` (best-effort, fail-open).

**First-write-wins file ownership (`core.py`, `hooks/claude_code.py`):**
- New `CoordinationEngine.claim_file_ownership(path, agent_id)` method does `INSERT OR IGNORE` into `file_ownership`.
- Hook PostToolUse now calls it after `notify_change` — first agent to write a file becomes its owner.
- Populates the previously-dead `file_ownership` table on every sub-agent write.
- Boundary-crossing warnings, agent-tree ownership labels, and file-agent maps now have real data.

**Contract tests (`hooks/claude_code.py`, `tests/fixtures/claude_code_events/`, `tests/test_hooks.py`):**
- New `_save_event_snapshot()` helper activated by `COORDINATIONHUB_CAPTURE_EVENTS=1` env var. Writes raw events to `~/.coordinationhub/event_snapshots/`.
- New `tests/fixtures/claude_code_events/*.json` fixtures for 6 event types (SessionStart, PreToolUse_Write, PostToolUse_Write, SubagentStart, SubagentStop, SessionEnd).
- New `TestEventContract` class with 12 tests — validates required fields and that each handler accepts its fixture without raising.

### Files Changed

- Deleted: `registry_ops.py`, `registry_query.py`, `visibility.py`, `responsibilities.py`, `graph.py`, `graph_validate.py`, `graph_loader.py`, 6× `schemas_*.py`.
- Consolidated: `agent_registry.py` (~290 LOC), `schemas.py` (~590 LOC), `graphs.py` (~330 LOC), `scan.py` (~240 LOC).
- Modified: `lock_ops.py` (smart reap), `core_locking.py` (grace period passthrough), `core.py` (import updates + `claim_file_ownership`), `hooks/claude_code.py` (TTL + ownership + capture + refresh).
- New: 6 fixture JSON files, `TestEventContract` class.
- Tests: 274 → 290. `test_hooks.py`: 33 → 47 (12 contract + 2 ownership). `test_locking.py`: 38 → 40 (2 smart reap).
- CI: `.github/workflows/test.yml` adds version consistency check.

### Counts

- Python files in `coordinationhub/`: 42 → 29.
- Tests: 274 → 290 across 16 files.
- Version: 0.3.8 → 0.4.0 (minor bump for architectural work, not just bug fixes).

---

## 2026-04-11 — v0.3.8 Fix SubagentStop Status Transition & Background Agent Dedup (Review Thirteen)

### Motivation

Review Thirteen tested 15 sub-agents across two parallel waves in RecipeLab. Two bugs surfaced:
1. All 15 agents remained `status: active` after completing — SubagentStop could not find the correct agent to deregister.
2. Background agents (`run_in_background: true`) registered twice with the same `claude_agent_id`, creating duplicate entries.

### Bug Fix 1: SubagentStop Status Transition

**Root cause:** `handle_subagent_stop` used `_subagent_id()` to reconstruct the child ID, but this function generates a NEW sequence-based ID (counting existing children) rather than finding the EXISTING registered agent. SubagentStop events carry the raw Claude hex ID in `subagent_id`, not `tool_use_id`, so the derived ID was always wrong.

**Fix:** Replaced `_subagent_id()` call with `_resolve_agent_id()`, which looks up the `hub.cc.*` child ID from the `claude_agent_id` column via `find_agent_by_claude_id`. Falls back to `_subagent_id` derivation only when `_resolve_agent_id` returns the session root (no `subagent_id` in the event).

### Bug Fix 2: Background Agent Double Registration

**Root cause:** `handle_subagent_start` deduplicated by comparing the GENERATED `child_id` against existing agent IDs. Background agents fire SubagentStart twice with the same `claude_agent_id` but different sequence numbers, so the dedup check always failed.

**Fix:** Added early `find_agent_by_claude_id` check before generating a new child ID. If an agent with the same raw Claude hex ID already exists, heartbeat it instead of creating a duplicate.

### Files Changed

- `hooks/claude_code.py`: ~400 LOC → ~428 LOC. `handle_subagent_stop` rewritten to use `_resolve_agent_id`. `handle_subagent_start` adds `find_agent_by_claude_id` dedup.
- `tests/test_hooks.py`: 31 → 33 tests. New: `test_subagent_stop_sets_status_stopped_via_claude_id`, `test_background_agent_dedup`.

### Counts

- Tests: 272 → 274 across 16 files. `test_hooks.py`: 31 → 33.

---

## 2026-04-11 — v0.3.7 Adoption Friction Fixes: init, doctor, watch, error logging, session summary

### Motivation

Five adoption friction points identified during post-Review-Twelve analysis:
1. Silent failure masking (`except Exception: pass`) hid bugs across multiple review cycles.
2. No one-command setup — users manually edited `~/.claude/settings.json`.
3. No "is it working?" signal during normal operation.
4. The venv trap — `python3` in hooks resolved to a venv Python without coordinationhub.
5. Dashboard is pull-only — no live view during multi-agent sessions.

### New Commands

- **`coordinationhub init`** — One-command setup: creates `.coordinationhub/`, initializes DB, writes/merges hook config into `~/.claude/settings.json` using `sys.executable` (absolute path, avoids venv trap), then runs doctor checks.
- **`coordinationhub doctor`** — 5 diagnostic checks: importability, hooks config, storage dir, schema version, hook Python interpreter. Returns structured OK/FAIL per check.
- **`coordinationhub watch [--interval N]`** — Live-refresh agent tree with status bar (agents, locks, conflicts). Ctrl+C to stop.

### Hook Improvements

- **Error logging to `~/.coordinationhub/hook.log`** — Timestamps, tracebacks, auto-truncation at 1 MB. Also prints to stderr. Replaces `except Exception: pass` in main dispatch.
- **Session summary on SessionEnd** — Returns "Session summary: N agents tracked, N locks held, N conflicts, N notifications" as `additionalContext`.

### Hook Merge Logic

`_merge_hooks(existing, new)` merges CoordinationHub hooks into existing config:
- For each event type, checks if a coordinationhub hook already exists by command string.
- If found, updates the command (e.g., new Python path). If not, appends.
- Preserves all non-CoordinationHub hooks (e.g., user's custom Bash lint hooks).
- Idempotent: running `init` twice produces identical config.

### Files Changed

- New: `cli_setup.py` (~348 LOC), `tests/test_setup.py` (8 tests).
- Modified: `hooks/claude_code.py` (~330 → ~400 LOC), `cli.py` (~237 → ~267 LOC), `cli_commands.py` (~44 → ~51 LOC).
- Docs: all 4 docs updated (README, COMPLETE_PROJECT_DOCUMENTATION, LLM_Development, CLAUDE.md).

### Counts

- CLI commands: 31 → 34.
- Tests: 261 → 272 across 16 files (was 15). `test_hooks.py`: 28 → 31. New: `test_setup.py` (8).

---

## 2026-04-10 — v0.3.6 Fix Sub-Agent ID Mismatch (Review Twelve)

### Root Cause

All sub-agent coordination failures (missing parent_id, 0 locks, ghost agents, broken assessment) traced to a single bug: `_resolve_agent_id` in the Claude Code hook returned the raw Claude Code hex ID (e.g. `ac70a34bf2d2264d4`) instead of the `hub.cc.*` child ID that SubagentStart created. This caused each sub-agent to exist twice in the DB — once properly parented (from SubagentStart, never used for tool calls) and once as a ghost (from PreToolUse, with no hierarchy).

### Fix

- **`claude_agent_id` column** added to agents table (schema v2 → v3 with auto-migration). Stores the raw Claude Code hex ID on the `hub.cc.*` agent record during SubagentStart.
- **`_resolve_agent_id`** now accepts an engine parameter. When a raw Claude Code ID is present, it queries `find_agent_by_claude_id` to map it back to the `hub.cc.*` child before proceeding. Falls back to raw ID only when no mapping exists (backward compat).
- **`handle_subagent_start`** now extracts the raw `subagent_id`/`agent_id` from the event and passes it as `claude_agent_id` during registration.
- All handlers (`handle_pre_write`, `handle_post_write`, `handle_post_stele_index`, `handle_post_trammel_claim`) now pass the engine to `_resolve_agent_id`.

### Cascade of Fixes

1. **parent_id populated** — Sub-agents queried from PreToolUse/PostToolUse now resolve to the properly-parented `hub.cc.*` entries.
2. **Locks acquired under correct ID** — Locks are associated with the hierarchical agent, not a disconnected ghost.
3. **No ghost duplication** — A single agent record per sub-agent.
4. **Assessment scoring works** — 5-metric assessment can now compute on sub-agents with proper parent-child relationships.
5. **Change notifications attributed correctly** — PostToolUse notifications use the `hub.cc.*` ID, not the raw hex.

### Files Changed

- `db.py`: ~280 → ~295 LOC. `_CURRENT_SCHEMA_VERSION = 3`, `_migrate_v2_to_v3`, `claude_agent_id` column + index.
- `registry_ops.py`: ~106 → ~145 LOC. `claude_agent_id` param on `register_agent`, new `find_agent_by_claude_id`.
- `core.py`: ~280 → ~285 LOC. `claude_agent_id` passthrough + `find_agent_by_claude_id` method.
- `agent_registry.py`: re-exports `find_agent_by_claude_id`.
- `hooks/claude_code.py`: ~310 → ~330 LOC. Engine-aware `_resolve_agent_id`, SubagentStart stores mapping.
- `test_hooks.py`: 23 → 28 tests (5 new: mapping, lock ID, no ghosts, post-write ID, fallback).

### Counts

- Tests: 256 → 261 across 15 files.
- Schema version: 2 → 3.

---

## 2026-04-10 — v0.3.5 Ownership-Aware Locking & Contention Hotspots

### New Features

**Ownership-aware locking (`core_locking.py`):**
- `acquire_lock` now cross-checks the `file_ownership` table after acquiring a lock.
- When an agent locks a file assigned to a different agent, the response includes `ownership_warning: {owned_by: "<owner_agent_id>", message: "..."}`.
- A `boundary_crossing` conflict is recorded in `lock_conflicts` with resolution `allowed`.
- A `boundary_crossing` change notification is fired so the owning agent (or project manager) discovers the incursion via `get_notifications`.
- Self-lock refreshes (re-acquiring own lock) skip the ownership check entirely — no false warnings.

**`get_contention_hotspots` MCP tool + CLI (`core.py`, `schemas_audit.py`, `dispatch.py`, `cli_vis.py`, `cli.py`):**
- Queries `lock_conflicts` grouped by `document_path`, ranked by conflict count descending.
- Returns `{hotspots: [{document_path, conflict_count, agents_involved}], total}`.
- CLI: `coordinationhub contention-hotspots [--limit N]`.
- Identifies coordination chokepoints — files that multiple agents need concurrent access to.

**Rich agent tree (`agent_status.py`):**
- `get_agent_tree` now renders a project-management-style view: each agent node shows its current task, active file locks (with lock type and region info), and boundary crossing warnings.
- Any agent in the swarm calls `agent-tree` to see the same live hierarchy — shared situational awareness across the swarm.
- `agent_status.py`: ~225 LOC → ~290 LOC (rich tree renderer extracted as `_render_rich_tree` / `_render_node`).

### Motivation (Review Eleven)

These features directly address gaps observed during a 3-agent parallel refactor:
1. Agent A crossed into Agent B's file territory undetected — ownership-aware locking now surfaces this.
2. `routeLoader.js` was a coordination chokepoint touched by every agent — contention hotspots tool now identifies such files.

### Counts

- MCP tools: 29 → 30
- CLI commands: 30 → 31
- Tests: 246 → 256 across 15 files. `test_conflicts.py`: 6 → 16 tests.

---

## 2026-04-10 — Review Eleven Findings (No Code Changes)

### Summary

CoordinationHub was indirectly challenged during a 3-agent parallel refactor in RecipeLab (DistributedRecipeValidator feature + multi-agent refactor). The feature phase did not exercise CoordinationHub (single-agent service simulating consensus, not real multi-agent coordination). The refactor phase spawned 3 agents with prompt-based file boundaries — no automated locking.

### Key Validations

1. **Prompt-based boundaries are fragile** — Agent A crossed into Agent B's territory (modified `mcpChallengeRoutes.js`, a route file assigned to Agent B) because import paths changed. `acquire_lock` contention detection would have caught this.
2. **Completion order matters** — Agent B finished before Agent A, creating a race condition on `routeLoader.js`. `wait_for_locks` + `notify_change` would have sequenced this.
3. **Region locking is needed** — `routeLoader.js` is a coordination chokepoint touched by every feature/route change. Region locking (`region_start`/`region_end`) would allow multiple agents to lock non-overlapping sections.
4. **Manual agent tracking is inferior** — Task IDs + output polling vs `register_agent`/`get_agent_tree`/`heartbeat`.
5. **No overwrites occurred (lucky)** — careful pre-partitioning prevented conflicts, but this is exactly the fragile coordination CoordinationHub automates.

### Remaining Gaps (Future Testing)

- Actually connect CoordinationHub MCP server in a multi-agent workflow
- Test region-based locking on shared files
- Test cascade orphaning by killing an agent mid-work
- Test `broadcast` for conflict pre-check before writes
- Compare prompt-based vs lock-based coordination on overlapping file assignments

### Verdict

No code changes required. Existing design (region locking, cascade orphaning, lock contention, change notifications) addresses all observed coordination problems. Findings closed.

---

## 2026-04-10 — v0.3.4 Core Split, Assessment Synonyms, SQLite Perf

### New Features

**`core_locking.py` — LockingMixin extraction:**
- All locking and coordination methods extracted from `core.py` into `core_locking.py` (~230 LOC) as `LockingMixin`.
- `CoordinationEngine` inherits from `LockingMixin`, keeping `core.py` focused on identity, change awareness, audit, and graph/visibility (~260 LOC, down from ~495).
- Methods moved: `acquire_lock`, `release_lock`, `refresh_lock`, `get_lock_status`, `list_locks`, `release_agent_locks`, `reap_expired_locks`, `reap_stale_agents`, `broadcast`, `wait_for_locks`.

**Assessment keyword matching improved (`assessment_scorers.py`):**
- `_EVENT_RESPONSIBILITY_MAP` expanded with ~20 synonyms covering common event-type variations.
- Token-overlap fallback for unknown event types that don't match any mapped keyword.
- `assessment_scorers.py`: ~304 → ~315 LOC.

**SQLite performance tuning (`db.py`):**
- `PRAGMA cache_size=-8000` (8MB page cache, up from default 2MB).
- `PRAGMA mmap_size=67108864` (64MB memory-mapped I/O).
- New composite expiry index `idx_locks_expiry` for faster lock reaping queries.
- `db.py`: ~275 → ~280 LOC.

### Counts

- MCP tools: 29 (unchanged)
- CLI commands: 30 (unchanged)
- Tests: 246 across 15 files (unchanged)

---

## 2026-04-10 — v0.3.3 Region Locking & CI

### New Features

**CI test workflow:**
- `.github/workflows/test.yml` runs pytest on push/PR across Python 3.10-3.12.

**DB schema versioning (db.py):**
- `schema_version` table with `_CURRENT_SCHEMA_VERSION = 2`.
- Migration runner `_migrate_v1_to_v2` restructures `document_locks` for region locking.
- `init_schema()` auto-migrates existing databases on startup.

**Region locking (lock_ops.py, core.py, schemas_locking.py):**
- `document_locks` table changed from `document_path TEXT PRIMARY KEY` to `id INTEGER PRIMARY KEY AUTOINCREMENT` with `region_start INTEGER` and `region_end INTEGER` columns.
- Multiple locks per file on non-overlapping regions. Shared locks enforced (multiple shared allowed, exclusive blocks all).
- New functions: `_regions_overlap`, `find_conflicting_locks`, `find_own_lock`.
- `acquire_lock` uses `BEGIN IMMEDIATE` for thread-safe concurrent locking.
- All locking tools (`acquire_lock`, `release_lock`, `refresh_lock`, `get_lock_status`, `list_locks`) support `region_start`/`region_end` params.
- CLI commands `acquire-lock`, `release-lock`, `refresh-lock` have `--region-start`/`--region-end` flags.

**Hook unit tests:**
- New `tests/test_hooks.py` with 23 tests covering all Claude Code hook handlers.

### Counts

- MCP tools: 29 (unchanged)
- CLI commands: 30 (unchanged)
- Tests: 206 → 246 across 15 files (was 14). `test_locking.py`: 21 → 38 tests.
- `lock_ops.py`: ~119 → ~175 LOC. `db.py`: ~215 → ~275 LOC. `core.py`: ~470 → ~495 LOC.

---

## 2026-04-10 — v0.3.2 Review Ten Fixes

Addresses findings from RecipeLab Review Ten (6-feature parallel build + refactoring phase).

### Bug Fix

**Stale locks from completed agents (Review Ten bug #1):**
- Hook TTL reduced from 600s to 120s — prevents completed-agent locks from blocking work for 10 minutes.
- `handle_pre_write` now calls `reap_expired_locks()` before `acquire_lock()` — cleans up stale locks from crashed agents as a safety net.
- `acquire_lock` already handled expired locks via TTL check, but the combination of shorter TTL + pre-acquire reaping ensures faster cleanup.

### New Feature

**`list_locks` tool + `list-locks` CLI:**
- `list_locks(agent_id?)` — lists all active (non-expired) locks with document path, holder, expiry time, lock type, worktree.
- `list-locks` CLI: `coordinationhub list-locks [--agent-id <id>]`.
- Added to dispatch table, schemas (schemas_locking.py), CLI parser.
- 5 new tests in `test_locking.py`.

### Counts

- MCP tools: 28 → 29
- CLI commands: 29 → 30
- Tests: 202 → 206

---

## 2026-04-07 — v0.3.1 Polish Pass

### New Features

**`spawn_propagation` assessment metric (assessment.py):**
- New scorer `score_spawn_propagation(trace, graph)` verifies child agents inherit and act within their parent's declared responsibilities.
- Events from a child agent are checked against the union of the child's own scope and the parent's scope.
- Always included in metrics even if not listed in graph's `assessment.metrics`.
- `_suggest_graph_refinements(suite, graph)` returns `missing_handoff` and `missing_agent` suggestions for graph refinement.

**Graph-role-aware file scan (scan.py):**
- `_role_based_agent(graph, path)` maps file extension to graph role: `.py` → `implement`/`write`, `.md/.yaml/.yml` → `document`/`plan`, `.json/.toml` → `config`/`data`.
- `_get_spawned_agent_responsibilities(connect, agent_id)` resolves a spawned agent's parent's graph role from the lineage table.
- Scan assignment priority: exact path → nearest ancestor → graph role → spawned-agent inheritance → first-registered fallback.
- `SKIP_PARTS` expanded to include `.git`, `.venv`, `venv`, `.env`, `.eggs`, `*.egg-info`, `.mypy_cache`, `.tox`, `.ruff_cache`.

**`run_assessment` graph_agent_id filter:**
- `graph_agent_id` param filters traces to only those where a `register` event uses that `graph_agent_id`.
- Added to `core.py`, `dispatch.py`, `schemas_visibility.py`, `cli.py`, `cli_vis.py`.
- CLI: `coordinationhub assess --suite <file> --graph-agent-id planner`.

**Full trace storage in SQLite:**
- `run_assessment` result now includes `full_trace_json` (JSON-encoded traces) and `suggested_refinements`.
- `store_assessment_results` persists both in `details_json` alongside per-metric scores.
- Markdown report updated to show filter info and suggested graph refinements section.

### Visibility / Dashboard Improvements

**Dashboard JSON mode (`cli_vis.py`):**
- `dashboard --json` now includes the full `file_map` with each entry carrying `graph_agent_id`, `role`, `responsibilities`, and `task_description`.

**`get_agent_status` and `get_file_agent_map` (agent_status.py):**
- `get_agent_status` now returns `owned_files_with_tasks`: list of `{file, task}` dicts per owned file.
- `get_file_agent_map` now includes `graph_agent_id` in each entry alongside `role` and `responsibilities`.

**Graph auto-mapping on load (graphs.py):**
- `_populate_agent_responsibilities_from_graph(connect, graph)` called inside `load_coordination_spec_from_disk` after a successful load.
- For each graph agent whose id matches an active registered agent, `agent_responsibilities` is upserted.

### Input Validation

- `load_coordination_spec(path)`: returns `{"loaded": False, "error": "Coordination spec not found: <path>"}` when explicit path does not exist.
- `scan_project(extensions=[])`: returns `{"scanned": 0, "owned": 0, "error": "extensions list cannot be empty"}`.

### Code Quality

- All 7 graph/visibility tool methods in `core.py` now have comments explaining their relationship to the lock/lineage foundation.
- `visibility.py` re-exports `_role_based_agent` and `_get_spawned_agent_responsibilities` from `scan.py`.
- All schema files: schemas_identity (~123 LOC), schemas_locking (~145 LOC), schemas_coordination (~59 LOC), schemas_change (~77 LOC), schemas_audit (~43 LOC), schemas_visibility (~156 LOC) — all well under 500 LOC.

### Tests

15 new tests added across `test_assessment.py` and `test_visibility.py`:
- `score_spawn_propagation`: child within scope, child outside scope, coordination always OK, empty trace
- `run_assessment`: spawn_propagation included, graph_agent_id filter, full trace stored, suggested refinements
- `format_markdown_report`: with refinements section
- Graph auto-mapping on load
- Spawned agent inherits parent role during scan
- `get_agent_status` includes `owned_files_with_tasks`
- `get_file_agent_map` includes `graph_agent_id`
- `scan_project` empty extensions returns error
- Dashboard JSON output structure

Total: **165 tests** (up from 150).

### Example Files

- `coordination_spec.yaml` — YAML format example with `planner`, `executor`, `reviewer` agents
- `coordination_spec.json` — JSON format equivalent
- README.md updated to reference both with relative links

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

**`schemas.py` split into group files:**
- `schemas.py` (~31 LOC): Schema aggregator — imports all groups, re-exports `TOOL_SCHEMAS`
- `schemas_identity.py` (~123 LOC): Identity & Registration (6 tools)
- `schemas_locking.py` (~145 LOC): Document Locking (7 tools)
- `schemas_coordination.py` (~59 LOC): Coordination Actions (2 tools)
- `schemas_change.py` (~77 LOC): Change Awareness (3 tools)
- `schemas_audit.py` (~43 LOC): Audit & Status (2 tools)
- `schemas_visibility.py` (~156 LOC): Graph & Visibility (8 tools)
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
- **Coordination context bundle**: Agent registration returns a JSON bundle with `agent_id`, `registered_agents`, `active_locks`, `pending_notifications`, and `coordination_url` — parent agents pass this to spawned sub-agents
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
