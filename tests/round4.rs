//! Checks for the fourth review. Names here are not taken from the reports.

use coordinationhub::json::{self, Value};
use coordinationhub::Engine;
use std::process::Command;

fn engine(name: &str) -> Engine {
    let dir = std::env::temp_dir().join(format!(
        "chub-r4-{name}-{}-{}",
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
    engine
        .call(tool, &args)
        .unwrap_or_else(|err| json::obj(vec![("error", json::s(err))]))
}

fn topics(engine: &Engine) -> Vec<String> {
    engine
        .store
        .state
        .hub_events
        .iter()
        .map(|row| row.topic.clone())
        .collect()
}

#[test]
fn subtask_does_not_emit_task_created() {
    let mut engine = engine("events");
    call(
        &mut engine,
        "register_agent",
        json::obj(vec![("agent_id", json::s("harbor"))]),
    );
    call(
        &mut engine,
        "create_task",
        json::obj(vec![
            ("task_id", json::s("pier")),
            ("parent_agent_id", json::s("harbor")),
            ("description", json::s("moor the skiff")),
        ]),
    );
    let before = topics(&engine).into_iter().filter(|t| t == "task.created").count();
    call(
        &mut engine,
        "create_subtask",
        json::obj(vec![
            ("task_id", json::s("pier-line")),
            ("parent_task_id", json::s("pier")),
            ("parent_agent_id", json::s("harbor")),
            ("description", json::s("coil the line")),
        ]),
    );
    let after = topics(&engine).into_iter().filter(|t| t == "task.created").count();
    assert_eq!(before, 1);
    assert_eq!(after, 1);
}

#[test]
fn lock_release_and_lease_release_are_events() {
    let mut engine = engine("locks");
    call(
        &mut engine,
        "register_agent",
        json::obj(vec![("agent_id", json::s("quay"))]),
    );
    call(
        &mut engine,
        "acquire_lock",
        json::obj(vec![
            ("document_path", json::s("bollard.md")),
            ("agent_id", json::s("quay")),
        ]),
    );
    call(
        &mut engine,
        "release_lock",
        json::obj(vec![
            ("document_path", json::s("bollard.md")),
            ("agent_id", json::s("quay")),
        ]),
    );
    call(
        &mut engine,
        "acquire_coordinator_lease",
        json::obj(vec![("agent_id", json::s("quay")), ("ttl", json::i(4))]),
    );
    call(
        &mut engine,
        "manage_leases",
        json::obj(vec![
            ("action", json::s("release")),
            ("agent_id", json::s("quay")),
        ]),
    );
    let seen = topics(&engine);
    assert!(seen.iter().any(|t| t == "lock.acquired"), "{seen:?}");
    assert!(seen.iter().any(|t| t == "lock.released"), "{seen:?}");
    assert!(seen.iter().any(|t| t == "lease.acquired"), "{seen:?}");
    assert!(seen.iter().any(|t| t == "lease.released"), "{seen:?}");
}

#[test]
fn hotspot_ties_follow_path() {
    let mut engine = engine("hot");
    call(
        &mut engine,
        "register_agent",
        json::obj(vec![("agent_id", json::s("skiff"))]),
    );
    call(
        &mut engine,
        "register_agent",
        json::obj(vec![("agent_id", json::s("yawl"))]),
    );
    for path in ["zeta.md", "alpha.md"] {
        call(
            &mut engine,
            "acquire_lock",
            json::obj(vec![
                ("document_path", json::s(path)),
                ("agent_id", json::s("skiff")),
            ]),
        );
        call(
            &mut engine,
            "acquire_lock",
            json::obj(vec![
                ("document_path", json::s(path)),
                ("agent_id", json::s("yawl")),
                ("force", json::b(true)),
            ]),
        );
    }
    let spots = call(
        &mut engine,
        "get_contention_hotspots",
        json::obj(vec![("limit", json::i(10))]),
    );
    let paths: Vec<String> = spots
        .get("hotspots")
        .and_then(Value::as_array)
        .unwrap()
        .iter()
        .map(|row| row.get("document_path").and_then(Value::as_str).unwrap().to_string())
        .collect();
    assert!(paths[0].ends_with("alpha.md"), "{paths:?}");
    assert!(paths[1].ends_with("zeta.md"), "{paths:?}");
}

#[test]
fn command_help_matches_the_flag_list() {
    let bin = env!("CARGO_BIN_EXE_coordinationhub");
    let output = Command::new(bin).args(["heartbeat", "--help"]).output().unwrap();
    let text = String::from_utf8_lossy(&output.stdout);
    assert!(text.contains("--replica"), "{text}");
    assert!(text.contains("-j, --json"), "{text}");
    assert!(output.status.success());
    let unknown = Command::new(bin)
        .args(["heartbeat", "harbor", "--no-such-flag"])
        .output()
        .unwrap();
    assert_eq!(unknown.status.code(), Some(2));
    let err = String::from_utf8_lossy(&unknown.stderr);
    assert!(err.contains("unrecognized arguments: --no-such-flag"), "{err}");
}

#[test]
fn missing_field_validation_has_no_tool_prefix() {
    let mut engine = engine("valid");
    let err = call(&mut engine, "heartbeat", json::obj(vec![]));
    let text = err.get("error").and_then(Value::as_str).unwrap_or("");
    assert_eq!(text, "Input validation error: 'agent_id' is a required property");
}
