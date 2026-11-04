//! CLI commands that map onto an engine tool.

use crate::json::{self, Value};

pub fn extra(
    command: &str,
    pos: &[String],
    flag: impl Fn(&str) -> Option<String>,
    all: impl Fn(&str) -> Vec<String>,
    has: impl Fn(&str) -> bool,
) -> Result<Option<(String, Value)>, String> {
    let req = |index: usize, name: &str| -> Result<String, String> {
        pos.get(index)
            .cloned()
            .ok_or_else(|| format!("{name} required"))
    };
    let num = |name: &str, default: f64| -> f64 {
        flag(name).and_then(|v| v.parse().ok()).unwrap_or(default)
    };
    let whole = |name: &str, default: i64| -> i64 {
        flag(name).and_then(|v| v.parse().ok()).unwrap_or(default)
    };
    let pair = match command {
        "agent-relations" => (
            "get_agent_relations",
            json::obj(vec![
                ("agent_id", json::s(req(0, "agent_id")?)),
                ("mode", json::s(flag("mode").unwrap_or_else(|| "lineage".into()))),
            ]),
        ),
        "refresh-lock" => (
            "refresh_lock",
            json::obj(vec![
                ("document_path", json::s(req(0, "document_path")?)),
                ("agent_id", json::s(req(1, "agent_id")?)),
                ("ttl", json::f(num("ttl", 300.0))),
                ("region_start", json::opt_i(flag("region_start").and_then(|v| v.parse().ok()))),
                ("region_end", json::opt_i(flag("region_end").and_then(|v| v.parse().ok()))),
            ]),
        ),
        "admin-locks" => (
            "admin_locks",
            json::obj(vec![
                ("action", json::s(req(0, "action")?)),
                ("agent_id", json::opt_string(&flag("agent_id"))),
                ("grace_seconds", json::f(num("grace_seconds", 0.0))),
                ("timeout", json::f(num("timeout", 600.0))),
            ]),
        ),
        "broadcast" => (
            "broadcast",
            json::obj(vec![
                ("agent_id", json::s(req(0, "agent_id")?)),
                ("document_path", json::opt_string(&flag("document_path"))),
                ("message", json::opt_string(&flag("message"))),
                ("require_ack", json::b(has("require_ack"))),
                (
                    "handoff_targets",
                    json::arr(all("handoff_targets").into_iter().map(json::s).collect()),
                ),
            ]),
        ),
        "acknowledge-broadcast" => (
            "acknowledge_broadcast",
            json::obj(vec![
                ("broadcast_id", json::i(req(0, "broadcast_id")?.parse().unwrap_or(0))),
                ("agent_id", json::s(req(1, "agent_id")?)),
            ]),
        ),
        "wait-for-broadcast-acks" => (
            "wait_for_broadcast_acks",
            json::obj(vec![
                ("broadcast_id", json::i(req(0, "broadcast_id")?.parse().unwrap_or(0))),
                ("timeout_s", json::f(num("timeout", 30.0))),
            ]),
        ),
        "acknowledge-handoff" => (
            "wait_for_handoff",
            json::obj(vec![
                ("handoff_id", json::i(req(0, "handoff_id")?.parse().unwrap_or(0))),
                ("agent_id", json::s(req(1, "agent_id")?)),
                ("mode", json::s("ack")),
            ]),
        ),
        "complete-handoff" => (
            "wait_for_handoff",
            json::obj(vec![
                ("handoff_id", json::i(req(0, "handoff_id")?.parse().unwrap_or(0))),
                ("mode", json::s("complete")),
            ]),
        ),
        "cancel-handoff" => (
            "wait_for_handoff",
            json::obj(vec![
                ("handoff_id", json::i(req(0, "handoff_id")?.parse().unwrap_or(0))),
                ("mode", json::s("cancel")),
            ]),
        ),
        "get-handoffs" => (
            "wait_for_handoff",
            json::obj(vec![
                ("mode", json::s("status")),
                ("handoff_id", json::i(whole("handoff_id", 0))),
                ("timeout_s", json::f(0.0)),
            ]),
        ),
        "wait-for-handoff" => (
            "wait_for_handoff",
            json::obj(vec![
                ("handoff_id", json::i(req(0, "handoff_id")?.parse().unwrap_or(0))),
                ("timeout_s", json::f(num("timeout", 30.0))),
                ("mode", json::s(flag("mode").unwrap_or_else(|| "completion".into()))),
                ("agent_id", json::opt_string(&flag("agent_id"))),
            ]),
        ),
        "wait-for-locks" => (
            "wait_for_locks",
            json::obj(vec![
                ("agent_id", json::s(req(0, "agent_id")?)),
                (
                    "document_paths",
                    json::arr(pos.iter().skip(1).map(|s| json::s(s.clone())).collect()),
                ),
                ("timeout_s", json::f(num("timeout", 30.0))),
            ]),
        ),
        "prune-notifications" => {
            let mut pairs = Vec::new();
            if let Some(age) = flag("max_age_seconds")
                .or_else(|| flag("max_age"))
                .and_then(|v| v.parse::<f64>().ok())
            {
                pairs.push(("prune_max_age_seconds", json::f(age)));
            }
            if let Some(entries) = flag("max_entries").and_then(|v| v.parse::<i64>().ok()) {
                pairs.push(("prune_max_entries", json::i(entries)));
            }
            ("get_notifications", json::obj(pairs))
        }
        "wait-for-notifications" => (
            "get_notifications",
            json::obj(vec![
                ("agent_id", json::s(req(0, "agent_id")?)),
                ("timeout_s", json::f(num("timeout", 30.0))),
                ("poll_interval_s", json::f(num("poll_interval", 2.0))),
                ("exclude_agent", json::opt_string(&flag("exclude_agent"))),
            ]),
        ),
        "contention-hotspots" => (
            "get_contention_hotspots",
            json::obj(vec![("limit", json::i(whole("limit", 10)))]),
        ),
        "load-spec" => (
            "load_coordination_spec",
            json::obj(vec![("path", json::opt_string(&pos.first().cloned()))]),
        ),
        "validate-spec" => (
            "load_coordination_spec",
            json::obj(vec![("path", json::opt_string(&flag("path")))]),
        ),
        "scan-project" => (
            "scan_project",
            json::obj(vec![
                ("worktree_root", json::opt_string(&flag("worktree_root"))),
                (
                    "extensions",
                    {
                        let items = all("extensions");
                        if items.is_empty() {
                            json::null()
                        } else {
                            json::arr(items.into_iter().map(json::s).collect())
                        }
                    },
                ),
            ]),
        ),
        "assess" => (
            "run_assessment",
            json::obj(vec![
                ("suite_path", json::opt_string(&flag("suite"))),
                ("format", json::s(flag("format").unwrap_or_else(|| "markdown".into()))),
                ("graph_agent_id", json::opt_string(&flag("graph_agent_id"))),
            ]),
        ),
        "await-agent" => (
            "await_agent",
            json::obj(vec![
                ("agent_id", json::s(req(0, "agent_id")?)),
                ("timeout_s", json::f(num("timeout", 60.0))),
            ]),
        ),
        "get-messages" => (
            "manage_messages",
            json::obj(vec![
                ("action", json::s("get")),
                ("agent_id", json::s(req(0, "agent_id")?)),
                ("unread_only", json::b(has("unread_only"))),
                ("limit", json::i(whole("limit", 50))),
            ]),
        ),
        "mark-messages-read" => (
            "manage_messages",
            json::obj(vec![
                ("action", json::s("mark_read")),
                ("agent_id", json::s(req(0, "agent_id")?)),
                (
                    "message_ids",
                    json::arr(
                        flag("message_ids")
                            .unwrap_or_default()
                            .split(',')
                            .filter_map(|s| s.parse::<i64>().ok())
                            .map(json::i)
                            .collect(),
                    ),
                ),
            ]),
        ),
        "assign-task" => (
            "assign_task",
            json::obj(vec![
                ("task_id", json::s(req(0, "task_id")?)),
                ("assigned_agent_id", json::s(req(1, "assigned_agent_id")?)),
            ]),
        ),
        "update-task-status" => (
            "update_task_status",
            json::obj(vec![
                ("task_id", json::s(req(0, "task_id")?)),
                ("status", json::s(req(1, "status")?)),
                ("summary", json::opt_string(&flag("summary"))),
                ("blocked_by", json::opt_string(&flag("blocked_by"))),
                ("error", json::opt_string(&flag("error"))),
            ]),
        ),
        "query-tasks" => (
            "query_tasks",
            json::obj(vec![
                ("query_type", json::s(req(0, "query_type")?)),
                ("task_id", json::opt_string(&flag("task_id"))),
                ("parent_agent_id", json::opt_string(&flag("parent_agent_id"))),
                ("assigned_agent_id", json::opt_string(&flag("assigned_agent_id"))),
                ("parent_task_id", json::opt_string(&flag("parent_task_id"))),
                ("root_task_id", json::opt_string(&flag("root_task_id"))),
            ]),
        ),
        "create-subtask" => (
            "create_subtask",
            json::obj(vec![
                ("task_id", json::s(req(0, "task_id")?)),
                ("parent_task_id", json::s(req(1, "parent_task_id")?)),
                ("parent_agent_id", json::s(req(2, "parent_agent_id")?)),
                ("description", json::s(req(3, "description")?)),
                ("priority", json::i(whole("priority", 0))),
            ]),
        ),
        "retry-task" => (
            "task_failures",
            json::obj(vec![
                ("action", json::s("retry")),
                ("task_id", json::s(req(0, "task_id")?)),
            ]),
        ),
        "dead-letter-queue" => (
            "task_failures",
            json::obj(vec![
                ("action", json::s("list_dead_letter")),
                ("limit", json::i(whole("limit", 50))),
            ]),
        ),
        "task-failure-history" => (
            "task_failures",
            json::obj(vec![
                ("action", json::s("history")),
                ("task_id", json::s(req(0, "task_id")?)),
            ]),
        ),
        "wait-for-task" => (
            "wait_for_task",
            json::obj(vec![
                ("task_id", json::s(req(0, "task_id")?)),
                ("timeout_s", json::f(num("timeout", 60.0))),
                ("poll_interval_s", json::f(num("poll_interval", 2.0))),
            ]),
        ),
        "get-available-tasks" => (
            "get_available_tasks",
            json::obj(vec![("agent_id", json::opt_string(&flag("agent_id")))]),
        ),
        "declare-work-intent" => (
            "manage_work_intents",
            json::obj(vec![
                ("action", json::s("declare")),
                ("agent_id", json::s(req(0, "agent_id")?)),
                ("document_path", json::s(req(1, "document_path")?)),
                ("intent", json::s(req(2, "intent")?)),
                ("ttl", json::f(num("ttl", 60.0))),
            ]),
        ),
        "get-work-intents" => (
            "manage_work_intents",
            json::obj(vec![
                ("action", json::s("get")),
                ("agent_id", json::opt_string(&flag("agent_id"))),
            ]),
        ),
        "clear-work-intent" => (
            "manage_work_intents",
            json::obj(vec![
                ("action", json::s("clear")),
                ("agent_id", json::s(req(0, "agent_id")?)),
                ("document_path", json::opt_string(&flag("document_path"))),
            ]),
        ),
        "declare-dependency" => (
            "manage_dependencies",
            json::obj(vec![
                ("mode", json::s("declare")),
                ("dependent_agent_id", json::s(req(0, "dependent_agent_id")?)),
                ("depends_on_agent_id", json::s(req(1, "depends_on_agent_id")?)),
                ("depends_on_task_id", json::opt_string(&flag("depends_on_task_id"))),
                ("condition", json::s(flag("condition").unwrap_or_else(|| "task_completed".into()))),
            ]),
        ),
        "manage-dependencies" => (
            "manage_dependencies",
            json::obj(vec![
                ("mode", json::s(req(0, "mode")?)),
                ("agent_id", json::s(req(1, "agent_id")?)),
            ]),
        ),
        "satisfy-dependency" => (
            "manage_dependencies",
            json::obj(vec![
                ("mode", json::s("satisfy")),
                ("dep_id", json::i(req(0, "dep_id")?.parse().unwrap_or(0))),
            ]),
        ),
        "get-all-dependencies" => (
            "manage_dependencies",
            json::obj(vec![
                ("mode", json::s("list")),
                ("dependent_agent_id", json::opt_string(&flag("dependent_agent_id"))),
            ]),
        ),
        "acquire-coordinator-lease" | "claim-leadership" => (
            if command == "claim-leadership" { "manage_leases" } else { "acquire_coordinator_lease" },
            json::obj(vec![
                ("action", json::s(if command == "claim-leadership" { "claim" } else { "acquire" })),
                ("agent_id", json::s(req(0, "agent_id")?)),
                ("ttl", json::f(num("ttl", 10.0))),
            ]),
        ),
        "refresh-coordinator-lease" => (
            "manage_leases",
            json::obj(vec![
                ("action", json::s("refresh")),
                ("agent_id", json::s(req(0, "agent_id")?)),
            ]),
        ),
        "release-coordinator-lease" => (
            "manage_leases",
            json::obj(vec![
                ("action", json::s("release")),
                ("agent_id", json::s(req(0, "agent_id")?)),
            ]),
        ),
        "get-leader" | "leader-status" | "ha-dashboard" => (
            "manage_leases",
            json::obj(vec![("action", json::s("leader"))]),
        ),
        "spawn-subagent" => (
            "spawn_subagent",
            json::obj(vec![
                ("parent_agent_id", json::s(req(0, "parent_agent_id")?)),
                ("subagent_type", json::s(req(1, "subagent_type")?)),
                ("description", json::opt_string(&flag("description"))),
                ("prompt", json::opt_string(&flag("prompt"))),
                ("source", json::s(flag("source").unwrap_or_else(|| "external".into()))),
            ]),
        ),
        "report-subagent-spawned" => (
            "report_subagent_spawned",
            json::obj(vec![
                ("parent_agent_id", json::s(req(0, "parent_agent_id")?)),
                ("child_agent_id", json::s(req(1, "child_agent_id")?)),
                ("subagent_type", json::opt_string(&flag("subagent_type"))),
                ("source", json::s(flag("source").unwrap_or_else(|| "external".into()))),
                ("caller_agent_id", json::opt_string(&flag("caller_agent_id"))),
            ]),
        ),
        "list-pending-spawns" => (
            "get_pending_spawns",
            json::obj(vec![
                ("parent_agent_id", json::s(req(0, "parent_agent_id")?)),
                ("include_consumed", json::b(has("all") || has("include_consumed"))),
            ]),
        ),
        "request-subagent-deregistration" => (
            "request_subagent_deregistration",
            json::obj(vec![
                ("parent_agent_id", json::s(req(0, "parent_agent_id")?)),
                ("child_agent_id", json::s(req(1, "child_agent_id")?)),
            ]),
        ),
        "await-subagent-stopped" => (
            "await_subagent_stopped",
            json::obj(vec![
                ("child_agent_id", json::s(req(0, "child_agent_id")?)),
                ("timeout", json::f(num("timeout", 30.0))),
            ]),
        ),
        _ => return Ok(None),
    };
    Ok(Some((pair.0.to_string(), pair.1)))
}
