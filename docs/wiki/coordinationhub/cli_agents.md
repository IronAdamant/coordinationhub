# coordinationhub/cli_agents.py

Agent identity and lifecycle CLI commands: server start (`serve`, `serve-mcp`), system `status`, and agent `register` / `heartbeat` / `deregister` / `list-agents` / `agent-relations`.

## Key Functions / Classes
- `cmd_serve(args)` — starts `CoordinationHubMCPServer` over HTTP (blocking); prints the bearer auth token, or "Auth: DISABLED" with `--no-auth`.
- `cmd_serve_mcp(args)` — exports storage/project/namespace settings as `COORDINATIONHUB_*` env vars, then delegates to `mcp_stdio.main` (stdio MCP mode).
- `cmd_status(engine, args)` — `engine.status()` summary (replica-capable).
- `cmd_register(engine, args)` — registers an agent with optional parent, graph role, worktree root.
- `cmd_heartbeat` / `cmd_deregister` — liveness ping; teardown reporting orphaned children and released locks.
- `cmd_list_agents(engine, args)` — lists agents, auto-reaping stale ones first.
- `cmd_agent_relations(engine, args)` — lineage (ancestors/descendants) or siblings view.

## Design Notes
- T2.1: `serve` defaults to auth enabled with a random token; `--no-auth` is an explicit opt-in for local-trust scenarios (e.g. multiprocess tests).
- `cmd_list_agents` calls `engine.reap_stale_agents()` before listing so the displayed status matches DB state — fixes the "active (STALE)" vs "[stopped]" inconsistency between list-agents and dashboard reported in Review Fourteen.
- T7.11: stale agents are prefixed with `!` so line-based scripts (`grep "^ *!"`) can find stuck agents without parsing the trailing `(STALE)` literal.
- Read-only commands (`status`, `agent-relations`) use `@_command(replica=True)`; mutating ones use plain `@_command()`. `serve`/`serve-mcp` bypass the decorator entirely (they own server lifecycle, not an engine call).

## Relationships
Imports: `.cli_utils` (`print_json`, `command`); lazily `.mcp_server` (`CoordinationHubMCPServer` in `cmd_serve`) and `.mcp_stdio` (`main` in `cmd_serve_mcp`).
Imported by: `coordinationhub/cli_commands.py` (re-export hub); tests `tests/test_cli.py`, `tests/test_cli_integration.py`, `tests/test_setup.py`.
