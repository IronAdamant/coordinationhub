//! Checks for the third review. Inputs are not taken from the reports.

use coordinationhub::hooks;
use coordinationhub::json::{self, Value};
use coordinationhub::Engine;
use std::process::Command;
use std::time::Duration;

fn engine(name: &str) -> Engine {
    let dir = std::env::temp_dir().join(format!(
        "chub-r3-{name}-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(dir.join("proj")).unwrap();
    Engine::open(Some(&dir.join("store")), Some(&dir.join("proj")), "hub").unwrap()
}

fn call(engine: &mut Engine, tool: &str, args: Value) -> Value {
    engine.call(tool, &args).unwrap_or_else(|err| json::obj(vec![("error", json::s(err))]))
}

#[test]
fn old_notifications_time_out() {
    let mut engine = engine("wait");
    call(&mut engine, "register_agent", json::obj(vec![("agent_id", json::s("cedar"))]));
    call(
        &mut engine,
        "notify_change",
        json::obj(vec![
            ("document_path", json::s("old-note.md")),
            ("change_type", json::s("modified")),
            ("agent_id", json::s("cedar")),
        ]),
    );
    std::thread::sleep(Duration::from_millis(1100));
    let listed = call(
        &mut engine,
        "get_notifications",
        json::obj(vec![
            ("timeout_s", json::f(0.2)),
            ("poll_interval_s", json::f(0.05)),
        ]),
    );
    assert_eq!(listed.get("timed_out").and_then(Value::as_bool), Some(true));
    assert!(listed
        .get("notifications")
        .and_then(Value::as_array)
        .unwrap()
        .is_empty());
    assert!(listed.get("pruned").is_none());
}

#[test]
fn unknown_graph_filter_scores_zero() {
    let mut engine = engine("score");
    call(&mut engine, "register_agent", json::obj(vec![("agent_id", json::s("pine"))]));
    let scored = call(
        &mut engine,
        "run_assessment",
        json::obj(vec![
            ("format", json::s("json")),
            ("graph_agent_id", json::s("role-pine")),
        ]),
    );
    assert_eq!(scored.get("overall_score").and_then(Value::as_f64), Some(0.0));
    let traces = scored.get("traces").and_then(Value::as_object).unwrap();
    assert!(traces.is_empty());
}

#[test]
fn lease_ttl_is_a_whole_number() {
    let mut engine = engine("lease");
    call(&mut engine, "register_agent", json::obj(vec![("agent_id", json::s("warden"))]));
    let lease = call(
        &mut engine,
        "acquire_coordinator_lease",
        json::obj(vec![
            ("agent_id", json::s("warden")),
            ("ttl", json::i(2)),
        ]),
    );
    assert!(matches!(lease.get("ttl"), Some(Value::Int(2))));
}

#[test]
fn redaction_respects_word_boundaries_and_short_tlds() {
    let mut engine = engine("redact");
    let event = json::parse(
        r#"{"hook_event_name":"UserPromptSubmit","session_id":"pine0001","prompt":"prebearer token999 and box@z.q and sk-zzzzzzzzzzzzzzzzzz- end"}"#,
    )
    .unwrap();
    hooks::handle(&mut engine, &event);
    let status = call(
        &mut engine,
        "get_agent_status",
        json::obj(vec![("agent_id", json::s("hub.cc.pine0001"))]),
    );
    let task = status.get("current_task").and_then(Value::as_str).unwrap_or("");
    assert!(task.contains("prebearer"), "{task}");
    assert!(task.contains("box@z.q"), "{task}");
    assert!(task.contains("[REDACTED_API_KEY]-"), "{task}");
    assert!(!task.contains("zzzzzzzzzzzzzzzzzz"), "{task}");
}

#[test]
fn command_help_lists_that_commands_flags() {
    let bin = env!("CARGO_BIN_EXE_coordinationhub");
    let output = Command::new(bin).args(["release-lock", "--help"]).output().unwrap();
    let text = String::from_utf8_lossy(&output.stdout);
    assert!(text.contains("release-lock"), "{text}");
    assert!(text.contains("--region-start"), "{text}");
    assert!(output.status.success());
}

#[test]
fn doctor_without_hooks_exits_zero() {
    let bin = env!("CARGO_BIN_EXE_coordinationhub");
    let dir = std::env::temp_dir().join(format!("chub-doc-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let output = Command::new(bin)
        .args(["doctor", "--project-root", &dir.to_string_lossy()])
        .env("HOME", &dir)
        .output()
        .unwrap();
    let text = String::from_utf8_lossy(&output.stdout);
    assert!(text.contains("no IDE hook config found"), "{text}");
    assert_eq!(output.status.code(), Some(0));
}
