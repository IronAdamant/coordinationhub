# coordinationhub/paths.py

Path normalization and project-root detection utilities shared by the engine, subsystems, and CLI. Zero internal dependencies.

## Key Functions / Classes
- `detect_project_root(cwd=None)` — walks upward (max 256 levels) looking for a `.git` marker; returns the containing directory or None.
- `normalize_path(path, project_root)` — returns a posix-style path, relativized to `project_root` when the path is inside it, otherwise the resolved absolute form.

## Design Notes
- T7.26: `.git` is a directory in a normal repo but a file in a worktree or submodule (a `gitdir: ...` pointer file) — `detect_project_root` accepts both via explicit `is_dir() or is_file()` rather than `.exists()`, which would also accept broken symlinks.
- `normalize_path` is the canonical document-path shape stored in `document_locks`, `change_notifications`, `work_intent`, and `file_ownership` — every subsystem that takes a `document_path` argument funnels it through here, so lock/intent/ownership rows compare equal regardless of how callers spelled the path.
- Paths outside the project root stay absolute (resolved); there is no error path — normalization always returns a string.

## Relationships
Imports: none from the package.
Imported by: `core.py` (detect_project_root), `work_intent_subsystem.py`, `change_subsystem.py`, `locking_subsystem.py`, `broadcast_subsystem.py` (normalize_path), `cli_setup.py`, `cli_setup_doctor.py`.
