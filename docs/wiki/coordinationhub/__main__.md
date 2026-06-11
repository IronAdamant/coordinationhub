# coordinationhub/__main__.py

`python -m coordinationhub` entry point — a thin shim that delegates to the CLI's `main()`.

## Key Functions / Classes
- Module-level `if __name__ == "__main__": sys.exit(main() or 0)` — the only logic; no functions or classes are defined.

## Design Notes
- Exists so users can invoke the CLI without depending on the installed `coordinationhub` console script — e.g. inside a tox env or a CI runner where the script may not be on `PATH`.
- `main() or 0` coerces a `None` return from the CLI into exit code 0.
- Keep this file logic-free: argument parsing, subcommand routing, and engine construction all belong in `cli.py` and its `cli_*` helper modules.

## Relationships
Imports: `cli` (`main`).
Imported by: nothing — executed by the interpreter via `python -m coordinationhub`.
