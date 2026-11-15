//! Each MCP tool answers, including the additive `who` tool. Every call
//! checks its reply: a success names what it did, a refusal says why.

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

/// Calls a tool that must succeed and checks the named reply fields.
fn expect(engine: &mut Engine, tool: &str, args: Value, fields: &[(&str, Value)]) -> Value {
    let reply = engine
        .call(tool, &args)
        .unwrap_or_else(|err| panic!("{tool} failed: {err}"));
    for (key, want) in fields {
        assert_eq!(
            reply.get(key),
            Some(want),
            "{tool}: field {key} in {}",
            json::dumps(&reply)
        );
    }
    reply
}

/// Calls a tool that must be refused and returns the error text.
fn refused(engine: &mut Engine, tool: &str, args: Value) -> String {
    match engine.call(tool, &args) {
        Ok(reply) => panic!("{tool} should be refused, got {}", json::dumps(&reply)),
        Err(err) => err,
    }
}

fn len(reply: &Value, key: &str) -> usize {
    reply
        .get(key)
        .and_then(Value::as_array)
        .map(|items| items.len())
        .unwrap_or_else(|| panic!("no list {key} in {}", json::dumps(reply)))
}

fn yes() -> Value {
    json::b(true)
}

fn no() -> Value {
    json::b(false)
}

#[test]
fn every_tool_returns_json() {
    let (mut engine, dir) = engine("all");
    let e = &mut engine;
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
    expect(e, "register_agent", json::obj(vec![("agent_id", json::s("root"))]), &[("agent_id", json::s("root"))]);
    expect(
        e,
        "register_agent",
        json::obj(vec![("agent_id", json::s("child")), ("parent_id", json::s("root"))]),
        &[("agent_id", json::s("child")), ("parent_id", json::s("root"))],
    );
    expect(e, "heartbeat", json::obj(vec![("agent_id", json::s("root"))]), &[("updated", yes())]);
    let listed = expect(e, "list_agents", json::obj(vec![]), &[]);
    assert_eq!(len(&listed, "agents"), 2);
    let relations = expect(
        e,
        "get_agent_relations",
        json::obj(vec![("agent_id", json::s("child")), ("mode", json::s("siblings"))]),
        &[("mode", json::s("siblings"))],
    );
    assert_eq!(len(&relations, "siblings"), 0);
    let file = json::obj(vec![("document_path", json::s("a.txt")), ("agent_id", json::s("root"))]);
    expect(e, "acquire_lock", file.clone(), &[("acquired", yes()), ("locked_by", json::s("root"))]);
    expect(e, "refresh_lock", file.clone(), &[("refreshed", yes())]);
    expect(
        e,
        "get_lock_status",
        json::obj(vec![("document_path", json::s("a.txt"))]),
        &[("locked", yes()), ("locked_by", json::s("root"))],
    );
    expect(e, "list_locks", json::obj(vec![]), &[("count", json::i(1))]);
    expect(
        e,
        "notify_change",
        json::obj(vec![
            ("document_path", json::s("a.txt")),
            ("change_type", json::s("modified")),
            ("agent_id", json::s("root")),
        ]),
        &[("recorded", yes())],
    );
    let notes = expect(e, "get_notifications", json::obj(vec![]), &[]);
    assert!(len(&notes, "notifications") >= 2, "{}", json::dumps(&notes));
    expect(e, "release_lock", file.clone(), &[("released", yes()), ("count", json::i(1))]);
    expect(e, "admin_locks", json::obj(vec![("action", json::s("reap_expired"))]), &[("reaped", json::i(0))]);
    let conflicts = expect(e, "get_conflicts", json::obj(vec![]), &[]);
    assert_eq!(len(&conflicts, "conflicts"), 0);
    expect(e, "get_contention_hotspots", json::obj(vec![]), &[("total", json::i(0))]);
    let status = expect(e, "status", json::obj(vec![]), &[]);
    assert!(status.get("tools").and_then(Value::as_i64).unwrap() >= 50);
    expect(
        e,
        "send_message",
        json::obj(vec![
            ("from_agent_id", json::s("root")),
            ("to_agent_id", json::s("child")),
            ("message_type", json::s("note")),
        ]),
        &[("sent", yes())],
    );
    expect(
        e,
        "manage_messages",
        json::obj(vec![("action", json::s("get")), ("agent_id", json::s("child"))]),
        &[("count", json::i(1))],
    );
    expect(
        e,
        "create_task",
        json::obj(vec![
            ("task_id", json::s("t")),
            ("parent_agent_id", json::s("root")),
            ("description", json::s("work")),
        ]),
        &[("created", yes()), ("task_id", json::s("t"))],
    );
    expect(
        e,
        "create_subtask",
        json::obj(vec![
            ("task_id", json::s("t.1")),
            ("parent_task_id", json::s("t")),
            ("parent_agent_id", json::s("root")),
            ("description", json::s("part")),
        ]),
        &[("created", yes()), ("parent_task_id", json::s("t"))],
    );
    expect(
        e,
        "assign_task",
        json::obj(vec![("task_id", json::s("t")), ("assigned_agent_id", json::s("child"))]),
        &[("assigned", yes())],
    );
    expect(
        e,
        "update_task_status",
        json::obj(vec![("task_id", json::s("t.1")), ("status", json::s("completed"))]),
        &[("updated", yes()), ("status", json::s("completed")), ("prior_status", json::s("pending"))],
    );
    let tree = expect(
        e,
        "query_tasks",
        json::obj(vec![("query_type", json::s("tree")), ("root_task_id", json::s("t"))]),
        &[("id", json::s("t"))],
    );
    assert_eq!(len(&tree, "subtasks"), 1);
    expect(e, "get_available_tasks", json::obj(vec![]), &[("count", json::i(1))]);
    expect(
        e,
        "wait_for_task",
        json::obj(vec![("task_id", json::s("t.1")), ("timeout_s", json::i(0))]),
        &[("status", json::s("completed")), ("timed_out", no())],
    );
    expect(
        e,
        "task_failures",
        json::obj(vec![("action", json::s("list_dead_letter"))]),
        &[("count", json::i(0))],
    );
    expect(
        e,
        "manage_work_intents",
        json::obj(vec![
            ("action", json::s("declare")),
            ("agent_id", json::s("root")),
            ("document_path", json::s("a.txt")),
            ("intent", json::s("edit")),
        ]),
        &[("recorded", yes())],
    );
    expect(
        e,
        "manage_dependencies",
        json::obj(vec![
            ("mode", json::s("declare")),
            ("dependent_agent_id", json::s("child")),
            ("depends_on_agent_id", json::s("root")),
        ]),
        &[("declared", yes())],
    );
    expect(
        e,
        "acquire_coordinator_lease",
        json::obj(vec![("agent_id", json::s("root"))]),
        &[("acquired", yes()), ("holder_id", json::s("root"))],
    );
    // `leader` is not a manage_leases action; the refusal lists the real ones.
    let err = refused(e, "manage_leases", json::obj(vec![("action", json::s("leader"))]));
    assert!(err.contains("'leader' is not one of"), "{err}");
    assert!(err.contains("'get'"), "{err}");
    let leader = expect(
        e,
        "manage_leases",
        json::obj(vec![("action", json::s("get")), ("agent_id", json::s("root"))]),
        &[],
    );
    assert_eq!(
        leader.get("leader").and_then(|l| l.get("holder_id")),
        Some(&json::s("root")),
        "{}",
        json::dumps(&leader)
    );
    expect(
        e,
        "spawn_subagent",
        json::obj(vec![("parent_agent_id", json::s("root")), ("subagent_type", json::s("Explore"))]),
        &[("stashed", yes()), ("spawn_id", json::s("root.Explore.0"))],
    );
    let pending = expect(e, "get_pending_spawns", json::obj(vec![("parent_agent_id", json::s("root"))]), &[]);
    assert_eq!(pending.as_array().map(|items| items.len()), Some(1), "{}", json::dumps(&pending));
    expect(
        e,
        "report_subagent_spawned",
        json::obj(vec![
            ("parent_agent_id", json::s("root")),
            ("subagent_type", json::s("Explore")),
            ("child_agent_id", json::s("child")),
        ]),
        &[("reported", yes()), ("spawn_id", json::s("root.Explore.0"))],
    );
    expect(
        e,
        "is_subagent_stop_requested",
        json::obj(vec![("agent_id", json::s("child"))]),
        &[("stop_requested", no())],
    );
    expect(
        e,
        "request_subagent_deregistration",
        json::obj(vec![("parent_agent_id", json::s("root")), ("child_agent_id", json::s("child"))]),
        &[("requested", yes())],
    );
    expect(
        e,
        "await_subagent_registration",
        json::obj(vec![
            ("parent_agent_id", json::s("root")),
            ("subagent_type", json::s("Explore")),
            ("timeout", json::i(0)),
        ]),
        &[("registered", yes())],
    );
    expect(
        e,
        "await_subagent_stopped",
        json::obj(vec![("child_agent_id", json::s("missing")), ("timeout", json::i(0))]),
        &[("stopped", yes())],
    );
    // An ack broadcast needs a sibling to wait for.
    expect(
        e,
        "register_agent",
        json::obj(vec![("agent_id", json::s("sib")), ("parent_id", json::s("root"))]),
        &[("agent_id", json::s("sib"))],
    );
    let sent = expect(
        e,
        "broadcast",
        json::obj(vec![
            ("agent_id", json::s("child")),
            ("require_ack", yes()),
            ("message", json::s("go")),
        ]),
        &[("pending_acks", json::arr(vec![json::s("sib")]))],
    );
    let broadcast_id = sent.get("broadcast_id").cloned().unwrap();
    expect(
        e,
        "acknowledge_broadcast",
        json::obj(vec![("broadcast_id", broadcast_id.clone()), ("agent_id", json::s("sib"))]),
        &[("acknowledged", yes())],
    );
    expect(
        e,
        "wait_for_broadcast_acks",
        json::obj(vec![("broadcast_id", broadcast_id), ("timeout_s", json::i(0))]),
        &[("timed_out", no()), ("acknowledged_by", json::arr(vec![json::s("sib")]))],
    );
    let handoff = expect(
        e,
        "broadcast",
        json::obj(vec![
            ("agent_id", json::s("root")),
            ("handoff_targets", json::arr(vec![json::s("child")])),
        ]),
        &[("to_agents", json::arr(vec![json::s("child")]))],
    );
    let handoff_id = handoff.get("handoff_id").cloned().unwrap();
    expect(
        e,
        "wait_for_handoff",
        json::obj(vec![("handoff_id", handoff_id), ("mode", json::s("status"))]),
        &[("status", json::s("pending")), ("from_agent_id", json::s("root"))],
    );
    let free = expect(
        e,
        "wait_for_locks",
        json::obj(vec![
            ("agent_id", json::s("root")),
            ("document_paths", json::arr(vec![json::s("a.txt")])),
            ("timeout_s", json::i(0)),
        ]),
        &[("timed_out", json::arr(vec![]))],
    );
    assert_eq!(len(&free, "released"), 1);
    expect(
        e,
        "await_agent",
        json::obj(vec![("agent_id", json::s("child")), ("timeout_s", json::i(0))]),
        &[("awaited", no()), ("status", json::s("timeout"))],
    );
    expect(
        e,
        "update_agent_status",
        json::obj(vec![("agent_id", json::s("root")), ("current_task", json::s("port"))]),
        &[("updated", yes())],
    );
    expect(
        e,
        "get_agent_status",
        json::obj(vec![("agent_id", json::s("root"))]),
        &[("current_task", json::s("port")), ("active_locks", json::arr(vec![]))],
    );
    let tree = expect(e, "get_agent_tree", json::obj(vec![("agent_id", json::s("root"))]), &[]);
    assert_eq!(
        tree.get("root").and_then(|root| root.get("agent_id")),
        Some(&json::s("root")),
        "{}",
        json::dumps(&tree)
    );
    expect(e, "get_file_agent_map", json::obj(vec![]), &[("total", json::i(0))]);
    expect(e, "scan_project", json::obj(vec![]), &[("scanned", json::i(0))]);
    let missing = expect(
        e,
        "load_coordination_spec",
        json::obj(vec![("path", json::s("no-such.json"))]),
        &[("loaded", no())],
    );
    assert!(
        missing.get("error").and_then(Value::as_str).unwrap().contains("no-such.json"),
        "{}",
        json::dumps(&missing)
    );
    expect(
        e,
        "run_assessment",
        json::obj(vec![("format", json::s("json"))]),
        &[("suite_name", json::s("live_session"))],
    );
    let claim = |action: &str, agent: &str| {
        json::obj(vec![
            ("action", json::s(action)),
            ("agent_id", json::s(agent)),
            ("area", json::s("parser")),
        ])
    };
    expect(e, "who", claim("claim", "root"), &[("claimed", yes())]);
    expect(e, "who", claim("claim", "child"), &[("claimed", no())]);
    let picture = expect(e, "who", json::obj(vec![("action", json::s("status"))]), &[]);
    assert!(len(&picture, "claims") >= 1);
    let mut handing = claim("handoff", "root");
    if let Value::Object(pairs) = &mut handing {
        pairs.push(("to_agent_id".into(), json::s("child")));
    }
    expect(e, "who", handing, &[("handed_off", yes()), ("to_agent_id", json::s("child"))]);
    expect(e, "who", claim("release", "child"), &[("released", yes())]);
    expect(
        e,
        "deregister_agent",
        json::obj(vec![("agent_id", json::s("root"))]),
        &[("deregistered", yes()), ("children_orphaned", json::i(2)), ("lease_released", yes())],
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
