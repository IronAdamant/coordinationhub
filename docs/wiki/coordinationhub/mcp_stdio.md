# coordinationhub/mcp_stdio.py

The actual MCP transport: a JSON-RPC 2.0 stdio server built on the optional `mcp` Python package, exposing every CoordinationHub tool to MCP-aware clients (Claude Desktop, Cursor, etc.). Entry points: `coordinationhub-mcp` console script (pyproject) and `python -m coordinationhub.mcp_stdio`.

## Key Functions / Classes
- `create_server(storage_dir, project_root, namespace)` — builds and starts a `CoordinationEngine`, returns `(mcp.server.Server, engine)`; raises `RuntimeError` if the `mcp` package is missing; closes the engine if configuration fails.
- `_configure_server(engine)` — registers `list_tools` (from `TOOL_SCHEMAS`) and `call_tool` handlers; gives each server its own bounded `ThreadPoolExecutor` attached as `server.coordhub_executor` (T7.40).
- `_run_server()` — async main loop: reads `COORDINATIONHUB_STORAGE_DIR` / `COORDINATIONHUB_PROJECT_ROOT` / `COORDINATIONHUB_NAMESPACE` env vars, installs SIGTERM/SIGINT handlers on the asyncio loop for graceful shutdown (T7.41; silently skipped on Windows), and in `finally` shuts down the executor then `engine.close()` (flushes the WAL).
- `main()` — sync entry point; prints install instructions and exits 1 if `mcp` is unavailable.

## Design Notes
- T7.40: tool dispatch runs on an explicit pool sized by `COORDINATIONHUB_MCP_EXECUTOR_MAX_WORKERS` (default 8) instead of asyncio's shared default pool — deterministic lifecycle, caps concurrent DB pressure.
- T2.3: tool exceptions return a generic `"Error: Internal tool execution error (correlation_id=...)"` `TextContent`; the MCP SDK's `call_tool` decorator has no error envelope, so clients detect failure by the `"Error:"` prefix.
- Like the admin server, this server does NOT register itself as an agent (middleware, not a swarm participant).
- Requires `pip install coordinationhub[mcp]`; `_MCP_AVAILABLE` is probed at import and gates everything.

## Relationships
Imports: `.core` (`CoordinationEngine`), `.mcp_server` (`dispatch_tool` — the back-compat alias for `.dispatch`), `.schemas` (`TOOL_SCHEMAS`).
Imported by: `coordinationhub/cli_agents.py` (`from .mcp_stdio import main as mcp_main`); wired as the `coordinationhub-mcp` console script in `pyproject.toml`.
