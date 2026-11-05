//! Fresh inputs, chosen here, for the second verification gaps.

use coordinationhub::hooks;
use coordinationhub::json::{self, Value};
use coordinationhub::Engine;
use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::time::Duration;

fn engine(name: &str) -> (Engine, PathBuf) {
    let dir = std::env::temp_dir().join(format!(
        "chub-fresh-{name}-{}-{}",
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
fn emoji_survives_storage_and_a_second_process() {
    let (mut first, dir) = engine("emoji");
    let agent = "crew-🐙";
    let stored = call(
        &mut first,
        "register_agent",
        obj(vec![("agent_id", json::s(agent))]),
    )
    .unwrap();
    assert_eq!(stored.get("agent_id").and_then(Value::as_str), Some(agent));
    let dumped = json::dumps(&stored);
    assert!(dumped.contains("\\ud83d\\udc19"), "{dumped}");
    let parsed = json::parse(&dumped).unwrap();
    assert_eq!(parsed.get("agent_id").and_then(Value::as_str), Some(agent));
    let mut second = Engine::open(Some(&dir.join("store")), Some(&dir.join("proj")), "hub").unwrap();
    let listed = call(
        &mut second,
        "list_agents",
        obj(vec![("active_only", json::b(false))]),
    )
    .unwrap();
    assert!(json::dumps(&listed).contains("crew-"), "{listed:?}");
    let again = json::parse(&json::dumps(&listed)).unwrap();
    let names: Vec<&str> = again
        .get("agents")
        .and_then(Value::as_array)
        .unwrap()
        .iter()
        .filter_map(|row| row.get("agent_id").and_then(Value::as_str))
        .collect();
    assert!(names.contains(&agent), "{names:?}");
}

#[test]
fn bare_area_blocks_a_file_under_it() {
    let (mut engine, _dir) = engine("area");
    call(&mut engine, "register_agent", obj(vec![("agent_id", json::s("maple"))])).unwrap();
    call(&mut engine, "register_agent", obj(vec![("agent_id", json::s("quartz"))])).unwrap();
    let first = call(
        &mut engine,
        "who",
        obj(vec![
            ("action", json::s("claim")),
            ("agent_id", json::s("maple")),
            ("area", json::s("grove")),
        ]),
    )
    .unwrap();
    assert_eq!(first.get("claimed").and_then(Value::as_bool), Some(true));
    let nested = call(
        &mut engine,
        "who",
        obj(vec![
            ("action", json::s("claim")),
            ("agent_id", json::s("quartz")),
            ("area", json::s("grove/bench.rs")),
        ]),
    )
    .unwrap();
    assert_eq!(nested.get("claimed").and_then(Value::as_bool), Some(false));
    let dotted = call(
        &mut engine,
        "who",
        obj(vec![
            ("action", json::s("claim")),
            ("agent_id", json::s("quartz")),
            ("area", json::s("./grove")),
        ]),
    )
    .unwrap();
    assert_eq!(dotted.get("claimed").and_then(Value::as_bool), Some(false));
}

#[test]
fn available_tasks_come_back_highest_priority_first() {
    let (mut engine, _dir) = engine("prio");
    call(&mut engine, "register_agent", obj(vec![("agent_id", json::s("miller"))])).unwrap();
    for (id, priority) in [("clay", 2_i64), ("glaze", 8), ("ash", 5)] {
        call(
            &mut engine,
            "create_task",
            obj(vec![
                ("task_id", json::s(id)),
                ("parent_agent_id", json::s("miller")),
                ("description", json::s(id)),
                ("priority", json::i(priority)),
            ]),
        )
        .unwrap();
        call(
            &mut engine,
            "assign_task",
            obj(vec![
                ("task_id", json::s(id)),
                ("assigned_agent_id", json::s("miller")),
            ]),
        )
        .unwrap();
    }
    let listed = call(
        &mut engine,
        "get_available_tasks",
        obj(vec![("agent_id", json::s("miller"))]),
    )
    .unwrap();
    let ids: Vec<&str> = listed
        .get("tasks")
        .and_then(Value::as_array)
        .unwrap()
        .iter()
        .filter_map(|row| row.get("id").and_then(Value::as_str))
        .collect();
    assert_eq!(ids, vec!["glaze", "ash", "clay"]);
}

#[test]
fn notification_wait_returns_existing_rows_newest_first() {
    let (mut engine, _dir) = engine("notes");
    call(&mut engine, "register_agent", obj(vec![("agent_id", json::s("scribe"))])).unwrap();
    for name in ["alpha.md", "beta.md", "gamma.md"] {
        call(
            &mut engine,
            "notify_change",
            obj(vec![
                ("document_path", json::s(name)),
                ("change_type", json::s("modified")),
                ("agent_id", json::s("scribe")),
            ]),
        )
        .unwrap();
    }
    let listed = call(
        &mut engine,
        "get_notifications",
        obj(vec![
            ("timeout_s", json::f(0.3)),
            ("poll_interval_s", json::f(0.05)),
        ]),
    )
    .unwrap();
    assert_eq!(listed.get("timed_out").and_then(Value::as_bool), Some(false));
    let paths: Vec<&str> = listed
        .get("notifications")
        .and_then(Value::as_array)
        .unwrap()
        .iter()
        .filter_map(|row| row.get("document_path").and_then(Value::as_str))
        .collect();
    assert!(paths.first().unwrap().contains("gamma.md"), "{paths:?}");
}

#[test]
fn handoff_keeps_the_path_that_was_given() {
    let (mut engine, _dir) = engine("path");
    call(&mut engine, "register_agent", obj(vec![("agent_id", json::s("from"))])).unwrap();
    call(&mut engine, "register_agent", obj(vec![("agent_id", json::s("into"))])).unwrap();
    let recorded = call(
        &mut engine,
        "broadcast",
        obj(vec![
            ("agent_id", json::s("from")),
            ("document_path", json::s("notes/rough.md")),
            (
                "handoff_targets",
                json::arr(vec![json::s("into")]),
            ),
        ]),
    )
    .unwrap();
    let id = recorded.get("handoff_id").and_then(Value::as_i64).unwrap();
    let status = call(
        &mut engine,
        "wait_for_handoff",
        obj(vec![
            ("handoff_id", json::i(id)),
            ("mode", json::s("status")),
            ("timeout_s", json::f(0.0)),
        ]),
    )
    .unwrap();
    assert_eq!(
        status.get("document_path").and_then(Value::as_str),
        Some("notes/rough.md")
    );
}

#[test]
fn redaction_keeps_ordinary_words_and_removes_the_whole_token() {
    let (mut engine, _dir) = engine("redact");
    let event = json::parse(
        r#"{"hook_event_name":"UserPromptSubmit","session_id":"w1x2y3z4","prompt":"risk-free note sk-abcdefghijklmnopqrstuvwxyz and ada@example.com"}"#,
    )
    .unwrap();
    hooks::handle(&mut engine, &event);
    let status = call(
        &mut engine,
        "get_agent_status",
        obj(vec![("agent_id", json::s("hub.cc.w1x2y3z4"))]),
    )
    .unwrap();
    let task = status.get("current_task").and_then(Value::as_str).unwrap_or("");
    assert!(task.contains("risk-free"), "{task}");
    assert!(!task.contains("abcdefghijklmnopqrstuvwxyz"), "{task}");
    assert!(task.contains("[REDACTED_API_KEY]"), "{task}");
    assert!(task.contains("[REDACTED_EMAIL]"), "{task}");
    assert!(!task.contains("ada@example.com"), "{task}");
}

#[test]
fn live_assessment_names_the_trace_and_loads_a_graph() {
    let (mut engine, _dir) = engine("assess");
    call(&mut engine, "register_agent", obj(vec![("agent_id", json::s("lead"))])).unwrap();
    let report = call(
        &mut engine,
        "run_assessment",
        obj(vec![("format", json::s("markdown"))]),
    )
    .unwrap();
    let text = report.get("report").and_then(Value::as_str).unwrap_or("");
    assert!(text.contains("Graph Loaded:** True"), "{text}");
    assert!(text.contains("live_session"), "{text}");
    assert!(text.contains("100.00%") || text.contains("handoff_latency: 100.00%"), "{text}");
}

#[test]
fn lock_conflict_exits_four() {
    let bin = env!("CARGO_BIN_EXE_coordinationhub");
    let dir = std::env::temp_dir().join(format!("chub-cli-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    let store = dir.join("store");
    let project = dir.join("proj");
    std::fs::create_dir_all(&project).unwrap();
    let run = |args: &[&str]| {
        Command::new(bin)
            .args(args)
            .arg("--storage-dir")
            .arg(&store)
            .arg("--project-root")
            .arg(&project)
            .output()
            .unwrap()
    };
    assert!(run(&["register", "holder"]).status.success());
    assert!(run(&["register", "rival"]).status.success());
    assert!(run(&["acquire-lock", "shared.txt", "holder"]).status.success());
    let denied = run(&["acquire-lock", "shared.txt", "rival"]);
    assert_eq!(denied.status.code(), Some(4));
    let help = run(&["--help"]);
    let text = String::from_utf8_lossy(&help.stdout);
    for name in ["validate-spec", "ha-dashboard", "cancel-spawn", "prune-notifications", "watch"] {
        assert!(text.contains(name), "{name} missing from help");
    }
}

#[test]
fn events_require_a_token() {
    let bin = env!("CARGO_BIN_EXE_coordinationhub");
    let dir = std::env::temp_dir().join(format!("chub-http-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let mut child = Command::new(bin)
        .args([
            "serve",
            "--storage-dir",
            &dir.to_string_lossy(),
            "--port",
            "9871",
            "--auth-token",
            "maple-token",
        ])
        .env("COORDINATIONHUB_HOUSEKEEPING", "0")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .unwrap();
    std::thread::sleep(Duration::from_millis(200));
    let mut sock = TcpStream::connect("127.0.0.1:9871").unwrap();
    sock.write_all(b"GET /events HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n").unwrap();
    sock.set_read_timeout(Some(Duration::from_secs(2))).unwrap();
    let mut buf = [0_u8; 400];
    let n = sock.read(&mut buf).unwrap_or(0);
    let text = String::from_utf8_lossy(&buf[..n]);
    assert!(text.contains("401"), "{text}");
    let _ = child.kill();
}
