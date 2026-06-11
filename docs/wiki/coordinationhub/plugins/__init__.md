# coordinationhub/plugins/__init__.py

Package marker for the CoordinationHub plugin system. Docstring only; documents the three optional capabilities: assessment (coordination trace scoring), graph (coordination graph loading/validation), dashboard (web dashboard + SSE events).

## Key Functions / Classes
- None (docstring-only module).

## Design Notes
- All plugins are loaded by default for backward compatibility (see `registry.PluginRegistry.DEFAULT_PLUGINS`).
- Loading is gated by `registry.ALLOWED_PLUGINS` (T2.5 allow-list); this `__init__` intentionally imports nothing so an unused plugin's dependencies are never touched.

## Relationships
Imports: none.
Imported by: implicitly via any `coordinationhub.plugins.*` import (`registry.py` dynamic loads, `core.py`/`mcp_server.py`/subsystem imports of plugin submodules).
