# coordinationhub/schemas/visibility.py

Graph & Visibility MCP tool schemas for CoordinationHub. Pure data declarations (no logic) for coordination-graph loading, file-ownership scanning, agent status/scope, assessments, and the live agent tree.

## Key Functions / Classes
- `TOOL_SCHEMAS_VISIBILITY: dict[str, dict]` — the only symbol; declares seven tools:
  - `load_coordination_spec` — reload coordination_spec.yaml/.json from disk (default: project root); reports whether a graph loaded and its agent list
  - `scan_project` — file-ownership scan of `worktree_root`; upserts every tracked file (.py, .md, .json, .yaml, .txt, .toml by default) into the file_ownership table
  - `get_agent_status` — full status for one agent: current task, graph responsibilities, owned files, lineage, lock state
  - `get_file_agent_map` — file → owning agent + responsibility map (file_ownership joined with agent responsibilities); optional `agent_id` filter
  - `update_agent_status` — set `current_task` and/or `scope` (list of path prefixes); stored in agent_responsibilities
  - `run_assessment` — score a JSON suite (`suite_path`) or, when omitted, synthesize a live session trace from DB state; `format` markdown (default) or json; `scope` 'project' (default, current worktree) or 'all'
  - `get_agent_tree` — hierarchical agent tree with live work status, active locks (type and region), and boundary-crossing warnings; defaults to the oldest active root agent

## Design Notes
- Pure data declarations — no logic; re-exported by `coordinationhub.schemas` (module docstring).
- Scope enforcement invariant (stated in `update_agent_status`): once an agent declares a `scope`, lock acquisitions outside those path prefixes are denied.
- `get_agent_tree` is framed as a shared situational reference — any agent sees the same live state.
- `run_assessment` needs no hand-authored suite for live scoring; results are persisted to SQLite.

## Relationships
Imports: none within the package (only `from __future__ import annotations`).
Imported by: `coordinationhub/schemas/__init__.py` (merged into the aggregated `TOOL_SCHEMAS` and re-exported in `__all__`).
