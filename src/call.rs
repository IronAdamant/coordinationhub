//! Tool dispatch. Names and argument keys match the Python `TOOL_DISPATCH` table.

use crate::call_flow::*;
use crate::call_lease::*;
use crate::call_msg::*;
use crate::call_vis::*;
use crate::call_id::*;
use crate::call_lock::*;
use crate::json::Json;
use crate::model::State;
use crate::validate::{self, all_tool_names};
use std::path::Path;

#[derive(Debug)]
pub enum CallError {
    Value(String),
    Type(String),
}

impl CallError {
    pub fn message(&self) -> &str {
        match self {
            CallError::Value(s) | CallError::Type(s) => s,
        }
    }
}

pub fn is_read_only(name: &str) -> bool {
    matches!(
        name,
        "list_agents"
            | "get_agent_relations"
            | "get_lock_status"
            | "list_locks"
            | "get_notifications"
            | "get_conflicts"
            | "get_contention_hotspots"
            | "status"
            | "get_agent_status"
            | "get_file_agent_map"
            | "get_agent_tree"
            | "query_tasks"
            | "get_available_tasks"
            | "get_pending_spawns"
            | "is_subagent_stop_requested"
            | "list_work"
            | "inbox"
            | "compact_status"
    )
}

pub fn unknown_tool(name: &str) -> String {
    let names = all_tool_names();
    let listed = names
        .iter()
        .map(|n| format!("'{n}'"))
        .collect::<Vec<_>>()
        .join(", ");
    format!("Unknown tool: '{name}'. Available: [{listed}]")
}

pub fn call(st: &mut State, root: &Path, name: &str, args: &Json) -> Result<Json, CallError> {
    if !all_tool_names().iter().any(|n| n == name) {
        return Err(CallError::Value(unknown_tool(name)));
    }
    if let Err(e) = validate::validate_tool(name, args) {
        return Err(CallError::Value(e));
    }
    Ok(dispatch(st, root, name, args))
}

fn dispatch(st: &mut State, root: &Path, name: &str, args: &Json) -> Json {
    match name {
        "register_agent" => register(st, root, args),
        "heartbeat" => heartbeat(st, args),
        "deregister_agent" => deregister(st, args),
        "list_agents" => list_agents(st, args),
        "get_agent_relations" => relations(st, args),
        "acquire_lock" => acquire(st, root, args),
        "release_lock" => release(st, root, args),
        "refresh_lock" => refresh(st, root, args),
        "get_lock_status" => lock_status(st, root, args),
        "list_locks" => list_locks(st, args),
        "admin_locks" => admin(st, args),
        "broadcast" => broadcast(st, root, args),
        "acknowledge_broadcast" => ack_broadcast(st, args),
        "wait_for_broadcast_acks" => wait_broadcast(st, args),
        "wait_for_locks" => wait_locks(st, root, args),
        "await_agent" => await_agent(st, args),
        "notify_change" => notify_change(st, root, args),
        "get_notifications" => notifications(st, args),
        "get_conflicts" => conflicts(st, root, args),
        "get_contention_hotspots" => hotspots(st, args),
        "status" => status(st),
        "load_coordination_spec" => load_spec(st, root, args),
        "scan_project" => scan(st, root, args),
        "get_agent_status" => agent_status(st, args),
        "get_file_agent_map" => file_map(st, args),
        "update_agent_status" => update_status(st, args),
        "get_agent_tree" => agent_tree(st, args),
        "run_assessment" => run_assessment(st, root, args),
        "send_message" => send_message(st, args),
        "manage_messages" => manage_messages(st, args),
        "create_task" => create_task(st, args),
        "assign_task" => assign_task(st, args),
        "update_task_status" => update_task(st, args),
        "query_tasks" => query_tasks(st, args),
        "create_subtask" => create_task(st, args),
        "wait_for_task" => wait_task(st, args),
        "get_available_tasks" => available_tasks(st, args),
        "task_failures" => task_failures(st, args),
        "manage_work_intents" => intents(st, root, args),
        "wait_for_handoff" => wait_handoff(st, args),
        "manage_dependencies" => manage_deps(st, args),
        "acquire_coordinator_lease" => acquire_lease(st, args),
        "manage_leases" => manage_leases(st, args),
        "spawn_subagent" => spawn(st, args),
        "report_subagent_spawned" => report_spawn(st, args),
        "get_pending_spawns" => list_spawns(st, args),
        "await_subagent_registration" => await_reg(st, args),
        "request_subagent_deregistration" => request_stop(st, args),
        "is_subagent_stop_requested" => stop_requested(st, args),
        "await_subagent_stopped" => await_stopped(st, args),
        "claim_work" => claim_work(st, args),
        "release_work" => release_work(st, args),
        "list_work" => list_work(st, args),
        "inbox" => inbox(st, args),
        "compact_status" => compact_status(st, args),
        _ => Json::object(vec![(
            "error",
            Json::String(format!("unhandled {name}")),
        )]),
    }
}

fn wait_task(st: &State, args: &Json) -> Json {
    let id = crate::util::arg_string(args, "task_id").unwrap_or_default();
    let timeout = crate::util::arg_f64(args, "timeout_s", 60.0);
    match st.tasks.iter().find(|t| t.id == id) {
        Some(t) if matches!(t.status.as_str(), "completed" | "failed") => {
            crate::util::obj(vec![
                ("task_id", Json::String(id)),
                ("status", Json::String(t.status.clone())),
                ("timed_out", Json::Bool(false)),
                ("waited_s", Json::Float(0.0)),
            ])
        }
        Some(_) => crate::util::obj(vec![
            ("task_id", Json::String(id)),
            ("status", Json::String("timeout".into())),
            ("timed_out", Json::Bool(true)),
            ("timeout_s", Json::Float(timeout)),
        ]),
        None => crate::util::obj(vec![(
            "error",
            Json::String(format!("Task '{id}' not found")),
        )]),
    }
}

pub fn tool_list() -> Json {
    let mut tools = Vec::new();
    if let Json::Object(pairs) = validate::tool_schemas() {
        for (name, schema) in pairs {
            let description = schema
                .get("description")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let parameters = schema
                .get("parameters")
                .cloned()
                .unwrap_or(Json::object(vec![("type", Json::String("object".into()))]));
            tools.push(Json::object(vec![
                ("name", Json::String(name.clone())),
                ("description", Json::String(description)),
                ("parameters", parameters),
            ]));
        }
    }
    for name in validate::phase2_names() {
        tools.push(Json::object(vec![
            ("name", Json::String((*name).into())),
            (
                "description",
                Json::String(phase2_description(name).into()),
            ),
            ("parameters", Json::object(vec![("type", Json::String("object".into()))])),
        ]));
    }
    tools.sort_by(|a, b| {
        let an = a.get("name").and_then(|v| v.as_str()).unwrap_or("");
        let bn = b.get("name").and_then(|v| v.as_str()).unwrap_or("");
        an.cmp(bn)
    });
    Json::object(vec![
        ("tools", Json::Array(tools)),
        ("tools_version", Json::String(validate::tools_version())),
    ])
}

fn phase2_description(name: &str) -> &'static str {
    match name {
        "compact_status" => "One call: who is active, which locks and work claims are held, unread messages, and open tasks.",
        "claim_work" => "Claim an area of work. A live owner's claim blocks others. A stopped or stale owner loses the claim.",
        "release_work" => "Release a work claim you hold.",
        "list_work" => "List work claims and whether each holder is still alive.",
        "inbox" => "Unread messages, broadcasts waiting on you, and handoffs addressed to you.",
        _ => "",
    }
}
