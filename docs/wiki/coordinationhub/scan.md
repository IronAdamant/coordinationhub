# coordinationhub/scan.py

File ownership scan — walks the worktree, assigns files to agents via nearest-ancestor inheritance (optionally guided by coordination-graph roles), and upserts the `file_ownership` table.

## Key Functions / Classes
- `scan_project_tool(connect, project_root, worktree_root=None, extensions=None, graph=None)` — the scan entry point; assignment precedence: exact `file_ownership` match > nearest ancestor directory owner > graph role match by extension > first-registered active agent ("unassigned" if none). Returns `{scanned, owned[, truncated][, error]}`.
- `store_responsibilities(connect, agent_id, graph_agent_id, role, model, responsibilities)` — upsert into `agent_responsibilities`.
- `_validate_scan_root(...)` — T2.2 guard: worktree_root must be a real non-symlink directory equal to or inside project_root (rejects `/`, `/etc`, `~`).
- `_role_based_agent(graph, path)` — extension→responsibility-keyword heuristic (.py→implement/code, .md/.yaml→document/plan, .json/.toml→config/data).
- Constants: `DEFAULT_SCAN_EXTENSIONS`, `SKIP_PARTS`, `SKIP_GLOBS` (`*.egg-info`), `MAX_SCAN_FILES=50_000`, `MAX_SCAN_DEPTH=20`, `MAX_SCAN_SECONDS=30.0`.

## Design Notes
- T2.2 hardening: manual `os.walk(followlinks=False)` instead of rglob so the scan can skip symlinks (dirs and files), honour the depth budget, and bail on file-count/time ceilings (`truncated: True`); pre-fix an attacker-supplied root was walked indefinitely.
- T6.1: everything the per-file loop needs (ownership map, graph-role→agent map, fallback agent) is prefetched in ONE connection; pre-fix a 10K-file scan burned 10K+ connections via per-file lookups.
- T7.38: directory-owner seeding iterates paths sorted by length descending so first-write-wins is deterministic — a nested file's owner beats a shallow sibling's when filling the ancestor tree.
- T7.37: `owned` counts files assigned to a real agent; "unassigned" fallback rows don't count.
- Hidden components (leading dot) and `SKIP_PARTS`/`SKIP_GLOBS` matches are never descended into; glob matching exists because the old `part in SKIP_PARTS` literal check missed `mypkg.egg-info`.
- Zero internal dependencies on other coordinationhub modules; the DB connection and graph are injected.

## Relationships
Imports: none from the package.
Imported by: `identity_subsystem.py` and `visibility_subsystem.py` (both as `from . import scan as _scan`).
