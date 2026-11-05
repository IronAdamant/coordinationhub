//! Behaviour the frozen-clock harness could not see.
//! Inputs are fixed. Do not edit them to make a failure pass.

use coordinationhub::json::{self, Value};
use coordinationhub::Engine;
use std::path::PathBuf;
use std::time::Duration;

fn engine(name: &str) -> (Engine, PathBuf) {
    let dir = std::env::temp_dir().join(format!(
        "chub-behavior-{name}-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let project = dir.join("proj");
    std::fs::create_dir_all(&project).unwrap();
    let opened = Engine::open(Some(&dir.join("store")), Some(&project), "hub").unwrap();
    (opened, dir)
}

fn call(engine: &mut Engine, tool: &str, args: Value) -> Result<Value, String> {
    engine.call(tool, &args)
}

fn obj(pairs: Vec<(&str, Value)>) -> Value {
    json::obj(pairs)
}

#[test]
fn utf8_agent_id_round_trips() {
    let parsed = json::parse(r#"{"agent_id":"agent-ü-東京"}"#).unwrap();
    assert_eq!(
        parsed.get("agent_id").and_then(Value::as_str),
        Some("agent-ü-東京")
    );
    let (mut engine, _dir) = engine("utf8");
    let value = call(
        &mut engine,
        "register_agent",
        obj(vec![("agent_id", json::s("agent-ü-東京"))]),
    )
    .unwrap();
    assert_eq!(
        value.get("agent_id").and_then(Value::as_str),
        Some("agent-ü-東京")
    );
}

#[test]
fn two_processes_share_one_journal() {
    let (mut first, dir) = engine("share");
    let store = dir.join("store");
    let project = dir.join("proj");
    call(
        &mut first,
        "register_agent",
        obj(vec![("agent_id", json::s("from-first"))]),
    )
    .unwrap();
    let mut second = Engine::open(Some(&store), Some(&project), "hub").unwrap();
    let listed = call(
        &mut second,
        "list_agents",
        obj(vec![("active_only", json::b(false))]),
    )
    .unwrap();
    let text = json::dumps(&listed);
    assert!(text.contains("from-first"), "{text}");
    call(
        &mut second,
        "register_agent",
        obj(vec![("agent_id", json::s("from-second"))]),
    )
    .unwrap();
    let again = call(
        &mut first,
        "list_agents",
        obj(vec![("active_only", json::b(false))]),
    )
    .unwrap();
    let text = json::dumps(&again);
    assert!(text.contains("from-second"), "{text}");
}

#[test]
fn partial_handoff_cannot_complete() {
    let (mut engine, _dir) = engine("handoff");
    call(
        &mut engine,
        "register_agent",
        obj(vec![("agent_id", json::s("from"))]),
    )
    .unwrap();
    call(
        &mut engine,
        "register_agent",
        obj(vec![("agent_id", json::s("to-a"))]),
    )
    .unwrap();
    call(
        &mut engine,
        "register_agent",
        obj(vec![("agent_id", json::s("to-b"))]),
    )
    .unwrap();
    let recorded = call(
        &mut engine,
        "broadcast",
        obj(vec![
            ("agent_id", json::s("from")),
            (
                "handoff_targets",
                json::arr(vec![json::s("to-a"), json::s("to-b")]),
            ),
            ("require_ack", json::b(true)),
        ]),
    )
    .unwrap();
    let handoff_id = recorded
        .get("handoff_id")
        .and_then(Value::as_i64)
        .expect("handoff id");
    call(
        &mut engine,
        "wait_for_handoff",
        obj(vec![
            ("handoff_id", json::i(handoff_id)),
            ("agent_id", json::s("to-a")),
            ("mode", json::s("ack")),
        ]),
    )
    .unwrap();
    let completed = call(
        &mut engine,
        "wait_for_handoff",
        obj(vec![
            ("handoff_id", json::i(handoff_id)),
            ("mode", json::s("complete")),
        ]),
    )
    .unwrap();
    assert_eq!(
        completed.get("completed").and_then(Value::as_bool),
        Some(false)
    );
    assert_eq!(
        completed.get("reason").and_then(Value::as_str),
        Some("illegal_transition_from_partially_acknowledged")
    );
    let status = call(
        &mut engine,
        "wait_for_handoff",
        obj(vec![
            ("handoff_id", json::i(handoff_id)),
            ("mode", json::s("status")),
            ("timeout_s", json::f(0.0)),
        ]),
    )
    .unwrap();
    assert!(status.get("to_agents").and_then(Value::as_array).is_some());
    assert_eq!(
        status.get("status").and_then(Value::as_str),
        Some("partially_acknowledged")
    );
    let missing = call(
        &mut engine,
        "wait_for_handoff",
        obj(vec![
            ("handoff_id", json::i(99999)),
            ("mode", json::s("status")),
            ("timeout_s", json::f(0.0)),
        ]),
    )
    .unwrap();
    assert_eq!(
        missing.get("error").and_then(Value::as_str),
        Some("Handoff 99999 not found")
    );
}

#[test]
fn available_tasks_skip_unassigned_when_agent_is_set() {
    let (mut engine, _dir) = engine("avail");
    call(
        &mut engine,
        "register_agent",
        obj(vec![("agent_id", json::s("worker"))]),
    )
    .unwrap();
    call(
        &mut engine,
        "create_task",
        obj(vec![
            ("task_id", json::s("free")),
            ("parent_agent_id", json::s("worker")),
            ("description", json::s("orphan-note")),
        ]),
    )
    .unwrap();
    call(
        &mut engine,
        "create_task",
        obj(vec![
            ("task_id", json::s("mine")),
            ("parent_agent_id", json::s("worker")),
            ("description", json::s("mine")),
        ]),
    )
    .unwrap();
    call(
        &mut engine,
        "assign_task",
        obj(vec![
            ("task_id", json::s("mine")),
            ("assigned_agent_id", json::s("worker")),
        ]),
    )
    .unwrap();
    let listed = call(
        &mut engine,
        "get_available_tasks",
        obj(vec![("agent_id", json::s("worker"))]),
    )
    .unwrap();
    let ids: Vec<&str> = listed
        .get("tasks")
        .and_then(Value::as_array)
        .unwrap()
        .iter()
        .filter_map(|row| row.get("id").and_then(Value::as_str))
        .collect();
    assert_eq!(ids, vec!["mine"]);
}

#[test]
fn query_tasks_orders_by_priority_then_age() {
    let (mut engine, _dir) = engine("order");
    call(
        &mut engine,
        "register_agent",
        obj(vec![("agent_id", json::s("owner"))]),
    )
    .unwrap();
    for (id, priority) in [("low", 1), ("high", 9), ("mid", 5)] {
        call(
            &mut engine,
            "create_task",
            obj(vec![
                ("task_id", json::s(id)),
                ("parent_agent_id", json::s("owner")),
                ("description", json::s(id)),
                ("priority", json::i(priority)),
            ]),
        )
        .unwrap();
        std::thread::sleep(Duration::from_millis(5));
    }
    let listed = call(
        &mut engine,
        "query_tasks",
        obj(vec![
            ("query_type", json::s("child")),
            ("parent_agent_id", json::s("owner")),
        ]),
    )
    .unwrap();
    let ids: Vec<&str> = listed
        .get("tasks")
        .and_then(Value::as_array)
        .unwrap()
        .iter()
        .filter_map(|row| row.get("id").and_then(Value::as_str))
        .collect();
    assert_eq!(ids, vec!["high", "mid", "low"]);
}

#[test]
fn notification_limit_minus_one_returns_every_row() {
    let (mut engine, _dir) = engine("notes");
    call(
        &mut engine,
        "register_agent",
        obj(vec![("agent_id", json::s("writer"))]),
    )
    .unwrap();
    for name in ["a", "b", "c"] {
        call(
            &mut engine,
            "notify_change",
            obj(vec![
                ("document_path", json::s(name)),
                ("change_type", json::s("modified")),
                ("agent_id", json::s("writer")),
            ]),
        )
        .unwrap();
    }
    let listed = call(
        &mut engine,
        "get_notifications",
        obj(vec![("limit", json::i(-1))]),
    )
    .unwrap();
    let count = listed
        .get("notifications")
        .and_then(Value::as_array)
        .unwrap()
        .len();
    assert!(count >= 3, "{count}");
}

#[test]
fn who_rejects_overlap_unregistered_and_bad_ttl() {
    let (mut engine, _dir) = engine("who");
    call(
        &mut engine,
        "register_agent",
        obj(vec![("agent_id", json::s("owner"))]),
    )
    .unwrap();
    call(
        &mut engine,
        "register_agent",
        obj(vec![("agent_id", json::s("other"))]),
    )
    .unwrap();
    let claimed = call(
        &mut engine,
        "who",
        obj(vec![
            ("action", json::s("claim")),
            ("agent_id", json::s("owner")),
            ("area", json::s("cedar")),
        ]),
    )
    .unwrap();
    assert_eq!(claimed.get("claimed").and_then(Value::as_bool), Some(true));
    let overlap = call(
        &mut engine,
        "who",
        obj(vec![
            ("action", json::s("claim")),
            ("agent_id", json::s("other")),
            ("area", json::s("cedar/ring.rs")),
        ]),
    )
    .unwrap();
    assert_eq!(overlap.get("claimed").and_then(Value::as_bool), Some(false));
    let ghost = call(
        &mut engine,
        "who",
        obj(vec![
            ("action", json::s("claim")),
            ("agent_id", json::s("ghost-agent")),
            ("area", json::s("notes-bin")),
        ]),
    )
    .unwrap();
    assert_eq!(ghost.get("claimed").and_then(Value::as_bool), Some(false));
    let bad = call(
        &mut engine,
        "who",
        obj(vec![
            ("action", json::s("claim")),
            ("agent_id", json::s("owner")),
            ("area", json::s("other-area")),
            ("ttl", json::i(-5)),
        ]),
    );
    let err = bad.expect_err("negative ttl");
    assert!(err.contains("Input validation error"), "{err}");
    let text = call(
        &mut engine,
        "who",
        obj(vec![
            ("action", json::s("claim")),
            ("agent_id", json::s("owner")),
            ("area", json::s("ttl-word")),
            ("ttl", json::s("not-a-number")),
        ]),
    );
    let err = text.expect_err("string ttl");
    assert!(err.contains("number"), "{err}");
}

#[test]
fn spawn_report_checks_caller_and_registration_wait_sees_it() {
    let (mut engine, _dir) = engine("spawn");
    call(
        &mut engine,
        "register_agent",
        obj(vec![("agent_id", json::s("parent"))]),
    )
    .unwrap();
    call(
        &mut engine,
        "register_agent",
        obj(vec![("agent_id", json::s("child"))]),
    )
    .unwrap();
    call(
        &mut engine,
        "spawn_subagent",
        obj(vec![
            ("parent_agent_id", json::s("parent")),
            ("subagent_type", json::s("Explore")),
        ]),
    )
    .unwrap();
    let mismatch = call(
        &mut engine,
        "report_subagent_spawned",
        obj(vec![
            ("parent_agent_id", json::s("parent")),
            ("child_agent_id", json::s("child")),
            ("subagent_type", json::s("Explore")),
            ("caller_agent_id", json::s("outsider")),
        ]),
    )
    .unwrap();
    assert_eq!(
        mismatch.get("reason").and_then(Value::as_str),
        Some("caller_mismatch")
    );
    call(
        &mut engine,
        "report_subagent_spawned",
        obj(vec![
            ("parent_agent_id", json::s("parent")),
            ("child_agent_id", json::s("child")),
            ("subagent_type", json::s("Explore")),
            ("caller_agent_id", json::s("parent")),
        ]),
    )
    .unwrap();
    let waited = call(
        &mut engine,
        "await_subagent_registration",
        obj(vec![
            ("parent_agent_id", json::s("parent")),
            ("subagent_type", json::s("Explore")),
            ("timeout", json::f(0.2)),
        ]),
    )
    .unwrap();
    assert_eq!(
        waited.get("registered").and_then(Value::as_bool),
        Some(true)
    );
    assert!(waited.get("spawn").is_some());
}

#[test]
fn wait_for_task_sleeps_until_the_task_finishes() {
    let (mut watcher, dir) = engine("wait");
    let store = dir.join("store");
    let project = dir.join("proj");
    call(
        &mut watcher,
        "register_agent",
        obj(vec![("agent_id", json::s("owner"))]),
    )
    .unwrap();
    call(
        &mut watcher,
        "create_task",
        obj(vec![
            ("task_id", json::s("job")),
            ("parent_agent_id", json::s("owner")),
            ("description", json::s("job")),
        ]),
    )
    .unwrap();
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(200));
        let mut writer = Engine::open(Some(&store), Some(&project), "hub").unwrap();
        let _ = call(
            &mut writer,
            "update_task_status",
            obj(vec![
                ("task_id", json::s("job")),
                ("status", json::s("completed")),
            ]),
        );
    });
    let started = std::time::Instant::now();
    let result = call(
        &mut watcher,
        "wait_for_task",
        obj(vec![
            ("task_id", json::s("job")),
            ("timeout_s", json::f(2.0)),
            ("poll_interval_s", json::f(0.05)),
        ]),
    )
    .unwrap();
    assert_eq!(result.get("timed_out").and_then(Value::as_bool), Some(false));
    let waited = result.get("waited_s").and_then(Value::as_f64).unwrap_or(0.0);
    assert!(waited > 0.05, "{waited}");
    assert!(started.elapsed() >= Duration::from_millis(150));
}

#[test]
fn hook_session_start_registers_an_agent() {
    let (mut engine, _dir) = engine("hook");
    let event = json::parse(
        r#"{"hook_event_name":"SessionStart","session_id":"sess-1","cwd":"."}"#,
    )
    .unwrap();
    assert!(coordinationhub::hooks::handle(&mut engine, &event).is_none());
    let listed = call(
        &mut engine,
        "list_agents",
        obj(vec![("active_only", json::b(true))]),
    )
    .unwrap();
    let text = json::dumps(&listed);
    assert!(text.contains("hub.cc.sess-1"), "{text}");
}

#[test]
fn duplicate_task_does_not_invent_sqlite_text() {
    let (mut engine, _dir) = engine("dup");
    call(
        &mut engine,
        "register_agent",
        obj(vec![("agent_id", json::s("owner"))]),
    )
    .unwrap();
    call(
        &mut engine,
        "create_task",
        obj(vec![
            ("task_id", json::s("same")),
            ("parent_agent_id", json::s("owner")),
            ("description", json::s("once")),
        ]),
    )
    .unwrap();
    let err = call(
        &mut engine,
        "create_task",
        obj(vec![
            ("task_id", json::s("same")),
            ("parent_agent_id", json::s("owner")),
            ("description", json::s("twice")),
        ]),
    )
    .expect_err("duplicate");
    assert!(!err.contains("UNIQUE constraint"), "{err}");
    assert!(err.contains("already exists"), "{err}");
}
