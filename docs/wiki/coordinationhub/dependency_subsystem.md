# coordinationhub/dependency_subsystem.py

Cross-agent dependency declarations and checks: declare that one agent must wait for another (optionally a specific task), check/assert blockers, satisfy, list, and wait. Fourth T6.22 extraction from `core_dependencies.DependencyMixin`; exposed as `engine._dependency`.

## Key Functions / Classes
- `Dependency` — subsystem class; two-dep ctor (`connect_fn`, `publish_event_fn`), same shape as `Lease`.
- `Dependency.declare_dependency(dependent_agent_id, depends_on_agent_id, depends_on_task_id, condition)` — declare; publishes `dependency.declared`.
- `Dependency.manage_dependencies(mode, ...)` — unified dispatch: `declare | check | blockers | assert | satisfy | list | wait`; returns `{"error": ...}` for missing args or unknown modes.
- `Dependency.satisfy_dependency(dep_id)` — mark satisfied; publishes `dependency.satisfied`.
- `Dependency.get_all_dependencies(dependent_agent_id)` — list with count.
- `Dependency.wait_for_dependency(dep_id, timeout_s, poll_interval_s)` — poll-based wait (delegates entirely to the primitive; no event bus involvement).

## Design Notes
- Zero cross-mixin calls and zero `_hybrid_wait` usage confirmed in the coupling audit; only `_connect` and `_publish_event` are injected.
- `manage_dependencies(mode="declare")` duplicates the `declare_dependency` body (including event publish) rather than calling it — keep the two paths in sync when editing.
- `mode="check"` / `"blockers"` are aliases returning `{blocked, unsatisfied}`; `mode="assert"` returns `{can_start, blockers}` for pre-start gating.
- Cross-subsystem note from the module docstring: `TaskMixin.update_task_status` calls `_deps.satisfy_dependencies_for_task(...)` against the *primitive* module directly — a primitive-layer call, not a mixin-to-mixin call, and unaffected by this extraction.
- Waiting is plain polling via the primitive, so no event-storm robustness concerns here.

## Relationships
Imports: `dependencies` (dependency DB primitives)
Imported by: `core.py` (constructed by `CoordinationEngine`)
