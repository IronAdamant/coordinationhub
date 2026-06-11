# coordinationhub/plugins/registry.py

Plugin discovery and registration for CoordinationHub. Each plugin module exposes `register_tools(dispatch_table)` and `register_cli(subparsers)`; the registry imports allowed plugins and fans these calls out.

## Key Functions / Classes
- `ALLOWED_PLUGINS` — frozenset `{"assessment", "graph", "dashboard"}`; the only names hub start-up will attempt to import (T2.5).
- `_load_plugin(name)` — allow-list check, then `__import__("coordinationhub.plugins.{name}")`; returns a plugin dict (`name`, `module`, optional `register_tools` / `register_cli`) or `None`. All failures are logged warnings, never raised.
- `PluginRegistry` — loads `DEFAULT_PLUGINS = ("assessment", "graph", "dashboard")` (or an explicit `plugin_names` tuple) at construction; `register_tools(dispatch_table)`, `register_cli(subparsers)`, `list_plugins()`.

## Design Notes
- T2.5 security: arbitrary plugin names — including names smuggled via `plugin_names=...` or `sys.path` manipulation — are rejected before `__import__`, so untrusted modules cannot run on hub start-up. Rejected and broken plugins are silently skipped (warning log only).
- Dispatch-table entries are `name -> (engine_method_name, [arg_names])` tuples; plugins mutate the dict in place.
- Pure stdlib; no engine or DB dependency.

## Relationships
Imports: none within the coordinationhub package (stdlib `logging`/`typing` only; plugin modules are imported dynamically by name).
Imported by: no in-package importers as of this writing — exercised by `tests/test_plugins.py` (`PluginRegistry`, `_load_plugin`).
