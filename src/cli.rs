//! Command line. Flags follow the Python CLI. `-j` prints the tool result as JSON.

use crate::hash::sha256_hex;
use crate::hub::Hub;
use crate::json::{self, Json};
use crate::util::obj;
use std::env;
use std::fs;
use std::path::PathBuf;
use std::time::Duration;

struct Spec {
    name: &'static str,
    tool: &'static str,
    positionals: &'static [&'static str] }

const SPECS: &[Spec] = &[
    Spec { name: "status", tool: "status", positionals: &[] },
    Spec { name: "register", tool: "register_agent", positionals: &["agent_id"] },
    Spec { name: "heartbeat", tool: "heartbeat", positionals: &["agent_id"] },
    Spec { name: "deregister", tool: "deregister_agent", positionals: &["agent_id"] },
    Spec { name: "list-agents", tool: "list_agents", positionals: &[] },
    Spec { name: "agent-relations", tool: "get_agent_relations", positionals: &["agent_id"] },
    Spec { name: "acquire-lock", tool: "acquire_lock", positionals: &["document_path", "agent_id"] },
    Spec { name: "release-lock", tool: "release_lock", positionals: &["document_path", "agent_id"] },
    Spec { name: "refresh-lock", tool: "refresh_lock", positionals: &["document_path", "agent_id"] },
    Spec { name: "lock-status", tool: "get_lock_status", positionals: &["document_path"] },
    Spec { name: "list-locks", tool: "list_locks", positionals: &[] },
    Spec { name: "admin-locks", tool: "admin_locks", positionals: &["action"] },
    Spec { name: "broadcast", tool: "broadcast", positionals: &["agent_id"] },
    Spec { name: "acknowledge-broadcast", tool: "acknowledge_broadcast", positionals: &["broadcast_id", "agent_id"] },
    Spec { name: "wait-for-broadcast-acks", tool: "wait_for_broadcast_acks", positionals: &["broadcast_id"] },
    Spec { name: "acknowledge-handoff", tool: "wait_for_handoff", positionals: &["handoff_id", "agent_id"] },
    Spec { name: "complete-handoff", tool: "wait_for_handoff", positionals: &["handoff_id"] },
    Spec { name: "cancel-handoff", tool: "wait_for_handoff", positionals: &["handoff_id"] },
    Spec { name: "get-handoffs", tool: "wait_for_handoff", positionals: &[] },
    Spec { name: "wait-for-handoff", tool: "wait_for_handoff", positionals: &["handoff_id"] },
    Spec { name: "wait-for-locks", tool: "wait_for_locks", positionals: &["agent_id"] },
    Spec { name: "notify-change", tool: "notify_change", positionals: &["document_path", "change_type", "agent_id"] },
    Spec { name: "get-notifications", tool: "get_notifications", positionals: &[] },
    Spec { name: "prune-notifications", tool: "get_notifications", positionals: &[] },
    Spec { name: "wait-for-notifications", tool: "get_notifications", positionals: &["agent_id"] },
    Spec { name: "get-conflicts", tool: "get_conflicts", positionals: &[] },
    Spec { name: "contention-hotspots", tool: "get_contention_hotspots", positionals: &[] },
    Spec { name: "load-spec", tool: "load_coordination_spec", positionals: &[] },
    Spec { name: "validate-spec", tool: "status", positionals: &[] },
    Spec { name: "scan-project", tool: "scan_project", positionals: &[] },
    Spec { name: "dashboard", tool: "status", positionals: &[] },
    Spec { name: "agent-status", tool: "get_agent_status", positionals: &["agent_id"] },
    Spec { name: "agent-tree", tool: "get_agent_tree", positionals: &[] },
    Spec { name: "assess", tool: "run_assessment", positionals: &[] },
    Spec { name: "await-agent", tool: "await_agent", positionals: &["agent_id"] },
    Spec { name: "send-message", tool: "send_message", positionals: &["from_agent_id", "to_agent_id", "message_type"] },
    Spec { name: "get-messages", tool: "manage_messages", positionals: &["agent_id"] },
    Spec { name: "mark-messages-read", tool: "manage_messages", positionals: &["agent_id"] },
    Spec { name: "create-task", tool: "create_task", positionals: &["task_id", "parent_agent_id", "description"] },
    Spec { name: "assign-task", tool: "assign_task", positionals: &["task_id", "assigned_agent_id"] },
    Spec { name: "update-task-status", tool: "update_task_status", positionals: &["task_id", "status"] },
    Spec { name: "query-tasks", tool: "query_tasks", positionals: &["query_type"] },
    Spec { name: "create-subtask", tool: "create_subtask", positionals: &["task_id", "parent_task_id", "parent_agent_id", "description"] },
    Spec { name: "retry-task", tool: "task_failures", positionals: &["task_id"] },
    Spec { name: "dead-letter-queue", tool: "task_failures", positionals: &[] },
    Spec { name: "task-failure-history", tool: "task_failures", positionals: &["task_id"] },
    Spec { name: "wait-for-task", tool: "wait_for_task", positionals: &["task_id"] },
    Spec { name: "get-available-tasks", tool: "get_available_tasks", positionals: &[] },
    Spec { name: "declare-work-intent", tool: "manage_work_intents", positionals: &["agent_id", "document_path", "intent"] },
    Spec { name: "get-work-intents", tool: "manage_work_intents", positionals: &[] },
    Spec { name: "clear-work-intent", tool: "manage_work_intents", positionals: &["agent_id"] },
    Spec { name: "declare-dependency", tool: "manage_dependencies", positionals: &["dependent_agent_id", "depends_on_agent_id"] },
    Spec { name: "manage-dependencies", tool: "manage_dependencies", positionals: &["mode", "agent_id"] },
    Spec { name: "satisfy-dependency", tool: "manage_dependencies", positionals: &["dep_id"] },
    Spec { name: "get-all-dependencies", tool: "manage_dependencies", positionals: &[] },
    Spec { name: "acquire-coordinator-lease", tool: "acquire_coordinator_lease", positionals: &["agent_id"] },
    Spec { name: "refresh-coordinator-lease", tool: "manage_leases", positionals: &["agent_id"] },
    Spec { name: "release-coordinator-lease", tool: "manage_leases", positionals: &["agent_id"] },
    Spec { name: "get-leader", tool: "manage_leases", positionals: &[] },
    Spec { name: "claim-leadership", tool: "manage_leases", positionals: &["agent_id"] },
    Spec { name: "leader-status", tool: "manage_leases", positionals: &[] },
    Spec { name: "ha-dashboard", tool: "manage_leases", positionals: &[] },
    Spec { name: "spawn-subagent", tool: "spawn_subagent", positionals: &["parent_agent_id", "subagent_type"] },
    Spec { name: "report-subagent-spawned", tool: "report_subagent_spawned", positionals: &["parent_agent_id", "child_agent_id"] },
    Spec { name: "list-pending-spawns", tool: "get_pending_spawns", positionals: &["parent_agent_id"] },
    Spec { name: "cancel-spawn", tool: "get_pending_spawns", positionals: &["spawn_id"] },
    Spec { name: "request-subagent-deregistration", tool: "request_subagent_deregistration", positionals: &["parent_agent_id", "child_agent_id"] },
    Spec { name: "await-subagent-stopped", tool: "await_subagent_stopped", positionals: &["child_agent_id"] },
    Spec { name: "claim-work", tool: "claim_work", positionals: &["agent_id", "area"] },
    Spec { name: "release-work", tool: "release_work", positionals: &["agent_id", "area"] },
    Spec { name: "list-work", tool: "list_work", positionals: &[] },
    Spec { name: "inbox", tool: "inbox", positionals: &["agent_id"] },
    Spec { name: "compact-status", tool: "compact_status", positionals: &[] },
];

pub fn main() -> i32 {
    let mut args: Vec<String> = env::args().skip(1).collect();
    if args.iter().any(|a| a == "--help" || a == "-h") && args.iter().all(|a| a.starts_with('-') || a == "--help" || a == "-h") {
        print_help();
        return 0;
    }
    if args.is_empty() {
        print_help();
        return 0;
    }
    let command = args.remove(0);
    if command == "hook" {
        return match crate::hook::run() {
            Ok(()) => 0,
            Err(e) => {
                eprintln!("Error: {e}");
                1
            }
        };
    }
    let mut storage = None;
    let mut project = None;
    let mut namespace = "hub".to_string();
    let mut json_out = false;
    let mut flags: Vec<(String, Json)> = Vec::new();
    let mut positionals: Vec<String> = Vec::new();
    let mut i = 0;
    while i < args.len() {
        let a = args[i].clone();
        if a == "--storage-dir" {
            storage = args.get(i + 1).cloned().map(PathBuf::from);
            i += 2;
            continue;
        }
        if a == "--project-root" {
            project = args.get(i + 1).cloned().map(PathBuf::from);
            i += 2;
            continue;
        }
        if a == "--namespace" {
            namespace = args.get(i + 1).cloned().unwrap_or_else(|| "hub".into());
            i += 2;
            continue;
        }
        if a == "-j" || a == "--json" {
            json_out = true;
            i += 1;
            continue;
        }
        if a == "--help" || a == "-h" {
            print_help();
            return 0;
        }
        if a.starts_with("--") {
            let key = a.trim_start_matches('-').to_string();
            let next_is_val = args.get(i + 1).map(|s| !s.starts_with('-')).unwrap_or(false);
            if next_is_val {
                let val = args[i + 1].clone();
                flags.push((key, parse_flag_value(&val)));
                i += 2;
            } else {
                flags.push((key, Json::Bool(true)));
                i += 1;
            }
            continue;
        }
        positionals.push(a);
        i += 1;
    }
    if command == "serve" || command == "serve-sse" || command == "auto-start-dashboard" {
        return serve_cmd(&command, storage.as_deref(), project.as_deref(), &namespace, &flags);
    }
    if command == "serve-mcp" {
        let hub = match Hub::open(storage.as_deref(), project.as_deref(), &namespace) {
            Ok(h) => h,
            Err(e) => {
                eprintln!("Error: {e}");
                return 1;
            }
        };
        return match crate::mcp::serve_stdio(&hub) {
            Ok(()) => 0,
            Err(e) => {
                eprintln!("Error: {e}");
                1
            }
        };
    }
    if command == "init" {
        return init_cmd(project.as_deref(), &flags);
    }
    if command == "doctor" {
        return doctor_cmd(json_out);
    }
    if command == "watch" {
        return watch_cmd(storage.as_deref(), project.as_deref(), &namespace, &positionals, &flags);
    }
    let Some(spec) = SPECS.iter().find(|s| s.name == command) else {
        eprintln!("Error: unknown command {command}");
        print_help();
        return 2;
    };
    if positionals.len() < spec.positionals.len() {
        eprintln!(
            "Error: {command} expects {} argument(s)",
            spec.positionals.len()
        );
        return 2;
    }
    let mut pairs: Vec<(String, Json)> = Vec::new();
    for (idx, name) in spec.positionals.iter().enumerate() {
        pairs.push(((*name).to_string(), parse_flag_value(&positionals[idx])));
    }
    if command == "wait-for-locks" {
        let rest: Vec<Json> = positionals.iter().skip(1).cloned().map(Json::String).collect();
        pairs.push(("document_paths".into(), Json::Array(rest)));
    }
    if command == "broadcast" {
        let extra: Vec<Json> = positionals.iter().skip(1).cloned().map(Json::String).collect();
        if !extra.is_empty() {
            pairs.push(("handoff_targets".into(), Json::Array(extra)));
        }
    }
    for (k, v) in flags {
        let key = k.replace('-', "_");
        if key == "all" && command == "list-agents" {
            pairs.push(("include_stale".into(), v));
            continue;
        }
        if key == "all" && command == "list-pending-spawns" {
            pairs.push(("include_consumed".into(), v));
            continue;
        }
        if key == "timeout" {
            let dest = match command.as_str() {
                "wait-for-broadcast-acks" | "wait-for-handoff" | "wait-for-locks" | "await-agent"
                | "wait-for-notifications" | "wait-for-task" | "await-subagent-stopped" => "timeout_s",
                _ => "timeout" };
            pairs.push((dest.into(), v));
            continue;
        }
        if key == "max_age" || key == "max_age_seconds" {
            pairs.push(("max_age_seconds".into(), v));
            continue;
        }
        pairs.push((key, v));
    }
    apply_action_defaults(&command, &mut pairs);
    if command == "send-message" {
        if let Some(pos) = pairs.iter().position(|(k, _)| k == "payload") {
            if let Json::String(s) = &pairs[pos].1 {
                if let Ok(parsed) = json::parse(s) {
                    pairs[pos].1 = parsed;
                }
            }
        }
    }
    if command == "prune-notifications" {
        pairs.push(("prune".into(), Json::Bool(true)));
    }
    let args = Json::Object(pairs);
    let hub = match Hub::open(storage.as_deref(), project.as_deref(), &namespace) {
        Ok(h) => h,
        Err(e) => {
            eprintln!("Error: {e}");
            return 1;
        }
    };
    if command == "validate-spec" {
        let result = hub.transact(|st| crate::call_vis::validate_spec(st));
        emit(json_out, result);
        return 0;
    }
    if command == "dashboard" {
        let data = hub.transact(|st| crate::call_vis::dashboard(st));
        emit(json_out, data);
        return 0;
    }
    if command == "prune-notifications" {
        let result = hub.transact(|st| crate::call_vis::prune_notifs(st, &args));
        emit(json_out, result);
        return 0;
    }
    if command == "cancel-spawn" {
        let result = hub.transact(|st| crate::call_lease::cancel_spawn(st, &args));
        emit(json_out, result);
        return 0;
    }
    if command == "get-handoffs" {
        let result = hub.transact(|st| crate::call_flow::get_handoffs(st, &args));
        emit(json_out, result);
        return 0;
    }
    if command == "get-leader" || command == "leader-status" || command == "ha-dashboard" {
        let result = hub.transact(|st| crate::call_lease::leader_status(st));
        emit(json_out, result);
        return 0;
    }
    match hub.call(spec.tool, &args) {
        Ok(v) => {
            emit(json_out, v);
            0
        }
        Err(e) => {
            if json_out {
                println!("{}", Json::object(vec![("error", Json::String(e.message().into()))]).dump_pretty());
            } else {
                eprintln!("Error: {}", e.message());
            }
            1
        }
    }
}

fn apply_action_defaults(command: &str, pairs: &mut Vec<(String, Json)>) {
    let set = |pairs: &mut Vec<(String, Json)>, key: &str, val: &str| {
        if !pairs.iter().any(|(k, _)| k == key) {
            pairs.push((key.into(), Json::String(val.into())));
        }
    };
    match command {
        "get-messages" => set(pairs, "action", "get"),
        "mark-messages-read" => set(pairs, "action", "mark_read"),
        "declare-work-intent" => set(pairs, "action", "declare"),
        "get-work-intents" => set(pairs, "action", "get"),
        "clear-work-intent" => set(pairs, "action", "clear"),
        "declare-dependency" => set(pairs, "mode", "declare"),
        "satisfy-dependency" => set(pairs, "mode", "satisfy"),
        "get-all-dependencies" => set(pairs, "mode", "list"),
        "refresh-coordinator-lease" => set(pairs, "action", "refresh"),
        "release-coordinator-lease" => set(pairs, "action", "release"),
        "claim-leadership" => set(pairs, "action", "claim"),
        "get-leader" => set(pairs, "action", "get"),
        "retry-task" => set(pairs, "action", "retry"),
        "dead-letter-queue" => set(pairs, "action", "list_dead_letter"),
        "task-failure-history" => set(pairs, "action", "history"),
        "acknowledge-handoff" => set(pairs, "mode", "ack"),
        "complete-handoff" => set(pairs, "mode", "complete"),
        "cancel-handoff" => set(pairs, "mode", "cancel"),
        "get-handoffs" => set(pairs, "mode", "status"),
        _ => {}
    }
}

fn parse_flag_value(val: &str) -> Json {
    if val == "true" {
        return Json::Bool(true);
    }
    if val == "false" {
        return Json::Bool(false);
    }
    if let Ok(n) = val.parse::<i64>() {
        return Json::Int(n);
    }
    if val.contains('.') {
        if let Ok(n) = val.parse::<f64>() {
            return Json::Float(n);
        }
    }
    Json::String(val.to_string())
}

fn emit(json_out: bool, value: Json) {
    if json_out {
        print!("{}", value.dump_pretty());
    } else {
        println!("{}", value.dump_pretty());
    }
}

fn serve_cmd(command: &str, storage: Option<&std::path::Path>, project: Option<&std::path::Path>, namespace: &str, flags: &[(String, Json)]) -> i32 {
    let hub = match Hub::open(storage, project, namespace) {
        Ok(h) => std::sync::Arc::new(h),
        Err(e) => {
            eprintln!("Error: {e}");
            return 1;
        }
    };
    let port = flags.iter().find(|(k, _)| k == "port").and_then(|(_, v)| v.as_i64()).unwrap_or(if command == "serve" { 9877 } else { 9898 }) as u16;
    let host = flags.iter().find(|(k, _)| k == "host").and_then(|(_, v)| v.as_str()).unwrap_or("127.0.0.1").to_string();
    let no_auth = flags.iter().any(|(k, v)| k == "no-auth" && v.as_bool() == Some(true));
    let token = if no_auth {
        Some("disabled".into())
    } else {
        flags.iter().find(|(k, _)| k == "auth-token").and_then(|(_, v)| v.as_str()).map(|s| s.to_string())
    };
    if let Err(e) = crate::http_srv::serve(hub, crate::http_srv::HttpOpts { host, port, auth_token: token }) {
        eprintln!("Error: {e}");
        return 1;
    }
    0
}

fn init_cmd(project: Option<&std::path::Path>, flags: &[(String, Json)]) -> i32 {
    let root = project
        .map(|p| p.to_path_buf())
        .or_else(|| crate::paths::detect_project_root(&env::current_dir().unwrap_or_else(|_| PathBuf::from("."))))
        .unwrap_or_else(|| env::current_dir().unwrap_or_else(|_| PathBuf::from(".")));
    let storage = root.join(".coordinationhub");
    if let Err(e) = fs::create_dir_all(&storage) {
        eprintln!("Error: {e}");
        return 1;
    }
    println!("Storage directory: {}", storage.display());
    if Hub::open(Some(&storage), Some(&root), "hub").is_err() {
        eprintln!("Error: could not open storage");
        return 1;
    }
    println!("Database initialized.");
    let exe = env::current_exe().unwrap_or_else(|_| PathBuf::from("coordinationhub"));
    let cmd = format!("{} hook", exe.display());
    let home = env::var("HOME").or_else(|_| env::var("USERPROFILE")).unwrap_or_else(|_| ".".into());
    let hooks_path = PathBuf::from(home).join(".coordinationhub").join("hooks.json");
    if let Some(parent) = hooks_path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    let events = [
        "SessionStart",
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "SubagentStart",
        "SubagentStop",
        "SessionEnd",
    ];
    let mut blocks = Vec::new();
    for ev in events {
        blocks.push(format!(
            "    \"{ev}\": [{{\"matcher\": \"\", \"hooks\": [{{\"type\": \"command\", \"command\": \"{cmd}\"}}]}}]"
        ));
    }
    let body = format!("{{\n  \"hooks\": {{\n{}\n  }}\n}}\n", blocks.join(",\n"));
    if let Err(e) = fs::write(&hooks_path, body) {
        eprintln!("Error: {e}");
        return 1;
    }
    println!("Hooks written to {}", hooks_path.display());
    if flags.iter().any(|(k, v)| k == "monitor-skill" && v.as_bool() == Some(true)) {
        let skill_dir = hooks_path.parent().unwrap().join("skills").join("coordinationhub-monitor");
        let _ = fs::create_dir_all(&skill_dir);
        let _ = fs::write(skill_dir.join("SKILL.md"), include_str!("../assets/monitor_skill.md"));
        println!("Monitor skill installed.");
    }
    let _ = sha256_hex(b"init");
    0
}

fn doctor_cmd(json_out: bool) -> i32 {
    let exe_ok = env::current_exe().is_ok();
    let home = env::var("HOME").or_else(|_| env::var("USERPROFILE")).unwrap_or_else(|_| ".".into());
    let hooks = PathBuf::from(&home).join(".coordinationhub").join("hooks.json");
    let checks = vec![
        obj(vec![
            ("name", Json::String("binary".into())),
            ("ok", Json::Bool(exe_ok)),
            ("message", Json::String(if exe_ok { "coordinationhub binary is runnable".into() } else { "binary path unavailable".into() })),
        ]),
        obj(vec![
            ("name", Json::String("hooks_config".into())),
            ("ok", Json::Bool(hooks.is_file())),
            ("message", Json::String(if hooks.is_file() { format!("hooks at {}", hooks.display()) } else { "hooks.json missing; run coordinationhub init".into() })),
        ]),
        obj(vec![
            ("name", Json::String("storage_dir".into())),
            ("ok", Json::Bool(true)),
            ("message", Json::String("storage is created on first use".into())),
        ]),
    ];
    let all_ok = checks.iter().all(|c| c.get("ok").and_then(|v| v.as_bool()) == Some(true));
    let result = obj(vec![
        ("checks", Json::Array(checks)),
        ("all_ok", Json::Bool(all_ok)),
    ]);
    emit(json_out, result);
    if all_ok { 0 } else { 1 }
}

fn watch_cmd(storage: Option<&std::path::Path>, project: Option<&std::path::Path>, namespace: &str, positionals: &[String], flags: &[(String, Json)]) -> i32 {
    let hub = match Hub::open(storage, project, namespace) {
        Ok(h) => h,
        Err(e) => {
            eprintln!("Error: {e}");
            return 1;
        }
    };
    let interval = flags.iter().find(|(k, _)| k == "poll-interval" || k == "interval").and_then(|(_, v)| v.as_i64()).unwrap_or(5);
    let agent = positionals.first().cloned();
    loop {
        let mut args = Vec::new();
        if let Some(a) = &agent {
            args.push(("agent_id".into(), Json::String(a.clone())));
        }
        let tree = hub.call("get_agent_tree", &Json::Object(args));
        match tree {
            Ok(v) => println!("{}", v.dump_pretty()),
            Err(e) => eprintln!("Error: {}", e.message()) }
        std::thread::sleep(Duration::from_secs(interval.max(1) as u64));
    }
}

fn print_help() {
    println!("coordinationhub {version} — live coordination for coding agents", version = crate::VERSION);
    println!("Usage: coordinationhub <command> [args] [--storage-dir DIR] [--project-root DIR] [-j]");
    println!("Commands:");
    for spec in SPECS {
        println!("  {}", spec.name);
    }
    println!("  serve  serve-mcp  serve-sse  init  doctor  watch  hook");
    println!("  claim-work  release-work  list-work  inbox  compact-status");
}
