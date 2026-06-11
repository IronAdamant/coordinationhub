# coordinationhub/hooks/__init__.py

Package marker for the hooks package — IDE integration via the stdin/stdout event protocol. Docstring only; no exports.

## Key Functions / Classes
- None (single-line docstring module).

## Design Notes
- Deliberately empty: submodules are imported by their full paths (`coordinationhub.hooks.base`, `coordinationhub.hooks.stdio_adapter`), and `stdio_adapter` is invoked as `python -m coordinationhub.hooks.stdio_adapter`, so a heavy package `__init__` would slow every hook invocation.
- Working tree note: the package previously also held `cursor.py` and `kimi_cli.py` adapters; both are deleted in the current uncommitted state, leaving `base.py` + `stdio_adapter.py`.

## Relationships
Imports: none.
Imported by: implicitly via any `coordinationhub.hooks.*` import (`hooks/stdio_adapter.py`, tests).
