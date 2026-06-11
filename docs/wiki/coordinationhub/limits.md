# coordinationhub/limits.py

String-length caps for user-supplied fields (T6.14) — truncation at the primitive boundary keeps runaway or malicious payloads out of SQLite, dashboards, events, and log snapshots.

## Key Functions / Classes
- `truncate(value, max_len)` — clips to `max_len` Unicode code points, appending a `... [truncated N->M]` suffix; `None` passes through unchanged.
- Cap constants (env-overridable via `COORDINATIONHUB_MAX_<FIELD>`): `MAX_DESCRIPTION` (10k), `MAX_PROMPT` (100k), `MAX_SUMMARY` (10k), `MAX_ERROR` (10k), `MAX_CURRENT_TASK` (5k), `MAX_INTENT` (1k), `MAX_MESSAGE` (10k).
- `_env_int(name, default)` — tolerant env parsing; malformed values fall back to the default.

## Design Notes
- Caps are read from the environment at import time — changing the env var after the module is imported has no effect.
- Truncation leaves 32 code points of headroom for the suffix so the result still fits within the cap; observers can detect clipping by the suffix.
- Caps are measured in code points, not bytes.
- Zero internal dependencies — primitives import `truncate` directly; the schemas package mirrors the same caps as `maxLength` so dispatch-level validation (T6.11) and primitive-level truncation stay aligned.

## Relationships
Imports: none from the package.
Imported by: `tasks.py`, `pending_tasks.py`, `messages.py`, `work_intent.py`, `spawner.py`, `agent_status.py`, `schemas/tasks.py`, `schemas/intent.py`.
