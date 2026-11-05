//! Each MCP tool answers, including the additive `who` tool.

use coordinationhub::json::{self, Value};
use coordinationhub::Engine;
use std::path::PathBuf;

fn engine(name: &str) -> (Engine, PathBuf) {
    let dir = std::env::temp_dir().join(format!("chub-surface-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let project = dir.join("proj");
    std::fs::create_dir_all(&project).unwrap();
    let engine = Engine::open(Some(&dir.join("store")), Some(&project), "hub").unwrap();
    (engine, dir)
}

fn call(engine: &mut Engine, tool: &str, args: Value) -> Value {
    engine
        .call(tool, &args)
        .unwrap_or_else(|err| json::obj(vec![("error", json::s(err))]))
}

#[test]
fn every_tool_returns_json() {
    let (mut engine, dir) = engine("all");
    let tools = [
        "register_agent",
        "heartbeat",
        "list_agents",
        "get_agent_relations",
        "acquire_lock",
        "refresh_lock",
        "get_lock_status",
        "list_locks",
        "release_lock",
        "admin_locks",
        "notify_change",
        "get_notifications",
        "get_conflicts",
        "get_contention_hotspots",
        "status",
        "send_message",
        "manage_messages",
        "create_task",
        "create_subtask",
        "assign_task",
        "update_task_status",
        "query_tasks",
        "get_available_tasks",
        "wait_for_task",
        "task_failures",
        "manage_work_intents",
        "manage_dependencies",
        "acquire_coordinator_lease",
        "manage_leases",
        "spawn_subagent",
        "get_pending_spawns",
        "report_subagent_spawned",
        "request_subagent_deregistration",
        "is_subagent_stop_requested",
        "await_subagent_registration",
        "await_subagent_stopped",
        "broadcast",
        "acknowledge_broadcast",
        "wait_for_broadcast_acks",
        "wait_for_handoff",
        "wait_for_locks",
        "await_agent",
        "update_agent_status",
        "get_agent_status",
        "get_agent_tree",
        "get_file_agent_map",
        "scan_project",
        "load_coordination_spec",
        "run_assessment",
        "deregister_agent",
        "who",
    ];
    assert_eq!(tools.len(), 51);
    let _ = call(
        &mut engine,
        "register_agent",
        json::obj(vec![("agent_id", json::s("root"))]),
    );
    let _ = call(
        &mut engine,
        "register_agent",
        json::obj(vec![
            ("agent_id", json::s("child")),
            ("parent_id", json::s("root")),
        ]),
    );
    let _ = call(
        &mut engine,
        "heartbeat",
        json::obj(vec![("agent_id", json::s("root"))]),
    );
    let _ = call(&mut engine, "list_agents", json::obj(vec![]));
    let _ = call(
        &mut engine,
        "get_agent_relations",
        json::obj(vec![
            ("agent_id", json::s("child")),
            ("mode", json::s("siblings")),
        ]),
    );
    let _ = call(
        &mut engine,
        "acquire_lock",
        json::obj(vec![
            ("document_path", json::s("a.txt")),
            ("agent_id", json::s("root")),
        ]),
    );
    let _ = call(
        &mut engine,
        "refresh_lock",
        json::obj(vec![
            ("document_path", json::s("a.txt")),
            ("agent_id", json::s("root")),
        ]),
    );
    let _ = call(
        &mut engine,
        "get_lock_status",
        json::obj(vec![("document_path", json::s("a.txt"))]),
    );
    let _ = call(&mut engine, "list_locks", json::obj(vec![]));
    let _ = call(
        &mut engine,
        "notify_change",
        json::obj(vec![
            ("document_path", json::s("a.txt")),
            ("change_type", json::s("modified")),
            ("agent_id", json::s("root")),
        ]),
    );
    let _ = call(&mut engine, "get_notifications", json::obj(vec![]));
    let _ = call(
        &mut engine,
        "release_lock",
        json::obj(vec![
            ("document_path", json::s("a.txt")),
            ("agent_id", json::s("root")),
        ]),
    );
    let _ = call(
        &mut engine,
        "admin_locks",
        json::obj(vec![("action", json::s("reap_expired"))]),
    );
    let _ = call(&mut engine, "get_conflicts", json::obj(vec![]));
    let _ = call(&mut engine, "get_contention_hotspots", json::obj(vec![]));
    let status = call(&mut engine, "status", json::obj(vec![]));
    assert!(status.get("tools").and_then(Value::as_i64).unwrap() >= 50);
    let _ = call(
        &mut engine,
        "send_message",
        json::obj(vec![
            ("from_agent_id", json::s("root")),
            ("to_agent_id", json::s("child")),
            ("message_type", json::s("note")),
        ]),
    );
    let _ = call(
        &mut engine,
        "manage_messages",
        json::obj(vec![
            ("action", json::s("get")),
            ("agent_id", json::s("child")),
        ]),
    );
    let _ = call(
        &mut engine,
        "create_task",
        json::obj(vec![
            ("task_id", json::s("t")),
            ("parent_agent_id", json::s("root")),
            ("description", json::s("work")),
        ]),
    );
    let _ = call(
        &mut engine,
        "create_subtask",
        json::obj(vec![
            ("task_id", json::s("t.1")),
            ("parent_task_id", json::s("t")),
            ("parent_agent_id", json::s("root")),
            ("description", json::s("part")),
        ]),
    );
    let _ = call(
        &mut engine,
        "assign_task",
        json::obj(vec![
            ("task_id", json::s("t")),
            ("assigned_agent_id", json::s("child")),
        ]),
    );
    let _ = call(
        &mut engine,
        "update_task_status",
        json::obj(vec![
            ("task_id", json::s("t.1")),
            ("status", json::s("completed")),
        ]),
    );
    let _ = call(
        &mut engine,
        "query_tasks",
        json::obj(vec![
            ("query_type", json::s("tree")),
            ("root_task_id", json::s("t")),
        ]),
    );
    let _ = call(&mut engine, "get_available_tasks", json::obj(vec![]));
    let _ = call(
        &mut engine,
        "wait_for_task",
        json::obj(vec![("task_id", json::s("t.1")), ("timeout_s", json::i(0))]),
    );
    let _ = call(
        &mut engine,
        "task_failures",
        json::obj(vec![("action", json::s("list_dead_letter"))]),
    );
    let _ = call(
        &mut engine,
        "manage_work_intents",
        json::obj(vec![
            ("action", json::s("declare")),
            ("agent_id", json::s("root")),
            ("document_path", json::s("a.txt")),
            ("intent", json::s("edit")),
        ]),
    );
    let _ = call(
        &mut engine,
        "manage_dependencies",
        json::obj(vec![
            ("mode", json::s("declare")),
            ("dependent_agent_id", json::s("child")),
            ("depends_on_agent_id", json::s("root")),
        ]),
    );
    let _ = call(
        &mut engine,
        "acquire_coordinator_lease",
        json::obj(vec![("agent_id", json::s("root"))]),
    );
    let _ = call(
        &mut engine,
        "manage_leases",
        json::obj(vec![("action", json::s("leader"))]),
    );
    let _ = call(
        &mut engine,
        "spawn_subagent",
        json::obj(vec![
            ("parent_agent_id", json::s("root")),
            ("subagent_type", json::s("Explore")),
        ]),
    );
    let _ = call(
        &mut engine,
        "get_pending_spawns",
        json::obj(vec![("parent_agent_id", json::s("root"))]),
    );
    let _ = call(
        &mut engine,
        "report_subagent_spawned",
        json::obj(vec![
            ("parent_agent_id", json::s("root")),
            ("subagent_type", json::s("Explore")),
            ("child_agent_id", json::s("child")),
        ]),
    );
    let _ = call(
        &mut engine,
        "is_subagent_stop_requested",
        json::obj(vec![("agent_id", json::s("child"))]),
    );
    let _ = call(
        &mut engine,
        "request_subagent_deregistration",
        json::obj(vec![
            ("parent_agent_id", json::s("root")),
            ("child_agent_id", json::s("child")),
        ]),
    );
    let _ = call(
        &mut engine,
        "await_subagent_registration",
        json::obj(vec![
            ("parent_agent_id", json::s("root")),
            ("subagent_type", json::s("Explore")),
            ("timeout", json::i(0)),
        ]),
    );
    let _ = call(
        &mut engine,
        "await_subagent_stopped",
        json::obj(vec![
            ("child_agent_id", json::s("missing")),
            ("timeout", json::i(0)),
        ]),
    );
    let _ = call(
        &mut engine,
        "broadcast",
        json::obj(vec![
            ("agent_id", json::s("child")),
            ("require_ack", json::b(true)),
            ("message", json::s("go")),
        ]),
    );
    let _ = call(
        &mut engine,
        "acknowledge_broadcast",
        json::obj(vec![
            ("broadcast_id", json::i(1)),
            ("agent_id", json::s("root")),
        ]),
    );
    let _ = call(
        &mut engine,
        "wait_for_broadcast_acks",
        json::obj(vec![
            ("broadcast_id", json::i(1)),
            ("timeout_s", json::i(0)),
        ]),
    );
    let _ = call(
        &mut engine,
        "broadcast",
        json::obj(vec![
            ("agent_id", json::s("root")),
            ("handoff_targets", json::arr(vec![json::s("child")])),
        ]),
    );
    let _ = call(
        &mut engine,
        "wait_for_handoff",
        json::obj(vec![
            ("handoff_id", json::i(1)),
            ("mode", json::s("status")),
        ]),
    );
    let _ = call(
        &mut engine,
        "wait_for_locks",
        json::obj(vec![
            ("agent_id", json::s("root")),
            ("document_paths", json::arr(vec![json::s("a.txt")])),
            ("timeout_s", json::i(0)),
        ]),
    );
    let _ = call(
        &mut engine,
        "await_agent",
        json::obj(vec![
            ("agent_id", json::s("child")),
            ("timeout_s", json::i(0)),
        ]),
    );
    let _ = call(
        &mut engine,
        "update_agent_status",
        json::obj(vec![
            ("agent_id", json::s("root")),
            ("current_task", json::s("port")),
        ]),
    );
    let _ = call(
        &mut engine,
        "get_agent_status",
        json::obj(vec![("agent_id", json::s("root"))]),
    );
    let _ = call(
        &mut engine,
        "get_agent_tree",
        json::obj(vec![("agent_id", json::s("root"))]),
    );
    let _ = call(&mut engine, "get_file_agent_map", json::obj(vec![]));
    let _ = call(&mut engine, "scan_project", json::obj(vec![]));
    let missing = call(
        &mut engine,
        "load_coordination_spec",
        json::obj(vec![("path", json::s("no-such.json"))]),
    );
    assert_eq!(missing.get("loaded").and_then(Value::as_bool), Some(false));
    let _ = call(
        &mut engine,
        "run_assessment",
        json::obj(vec![("format", json::s("json"))]),
    );
    let claimed = call(
        &mut engine,
        "who",
        json::obj(vec![
            ("action", json::s("claim")),
            ("agent_id", json::s("root")),
            ("area", json::s("parser")),
        ]),
    );
    assert_eq!(claimed.get("claimed").and_then(Value::as_bool), Some(true));
    let blocked = call(
        &mut engine,
        "who",
        json::obj(vec![
            ("action", json::s("claim")),
            ("agent_id", json::s("child")),
            ("area", json::s("parser")),
        ]),
    );
    assert_eq!(blocked.get("claimed").and_then(Value::as_bool), Some(false));
    let picture = call(
        &mut engine,
        "who",
        json::obj(vec![("action", json::s("status"))]),
    );
    assert!(
        picture
            .get("claims")
            .and_then(Value::as_array)
            .unwrap()
            .len()
            >= 1
    );
    let handed = call(
        &mut engine,
        "who",
        json::obj(vec![
            ("action", json::s("handoff")),
            ("agent_id", json::s("root")),
            ("area", json::s("parser")),
            ("to_agent_id", json::s("child")),
        ]),
    );
    assert_eq!(
        handed.get("handed_off").and_then(Value::as_bool),
        Some(true)
    );
    let _ = call(
        &mut engine,
        "who",
        json::obj(vec![
            ("action", json::s("release")),
            ("agent_id", json::s("child")),
            ("area", json::s("parser")),
        ]),
    );
    let _ = call(
        &mut engine,
        "deregister_agent",
        json::obj(vec![("agent_id", json::s("root"))]),
    );
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn manifest_declares_no_crates() {
    let text = include_str!("../Cargo.toml").replace("\r\n", "\n");
    assert!(text.contains("[dependencies]\n"));
    assert!(!text.lines().any(|line| line.starts_with("serde")
        || line.starts_with("tokio")
        || line.starts_with("clap")));
    let deps = text
        .split("[dependencies]")
        .nth(1)
        .unwrap()
        .split("[dev-dependencies]")
        .next()
        .unwrap();
    assert!(deps.trim().is_empty(), "{deps}");
    let dev = text
        .split("[dev-dependencies]")
        .nth(1)
        .unwrap()
        .split("[[bin]]")
        .next()
        .unwrap();
    assert!(dev.trim().is_empty(), "{dev}");
}
