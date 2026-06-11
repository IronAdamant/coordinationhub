# coordinationhub/hooks/stdio_adapter.py

Stdio event adapter: reads one hook-event JSON object from stdin, dispatches to a `StdioHook` (subclass of `BaseHook`), and writes any decision JSON to stdout. Handles SessionStart/End, UserPromptSubmit, PreToolUse (Write|Edit|Agent), PostToolUse (Write/Edit + Stele/Trammel bridges), SubagentStart/Stop.

## Key Functions / Classes
- `StdioHook(BaseHook)` — `IDE_PREFIX = "cc"`; `from_cwd()` honors `IDE_PROJECT_DIR`; static event-extraction helpers `_subagent_type` / `_raw_agent_id`.
- `main()` — entry point (`python -m coordinationhub.hooks.stdio_adapter`); parses stdin, routes on `hook_event_name` + `tool_name`, dumps result to stdout. Graceful no-op on bad/empty input.
- `handle_session_start` / `handle_user_prompt_submit` / `handle_pre_write` / `handle_pre_agent` / `handle_post_write` / `handle_post_stele_index` / `handle_post_trammel_claim` / `handle_subagent_start` / `handle_subagent_stop` / `handle_session_end` — thin wrappers over the corresponding `StdioHook` methods; all accept an optional shared `hook=` (T3.2 back-compat: construct-and-close their own when omitted).
- `_HookRunner` — lazily builds one `StdioHook` shared across all branches of a single `main()` dispatch (T3.2: engine boot cost paid once).
- `_log_error(hook_event, exc)` — writes to `~/.coordinationhub/hook.log` + stderr, truncating the log to ~500 lines past 1 MB; never raises.
- `_save_event_snapshot(event)` — raw-event capture for contract-test fixtures when `COORDINATIONHUB_CAPTURE_EVENTS` is set (T3.14: microsecond + monotonic-counter filename suffix so burst events don't overwrite).
- Module-level helpers `_session_agent_id`, `_subagent_type`, `_resolve_agent_id`, `_subagent_id`, `_get_engine` — kept for tests and external integrations.

## Design Notes
- Fails open: every error path exits 0; `ImportError` is swallowed entirely so the adapter is a no-op if coordinationhub isn't importable.
- `_STELE_INDEX_TOOLS` / `_TRAMMEL_CLAIM_TOOLS` are exact-match frozensets (T3.15) — add new MCP tool aliases there rather than loosening matching (the old substring check matched e.g. `unstele_reindexer`).
- PreToolUse Agent stashes the sub-agent description/prompt (`stash_subagent_description`) so the following SubagentStart can correlate it into `current_task`.
- `_session_agent_id` delegates to `build_session_agent_id` (T2.9 + T6.28 lockstep guarantee).

## Relationships
Imports: `coordinationhub.hooks.base` (`BaseHook`, `build_session_agent_id`).
Imported by: nothing in-package — invoked as a subprocess via the `{python} -m coordinationhub.hooks.stdio_adapter` command template in `cli_setup.py`; imported by `tests/test_hooks.py`, `tests/test_setup.py`, `tests/test_scenario.py`.
