# coordinationhub/hooks/base.py

IDE-agnostic hook abstraction: engine lifecycle, agent registration/ID resolution, file locking, change notifications, and sub-agent pending-task correlation. IDE-specific adapters subclass `BaseHook` and map native event shapes onto these methods.

## Key Functions / Classes
- `BaseHook` — the hook protocol; constructs and starts a `CoordinationEngine` (fail-open: `_engine` stays `None` on failure), with lifecycle (`on_session_start/end`), locking (`on_pre_write`/`on_post_write`), prompt stamping (`on_user_prompt`), sub-agent handling (`on_subagent_start/stop`, `stash_subagent_description`), and tool bridges (`on_post_index`, `on_task_claim`).
- `build_session_agent_id(ide_prefix, session_id)` — canonical `hub.{ide_prefix}.{sanitized_session}` formatter (T6.28: single shared source so `BaseHook.session_agent_id` and `stdio_adapter._session_agent_id` can't drift).
- `_sanitize_session_id(session_id)` — 12-char DB-safe tag; unsafe input is SHA-256 hashed to a deterministic 12-hex prefix (T2.9: injection/collision defense, session_id becomes part of a DB primary key).
- `_redact_prompt(text)` — strips credential-shaped substrings (API keys, Bearer tokens, GitHub PATs, AWS key IDs, emails, long hex) before prompts land in `agents.current_task` (T2.1: dashboard exposure).
- `_log_hook_error(stage, exc)` — appends to `~/.coordinationhub/hook.log`; never raises.
- `BaseHook.translate_output(response)` — pass-through by default (Claude Code response shape); other-IDE adapters override to reshape (T3.13).

## Design Notes
- Fail-open invariant (T3.1/T3.3/T3.4): hooks must never raise out to the IDE. Engine errors are logged and methods return `None` defaults; `on_pre_write` explicitly allows the write when locking fails.
- `on_pre_write` builds Claude Code-shaped `hookSpecificOutput` dicts with `permissionDecision` allow/deny; deny includes a force-release CLI hint.
- Redaction runs BEFORE the 120-char prompt truncation so secrets past the cutoff can't survive at full length (T2.1).
- Agent dedup is namespaced by `(raw_ide_id, ide_vendor=IDE_PREFIX)` so raw ids from different IDEs don't cross-match (T3.12).
- Working-tree state: uncommitted edits removed the cursor/kimi adapter references; `IDE_PREFIX` defaults to `"ide"` and the only in-repo subclass is `StdioHook` (`"cc"`).
- Sub-agent IDs: `{parent}.{agent_type}.{tool_use_id[:6]}` when a tool_use_id exists, otherwise a sequence number derived from existing agents.

## Relationships
Imports: `coordinationhub.core` (`CoordinationEngine`, lazy in `_create_engine`), `coordinationhub.pending_tasks` (`stash_pending_task` / `consume_pending_task`, lazy).
Imported by: `coordinationhub/hooks/stdio_adapter.py` (only in-package importer); exercised directly by `tests/test_hooks_base.py` and `tests/test_hooks.py`; referenced in `plugins/dashboard/dashboard.py` docstring (T6.7 redaction note).
