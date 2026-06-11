# coordinationhub/__init__.py

Package root for CoordinationHub — the multi-agent swarm coordination MCP server. Exposes the two public entry classes and the package version.

## Key Functions / Classes
- `CoordinationEngine` — re-exported from `core`.
- `CoordinationHubMCPServer` — re-exported from `mcp_server`.
- `__version__ = "0.7.12"` — single source of truth for the package version.
- `__all__` — exactly the three names above.

## Design Notes
- Stdlib-only core; the optional `mcp` package is needed only for the stdio transport.
- Importing the package pulls in `core` and `mcp_server` (and transitively the storage/subsystem stack) — keep this import light-on-side-effects; no engine or server is constructed at import time.
- Anything not listed in `__all__` (primitives, subsystems, CLI modules) is internal API; external consumers should go through the engine facade or the MCP/CLI surfaces.

## Relationships
Imports: `core` (CoordinationEngine), `mcp_server` (CoordinationHubMCPServer).
Imported by: no in-package module imports the package root (sub-modules use relative imports); consumers are external code and tests (`from coordinationhub import ...`), `cli_setup_doctor.py`'s subprocess `import coordinationhub` health checks, and the console-script / `python -m coordinationhub` entry points.
