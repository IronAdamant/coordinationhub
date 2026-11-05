//! 1.1.0 changes from 0.8.0 behaviour, each listed in MIGRATION.md: claims
//! that cannot be stolen, explicit handoff tools, compact MCP replies, the
//! version flag, and shorter or clearer replies where agents lost time.

mod common;

use common::{tool_text, Hub};
use coordinationhub::json::{self, Value};
use std::process::{Command, Stdio};

fn text(result: &Value) -> String {
    tool_text(result).0
}

fn reply(result: &Value) -> Value {
    json::parse(&text(result)).unwrap_or_else(|err| panic!("{err}: {}", text(result)))
}

fn create(task: &str) -> (&'static str, Value) {
    (
        "create_task",
        json::obj(vec![
            ("task_id", json::s(task)),
            ("parent_agent_id", json::s("planner")),
            ("description", json::s(format!("work on {task}"))),
        ]),
    )
}

fn assign(task: &str, agent: &str) -> (&'static str, Value) {
    ("assign_task", json::obj(vec![("task_id", json::s(task)), ("assigned_agent_id", json::s(agent))]))
}

#[test]
fn assign_task_refuses_a_task_another_agent_holds() {
    let hub = Hub::new("assignrefuse");
    let mut force = assign("t1", "w2");
    if let Value::Object(pairs) = &mut force.1 {
        pairs.push(("force".into(), json::b(true)));
    }
    let replies = hub.mcp(&[
        create("t1"),
        assign("t1", "w1"),
        assign("t1", "w2"),
        assign("t1", "w1"),
        force,
        (
            "query_tasks",
            json::obj(vec![("query_type", json::s("task")), ("task_id", json::s("t1"))]),
        ),
        assign("missing", "w1"),
    ]);
    assert_eq!(reply(&replies[1]).get("assigned"), Some(&json::b(true)));
    let refused = reply(&replies[2]);
    assert_eq!(refused.get("assigned"), Some(&json::b(false)));
    assert_eq!(refused.get("reason").and_then(Value::as_str), Some("already_assigned"));
    assert_eq!(refused.get("assigned_agent_id").and_then(Value::as_str), Some("w1"));
    assert_eq!(refused.get("status").and_then(Value::as_str), Some("pending"));
    let error = refused.get("error").and_then(Value::as_str).unwrap();
    assert!(error.contains("already assigned to w1"), "{error}");
    assert!(error.contains("unassigned_only") && error.contains("force=true"), "{error}");
    // The holder assigning again is not a conflict.
    assert_eq!(reply(&replies[3]).get("assigned"), Some(&json::b(true)));
    // force=true reassigns on purpose.
    assert_eq!(reply(&replies[4]).get("assigned"), Some(&json::b(true)));
    let task = reply(&replies[5]);
    assert_eq!(
        task.get("task").and_then(|t| t.get("assigned_agent_id")).and_then(Value::as_str),
        Some("w2")
    );
    assert_eq!(reply(&replies[6]).get("reason").and_then(Value::as_str), Some("task_not_found"));
}

#[test]
fn cli_assign_task_reports_refusal_and_missing_task() {
    let hub = Hub::new("assigncli");
    hub.run(&["create-task", "t1", "planner", "work"]);
    let first = hub.run(&["assign-task", "t1", "w1"]);
    assert_eq!((first.code, first.stdout.trim()), (0, "Task assigned: t1 \u{2192} w1"));
    let second = hub.run(&["assign-task", "t1", "w2"]);
    assert_eq!(second.code, 4);
    assert!(second.stdout.starts_with("Assignment refused: task t1 is already assigned to w1"), "{}", second.stdout);
    let forced = hub.run(&["assign-task", "t1", "w2", "--force"]);
    assert_eq!(forced.code, 0);
    let missing = hub.run(&["assign-task", "nope", "w1"]);
    assert_eq!((missing.code, missing.stdout.trim()), (3, "Task not found: nope"));
}

/// Many processes claim the same task at once; the journal lock makes the
/// check and the write one step, so exactly one wins and every other process
/// is told who won.
#[test]
fn concurrent_claims_have_exactly_one_winner() {
    let hub = Hub::new("assignrace");
    const RACERS: usize = 16;
    for round in 0..3 {
        let task = format!("race{round}");
        hub.run(&["create-task", &task, "planner", "contested"]);
        let children: Vec<_> = (0..RACERS)
            .map(|index| {
                Command::new(env!("CARGO_BIN_EXE_coordinationhub"))
                    .args(["assign-task", &task, &format!("agent{index}"), "--json", "--storage-dir"])
                    .arg(&hub.store)
                    .arg("--project-root")
                    .arg(&hub.project)
                    .env("COORDINATIONHUB_HOUSEKEEPING", "0")
                    .stdout(Stdio::piped())
                    .stderr(Stdio::piped())
                    .spawn()
                    .unwrap()
            })
            .collect();
        let mut winners = Vec::new();
        let mut named = Vec::new();
        for (index, child) in children.into_iter().enumerate() {
            let out = child.wait_with_output().unwrap();
            let stdout = String::from_utf8_lossy(&out.stdout);
            let value = json::parse(&stdout).unwrap_or_else(|err| panic!("{err}: {stdout}"));
            if value.get("assigned") == Some(&json::b(true)) {
                assert_eq!(out.status.code(), Some(0));
                winners.push(format!("agent{index}"));
            } else {
                assert_eq!(out.status.code(), Some(4), "{stdout}");
                named.push(value.get("assigned_agent_id").and_then(Value::as_str).unwrap().to_string());
            }
        }
        assert_eq!(winners.len(), 1, "round {round}: winners {winners:?}");
        assert_eq!(named.len(), RACERS - 1);
        assert!(named.iter().all(|holder| *holder == winners[0]), "{named:?} vs {winners:?}");
        let stored = hub.json(&["query-tasks", "task", "--task-id", &task]);
        assert_eq!(
            stored.get("task").and_then(|t| t.get("assigned_agent_id")).and_then(Value::as_str),
            Some(winners[0].as_str())
        );
    }
}

#[test]
fn available_tasks_can_skip_assigned_ones() {
    let hub = Hub::new("unassigned");
    let replies = hub.mcp(&[
        create("a"),
        create("b"),
        assign("a", "w1"),
        ("get_available_tasks", json::obj(vec![])),
        ("get_available_tasks", json::obj(vec![("unassigned_only", json::b(true))])),
    ]);
    assert_eq!(reply(&replies[3]).get("count"), Some(&json::i(2)));
    let open = reply(&replies[4]);
    assert_eq!(open.get("count"), Some(&json::i(1)));
    let ids: Vec<&str> = open
        .get("tasks")
        .and_then(Value::as_array)
        .unwrap()
        .iter()
        .filter_map(|t| t.get("id").and_then(Value::as_str))
        .collect();
    assert_eq!(ids, ["b"]);
}

fn handoff(id: i64, agent: Option<&str>) -> Value {
    let mut pairs = vec![("handoff_id", json::i(id))];
    if let Some(agent) = agent {
        pairs.push(("agent_id", json::s(agent)));
    }
    json::obj(pairs)
}

#[test]
fn explicit_handoff_tools_and_the_old_modes() {
    let hub = Hub::new("handoff");
    let broadcast = |targets: &[&str]| {
        (
            "broadcast",
            json::obj(vec![
                ("agent_id", json::s("planner")),
                (
                    "handoff_targets",
                    json::arr(targets.iter().map(|t| json::s(*t)).collect()),
                ),
            ]),
        )
    };
    let replies = hub.mcp(&[
        broadcast(&["w1", "w2"]),
        ("complete_handoff", handoff(1, None)),
        ("acknowledge_handoff", handoff(1, Some("w9"))),
        ("acknowledge_handoff", handoff(1, Some("w1"))),
        ("acknowledge_handoff", handoff(1, Some("w2"))),
        ("wait_for_handoff", json::obj(vec![("handoff_id", json::i(1)), ("timeout_s", json::f(0.1))])),
        ("complete_handoff", handoff(1, None)),
        ("wait_for_handoff", json::obj(vec![("handoff_id", json::i(1)), ("timeout_s", json::f(0.1))])),
        ("complete_handoff", handoff(1, None)),
        ("cancel_handoff", handoff(99, None)),
        // The old modes still work and keep their 0.8.0 replies.
        broadcast(&["w3"]),
        (
            "wait_for_handoff",
            json::obj(vec![("handoff_id", json::i(2)), ("mode", json::s("ack")), ("agent_id", json::s("w3"))]),
        ),
        ("wait_for_handoff", json::obj(vec![("handoff_id", json::i(2)), ("mode", json::s("complete"))])),
        broadcast(&["w4"]),
        ("cancel_handoff", handoff(3, None)),
        ("wait_for_handoff", json::obj(vec![("handoff_id", json::i(3)), ("mode", json::s("status"))])),
    ]);
    assert_eq!(reply(&replies[0]).get("handoff_id"), Some(&json::i(1)));
    let early = reply(&replies[1]);
    assert_eq!(early.get("completed"), Some(&json::b(false)));
    assert_eq!(early.get("reason").and_then(Value::as_str), Some("illegal_transition_from_pending"));
    let why = early.get("error").and_then(Value::as_str).unwrap();
    assert!(why.contains("0 of 2 recipients have acknowledged") && why.contains("acknowledge_handoff"), "{why}");
    let stranger = reply(&replies[2]);
    assert_eq!(stranger.get("reason").and_then(Value::as_str), Some("not_recipient"));
    assert!(stranger.get("error").and_then(Value::as_str).unwrap().contains("recipients are w1, w2"));
    assert_eq!(reply(&replies[3]).get("status").and_then(Value::as_str), Some("partially_acknowledged"));
    assert_eq!(reply(&replies[4]).get("status").and_then(Value::as_str), Some("acknowledged"));
    // Waiting does not complete anything.
    assert_eq!(reply(&replies[5]).get("timed_out"), Some(&json::b(true)));
    assert_eq!(
        text(&replies[6]),
        r#"{"completed":true,"handoff_id":1}"#
    );
    assert_eq!(reply(&replies[7]).get("timed_out"), Some(&json::b(false)));
    assert!(reply(&replies[8]).get("error").and_then(Value::as_str).unwrap().contains("already completed"));
    assert!(reply(&replies[9]).get("error").and_then(Value::as_str).unwrap().contains("handoff 99 not found"));
    assert_eq!(reply(&replies[11]).get("status").and_then(Value::as_str), Some("acknowledged"));
    assert_eq!(text(&replies[12]), r#"{"completed":true,"handoff_id":2}"#);
    assert_eq!(text(&replies[14]), r#"{"cancelled":true,"handoff_id":3}"#);
    assert_eq!(reply(&replies[15]).get("status").and_then(Value::as_str), Some("cancelled"));
}

#[test]
fn mcp_replies_are_compact_with_raw_utf8() {
    let hub = Hub::new("compact");
    let replies = hub.mcp(&[
        ("register_agent", json::obj(vec![("agent_id", json::s("root"))])),
        (
            "register_agent",
            json::obj(vec![("agent_id", json::s("kid")), ("parent_id", json::s("root"))]),
        ),
        ("get_agent_tree", json::obj(vec![("agent_id", json::s("root"))])),
    ]);
    for result in &replies {
        let body = text(result);
        assert!(!body.contains('\n') && !body.contains("\": ") && !body.contains(", \""), "{body}");
        json::parse(&body).unwrap();
    }
    let tree = text(&replies[2]);
    assert!(tree.contains("\u{2514}\u{2500}\u{2500} kid [active]"), "{tree}");
    assert!(!tree.contains("\\u2514"), "{tree}");
}

#[test]
fn version_flag_prints_the_package_version() {
    let hub = Hub::new("version");
    let want = format!("coordinationhub {}\n", env!("CARGO_PKG_VERSION"));
    for flag in ["--version", "-V"] {
        let out = hub.run_bare(&[flag], None);
        assert_eq!((out.code, out.stdout.as_str(), out.stderr.as_str()), (0, want.as_str(), ""));
    }
}

#[test]
fn lease_get_needs_no_agent_id() {
    let hub = Hub::new("leaseget");
    let replies = hub.mcp(&[
        (
            "manage_leases",
            json::obj(vec![("action", json::s("acquire")), ("agent_id", json::s("boss"))]),
        ),
        ("manage_leases", json::obj(vec![("action", json::s("get"))])),
        ("manage_leases", json::obj(vec![("action", json::s("refresh"))])),
    ]);
    let leader = reply(&replies[1]);
    assert_eq!(
        leader.get("leader").and_then(|l| l.get("holder_id")).and_then(Value::as_str),
        Some("boss"),
        "{}",
        text(&replies[1])
    );
    let (missing, is_error) = tool_text(&replies[2]);
    assert!(is_error);
    assert_eq!(missing, "Error: agent_id is required for action 'refresh'; pass the agent acting on the lease");
}

#[test]
fn unknown_tool_suggests_close_names_instead_of_listing_all() {
    let hub = Hub::new("unknowntool");
    let replies = hub.mcp(&[
        ("assign_tsk", json::obj(vec![])),
        ("no_such_tool_x", json::obj(vec![])),
    ]);
    let (near, is_error) = tool_text(&replies[0]);
    assert!(is_error);
    assert_eq!(
        near,
        "Error: Unknown tool: 'assign_tsk'. Did you mean 'assign_task'? tools/list names every tool this server offers."
    );
    assert_eq!(
        text(&replies[1]),
        "Error: Unknown tool: 'no_such_tool_x'. tools/list names every tool this server offers."
    );
}
