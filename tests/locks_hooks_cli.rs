//! 1.3.0 part B: region locks, hook subagent IDs, the lock-denied message,
//! redaction, lineage after a re-parent, readable CLI output, exit codes,
//! scan defaults and file size caps, and the zero-dependency check. One
//! test per fix; each fails on 1.2.0.

mod common;

#[path = "../examples/check_zero_deps.rs"]
#[allow(dead_code)]
mod check_zero_deps;

use common::Hub;
use coordinationhub::db::Lock;
use coordinationhub::json::{self, Value};
use coordinationhub::{hooks, Engine};
use std::path::PathBuf;

fn engine(name: &str) -> (Engine, PathBuf) {
    let dir = std::env::temp_dir().join(format!(
        "chub-b13-{name}-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    std::fs::create_dir_all(dir.join("proj")).unwrap();
    let opened = Engine::open(Some(&dir.join("store")), Some(&dir.join("proj")), "hub").unwrap();
    (opened, dir)
}

fn obj(pairs: Vec<(&str, Value)>) -> Value {
    json::obj(pairs)
}

fn ok(engine: &mut Engine, tool: &str, args: Value) -> Value {
    engine
        .call(tool, &args)
        .unwrap_or_else(|err| panic!("{tool}: {err}"))
}

fn region(path: &str, agent: &str, start: Option<i64>, end: Option<i64>) -> Value {
    let mut pairs = vec![("document_path", json::s(path)), ("agent_id", json::s(agent))];
    if let Some(start) = start {
        pairs.push(("region_start", json::i(start)));
    }
    if let Some(end) = end {
        pairs.push(("region_end", json::i(end)));
    }
    obj(pairs)
}

fn acquired(reply: &Value) -> bool {
    reply.get("acquired").and_then(Value::as_bool) == Some(true)
}

fn two_agents(engine: &mut Engine) {
    for id in ["alto", "bass"] {
        ok(engine, "register_agent", obj(vec![("agent_id", json::s(id))]));
    }
}

// ------------------------------------------------------------------
// B1. Region locks: inclusive, 1-based, both ends or neither.
// ------------------------------------------------------------------

#[test]
fn regions_sharing_a_line_overlap() {
    let (mut engine, _dir) = engine("share");
    two_agents(&mut engine);
    assert!(acquired(&ok(&mut engine, "acquire_lock", region("song.rs", "alto", Some(10), Some(20)))));
    let shared = ok(&mut engine, "acquire_lock", region("song.rs", "bass", Some(20), Some(30)));
    assert!(!acquired(&shared), "line 20 is in both: {}", json::dumps(&shared));
    assert_eq!(shared.get("locked_by"), Some(&json::s("alto")));
    let one_line = ok(&mut engine, "acquire_lock", region("song.rs", "bass", Some(20), Some(20)));
    assert!(!acquired(&one_line), "{}", json::dumps(&one_line));
    let after = ok(&mut engine, "acquire_lock", region("song.rs", "bass", Some(21), Some(30)));
    assert!(acquired(&after), "{}", json::dumps(&after));
    let before = ok(&mut engine, "acquire_lock", region("song.rs", "bass", Some(1), Some(9)));
    assert!(acquired(&before), "{}", json::dumps(&before));
}

#[test]
fn malformed_regions_are_refused_with_the_rule() {
    let (mut engine, _dir) = engine("bad");
    two_agents(&mut engine);
    let cases: [(Option<i64>, Option<i64>, &str); 5] = [
        (Some(12), None, "region_end is missing"),
        (None, Some(12), "region_start is missing"),
        (Some(30), Some(5), "region_start (30) is after region_end (5)"),
        (Some(0), Some(5), "region_start must be 1 or more (got 0)"),
        (Some(-2), Some(-1), "region_start must be 1 or more (got -2)"),
    ];
    for tool in ["acquire_lock", "release_lock", "refresh_lock"] {
        for (start, end, want) in cases {
            let err = engine
                .call(tool, &region("song.rs", "alto", start, end))
                .expect_err(tool);
            assert!(err.contains(want), "{tool} {start:?}-{end:?}: {err}");
            assert!(err.contains("inclusive, 1-based"), "{err}");
            assert!(err.starts_with(&format!("Input validation error: {tool}: ")), "{err}");
        }
    }
    // The retry path (a waiting acquire) is checked the same way.
    let mut waiting = region("song.rs", "alto", Some(12), None);
    if let Value::Object(pairs) = &mut waiting {
        pairs.push(("retry".into(), json::b(true)));
    }
    assert!(engine.call("acquire_lock", &waiting).unwrap_err().contains("region_end is missing"));
    // Nothing was locked by any refused call.
    let listed = ok(&mut engine, "list_locks", obj(vec![]));
    assert_eq!(listed.get("count"), Some(&json::i(0)), "{}", json::dumps(&listed));
}

#[test]
fn half_open_region_no_longer_slips_past_a_held_range() {
    // 1.2.0 granted --region-start 12 without an end against a held 1-100.
    let hub = Hub::new("cliregion");
    hub.run(&["register", "alto"]);
    hub.run(&["register", "bass"]);
    let held = hub.run(&["acquire-lock", "song.rs", "alto", "--region-start", "1", "--region-end", "100"]);
    assert_eq!(held.code, 0, "{}{}", held.stdout, held.stderr);
    let half = hub.run(&["acquire-lock", "song.rs", "bass", "--region-start", "12"]);
    assert_eq!(half.code, 1, "{}{}", half.stdout, half.stderr);
    assert!(half.stderr.contains("region_end is missing"), "{}", half.stderr);
    let inverted = hub.run(&["acquire-lock", "song.rs", "bass", "--region-start", "30", "--region-end", "5", "--json"]);
    assert_eq!(inverted.code, 1);
    assert!(inverted.stdout.contains("is after region_end"), "{}", inverted.stdout);
    let inside = hub.run(&["acquire-lock", "song.rs", "bass", "--region-start", "100", "--region-end", "120"]);
    assert_eq!(inside.code, 4, "line 100 is held: {}{}", inside.stdout, inside.stderr);
}

#[test]
fn odd_regions_already_in_the_journal_still_block() {
    let (mut engine, _dir) = engine("legacy");
    two_agents(&mut engine);
    // Rows a 1.2.0 binary could write: a start without an end, and an
    // inverted pair.
    engine.store.begin().unwrap();
    let now = coordinationhub::util::now();
    for (id, path, start, end) in [(901, "old.rs", Some(12), None), (902, "flip.rs", Some(30), Some(5))] {
        engine.store.state.locks.push(Lock {
            id,
            document_path: coordinationhub::util::normalize_path(path, engine.project_root.as_deref()),
            locked_by: "alto".into(),
            locked_at: now,
            lock_ttl: 300.0,
            lock_type: "exclusive".into(),
            region_start: start,
            region_end: end,
            worktree_root: None,
        });
    }
    engine.store.commit().unwrap();
    engine.store.end();
    let tail = ok(&mut engine, "acquire_lock", region("old.rs", "bass", Some(500), Some(600)));
    assert!(!acquired(&tail), "a start without an end runs to the end of the file");
    let head = ok(&mut engine, "acquire_lock", region("old.rs", "bass", Some(1), Some(11)));
    assert!(acquired(&head), "{}", json::dumps(&head));
    let middle = ok(&mut engine, "acquire_lock", region("flip.rs", "bass", Some(10), Some(20)));
    assert!(!acquired(&middle), "30-5 is read as 5-30");
    for tool in ["list_locks", "get_agent_tree", "status"] {
        ok(&mut engine, tool, obj(vec![]));
    }
    let shown = ok(&mut engine, "get_lock_status", obj(vec![("document_path", json::s("old.rs"))]));
    assert_eq!(shown.get("locked"), Some(&json::b(true)));
}

// ------------------------------------------------------------------
// B2. Parallel subagents of one type get their own IDs.
// ------------------------------------------------------------------

fn hook(engine: &mut Engine, text: &str) -> Option<Value> {
    hooks::handle(engine, &json::parse(text).unwrap())
}

fn agents(engine: &mut Engine) -> Vec<(String, String, Option<String>, Option<String>)> {
    let listed = ok(engine, "list_agents", obj(vec![("active_only", json::b(false))]));
    listed
        .get("agents")
        .and_then(Value::as_array)
        .unwrap()
        .iter()
        .map(|row| {
            let text = |key: &str| row.get(key).and_then(Value::as_str).map(str::to_string);
            (text("agent_id").unwrap(), text("status").unwrap(), text("parent_id"), text("raw_ide_id"))
        })
        .collect()
}

fn write_event(session: &str, raw: &str, path: &str) -> String {
    json::dumps(&obj(vec![
        ("hook_event_name", json::s("PreToolUse")),
        ("tool_name", json::s("Write")),
        ("session_id", json::s(session)),
        ("agent_id", json::s(raw)),
        ("agent_type", json::s("Explore")),
        ("tool_input", obj(vec![("file_path", json::s(path))])),
    ]))
}

#[test]
fn parallel_subagents_of_one_type_get_their_own_ids() {
    let (mut engine, dir) = engine("subagents");
    let e = &mut engine;
    hook(e, r#"{"hook_event_name":"SessionStart","session_id":"s1"}"#);
    hook(e, r#"{"hook_event_name":"SubagentStart","session_id":"s1","agent_id":"r1","agent_type":"Explore"}"#);
    hook(e, r#"{"hook_event_name":"SubagentStart","session_id":"s1","agent_id":"r2","agent_type":"Explore"}"#);
    let rows = agents(e);
    let child = |rows: &[(String, String, Option<String>, Option<String>)], id: &str| {
        rows.iter().find(|row| row.0 == id).cloned().unwrap_or_else(|| panic!("no {id} in {rows:?}"))
    };
    let first = child(&rows, "hub.cc.s1.Explore.0");
    let second = child(&rows, "hub.cc.s1.Explore.1");
    assert_eq!(first.3.as_deref(), Some("r1"));
    assert_eq!(second.3.as_deref(), Some("r2"));
    assert_eq!(second.2.as_deref(), Some("hub.cc.s1"));
    // r1 stops: only its own child goes.
    hook(e, r#"{"hook_event_name":"SubagentStop","session_id":"s1","agent_id":"r1","agent_type":"Explore"}"#);
    let rows = agents(e);
    assert_eq!(child(&rows, "hub.cc.s1.Explore.0").1, "stopped");
    assert_eq!(child(&rows, "hub.cc.s1.Explore.1").1, "active");
    // r2 writes: the lock is its child's, and no agent is named r2.
    let path = dir.join("proj").join("notes.md").display().to_string();
    let reply = hook(e, &write_event("s1", "r2", &path)).expect("a decision");
    assert!(json::dumps(&reply).contains("\"allow\""), "{}", json::dumps(&reply));
    let status = ok(e, "get_lock_status", obj(vec![("document_path", json::s(&path))]));
    assert_eq!(status.get("locked_by"), Some(&json::s("hub.cc.s1.Explore.1")));
    // A write from a subagent whose start was missed gets a child too.
    let other = dir.join("proj").join("other.md").display().to_string();
    hook(e, &write_event("s1", "r3", &other));
    let rows = agents(e);
    assert!(rows.iter().all(|row| !matches!(row.0.as_str(), "r1" | "r2" | "r3")), "{rows:?}");
    assert!(rows.iter().all(|row| row.2.is_some() || row.0 == "hub.cc.s1"), "a stray root: {rows:?}");
    let late = child(&rows, "hub.cc.s1.Explore.2");
    assert_eq!(late.3.as_deref(), Some("r3"));
    // A stop for an unknown raw ID stops nothing.
    hook(e, r#"{"hook_event_name":"SubagentStop","session_id":"s1","agent_id":"zz","agent_type":"Explore"}"#);
    assert_eq!(child(&agents(e), "hub.cc.s1.Explore.1").1, "active");
}

#[test]
fn subagent_events_without_a_raw_id_keep_the_zero_index() {
    let (mut engine, _dir) = engine("noraw");
    hook(&mut engine, r#"{"hook_event_name":"SubagentStart","session_id":"s2","agent_type":"Plan"}"#);
    hook(&mut engine, r#"{"hook_event_name":"SubagentStart","session_id":"s2","agent_type":"Plan"}"#);
    let rows = agents(&mut engine);
    let plans: Vec<_> = rows.iter().filter(|row| row.0.starts_with("hub.cc.s2.Plan.")).collect();
    assert_eq!(plans.len(), 1, "{rows:?}");
    assert_eq!(plans[0].0, "hub.cc.s2.Plan.0");
    hook(&mut engine, r#"{"hook_event_name":"SubagentStop","session_id":"s2","agent_type":"Plan"}"#);
    let rows = agents(&mut engine);
    assert_eq!(rows.iter().find(|row| row.0 == "hub.cc.s2.Plan.0").unwrap().1, "stopped");
}

// ------------------------------------------------------------------
// B3. The lock-denied message, and agent IDs.
// ------------------------------------------------------------------

#[test]
fn a_denied_write_says_who_until_when_and_what_to_do() {
    let (mut engine, dir) = engine("denied");
    let path = dir.join("proj").join("lib.rs").display().to_string();
    let event = |session: &str| {
        json::dumps(&obj(vec![
            ("hook_event_name", json::s("PreToolUse")),
            ("tool_name", json::s("Edit")),
            ("session_id", json::s(session)),
            ("tool_input", obj(vec![("file_path", json::s(&path))])),
        ]))
    };
    hook(&mut engine, &event("holder1"));
    let denied = hook(&mut engine, &event("blocked2")).expect("a decision");
    let output = denied.get("hookSpecificOutput").unwrap();
    assert_eq!(output.get("permissionDecision"), Some(&json::s("deny")));
    let reason = output.get("permissionDecisionReason").and_then(Value::as_str).unwrap();
    assert!(reason.contains("is locked by hub.cc.holder1 until "), "{reason}");
    assert!(reason.contains(" s from now"), "{reason}");
    assert!(reason.contains("send_message"), "{reason}");
    assert!(reason.contains("wait_for_locks"), "{reason}");
    assert!(reason.contains("another file"), "{reason}");
    assert!(!reason.contains("release-lock"), "{reason}");
    assert!(!reason.contains("force"), "{reason}");
}

#[test]
fn register_refuses_control_characters_and_long_ids() {
    let (mut engine, _dir) = engine("ids");
    for (id, want) in [
        ("two\nlines", "U+000A at character 4"),
        ("bell\u{7}", "U+0007"),
        ("esc\u{1b}[31m", "U+001B"),
        ("sep\u{2028}x", "U+2028"),
    ] {
        let err = engine
            .call("register_agent", &obj(vec![("agent_id", json::s(id))]))
            .expect_err(id);
        assert!(err.contains("agent_id contains a control character or line break"), "{err}");
        assert!(err.contains(want), "{err}");
    }
    let err = engine
        .call(
            "register_agent",
            &obj(vec![("agent_id", json::s("kid")), ("parent_id", json::s("dad\r"))]),
        )
        .unwrap_err();
    assert!(err.contains("parent_id contains a control character"), "{err}");
    let long = "x".repeat(257);
    let err = engine
        .call("register_agent", &obj(vec![("agent_id", json::s(&long))]))
        .unwrap_err();
    assert!(err.contains("256"), "{err}");
    // The CLI skips the schema; the check still applies.
    let hub = Hub::new("cliids");
    let out = hub.run(&["register", &long]);
    assert_eq!(out.code, 1);
    assert!(out.stderr.contains("agent_id is 257 characters long; the limit is 256"), "{}", out.stderr);
    // Printable IDs of any script are still fine, up to the limit.
    for id in ["agent-\u{fc}-\u{6771}\u{4eac}", "with space", &"y".repeat(256)] {
        ok(&mut engine, "register_agent", obj(vec![("agent_id", json::s(id))]));
    }
}

// ------------------------------------------------------------------
// B4. Subagent prompts are redacted before they are stored.
// ------------------------------------------------------------------

#[test]
fn stashed_subagent_prompts_are_redacted() {
    let (mut engine, _dir) = engine("stash");
    let secret_hex = "ABCDEF0123456789abcdef0123456789AB";
    let event = json::dumps(&obj(vec![
        ("hook_event_name", json::s("PreToolUse")),
        ("tool_name", json::s("Agent")),
        ("session_id", json::s("s3")),
        ("tool_use_id", json::s("tu1")),
        (
            "tool_input",
            obj(vec![
                ("subagent_type", json::s("Explore")),
                ("description", json::s("mail ops@example.com about sk-abcdefghijklmnopqrstu")),
                ("prompt", json::s(format!("use token {secret_hex} and AKIAABCDEFGHIJKLMNOP"))),
            ]),
        ),
    ]));
    hook(&mut engine, &event);
    engine.store.begin().unwrap();
    let row = engine.store.state.spawns.iter().find(|row| row.id == "hook:tu1").cloned().unwrap();
    engine.store.end();
    let prompt = row.prompt.unwrap();
    let description = row.description.unwrap();
    assert!(prompt.contains("[REDACTED_HEX]"), "{prompt}");
    assert!(!prompt.contains(secret_hex), "{prompt}");
    assert!(prompt.contains("[REDACTED_AWS_KEY_ID]"), "{prompt}");
    assert!(description.contains("[REDACTED_EMAIL]"), "{description}");
    assert!(description.contains("[REDACTED_API_KEY]"), "{description}");
    // A user prompt with an upper-case hex token is redacted too.
    hook(
        &mut engine,
        &format!(r#"{{"hook_event_name":"UserPromptSubmit","session_id":"s3","prompt":"key {}"}}"#, secret_hex.to_uppercase()),
    );
    let status = ok(&mut engine, "get_agent_status", obj(vec![("agent_id", json::s("hub.cc.s3"))]));
    let task = status.get("current_task").and_then(Value::as_str).unwrap();
    assert_eq!(task, "key [REDACTED_HEX]");
}

// ------------------------------------------------------------------
// B5. Lineage after a re-parent; time stamps.
// ------------------------------------------------------------------

fn descendants(reply: &Value) -> Vec<(i64, String, String)> {
    reply
        .get("descendants_status")
        .and_then(Value::as_array)
        .unwrap()
        .iter()
        .map(|row| {
            (
                row.get("depth").and_then(Value::as_i64).unwrap(),
                row.get("agent_id").and_then(Value::as_str).unwrap().to_string(),
                row.get("status").and_then(Value::as_str).unwrap().to_string(),
            )
        })
        .collect()
}

#[test]
fn descendants_follow_a_re_parent() {
    for how in ["deregister", "reap"] {
        let (mut engine, _dir) = engine(how);
        let reg = |engine: &mut Engine, id: &str, parent: Option<&str>| {
            let mut pairs = vec![("agent_id", json::s(id))];
            if let Some(parent) = parent {
                pairs.push(("parent_id", json::s(parent)));
            }
            ok(engine, "register_agent", obj(pairs))
        };
        reg(&mut engine, "top", None);
        reg(&mut engine, "mid", Some("top"));
        reg(&mut engine, "leaf", Some("mid"));
        reg(&mut engine, "bud", Some("leaf"));
        if how == "deregister" {
            ok(&mut engine, "deregister_agent", obj(vec![("agent_id", json::s("mid"))]));
        } else {
            // Only mid goes quiet: the others beat after the reap.
            std::thread::sleep(std::time::Duration::from_millis(30));
            for id in ["top", "leaf", "bud"] {
                ok(&mut engine, "heartbeat", obj(vec![("agent_id", json::s(id))]));
            }
            ok(&mut engine, "admin_locks", obj(vec![("action", json::s("reap_stale")), ("timeout", json::f(0.02))]));
        }
        let again = reg(&mut engine, "top", None);
        let rows = descendants(&again);
        assert!(rows.contains(&(1, "leaf".into(), "active".into())), "{how}: {rows:?}");
        assert!(rows.contains(&(2, "bud".into(), "active".into())), "{how}: {rows:?}");
        assert!(rows.contains(&(1, "mid".into(), "stopped".into())), "{how}: {rows:?}");
        let mid = reg(&mut engine, "mid", Some("top"));
        assert!(descendants(&mid).is_empty(), "{how}: leaf moved away from mid: {:?}", descendants(&mid));
    }
}

#[test]
fn utc_stamps_and_unconvertible_times() {
    use coordinationhub::util::{local_stamp, utc_stamp};
    assert_eq!(utc_stamp(0), "1970-01-01 00:00:00 UTC");
    assert_eq!(utc_stamp(951_782_400), "2000-02-29 00:00:00 UTC");
    assert_eq!(utc_stamp(-1), "1969-12-31 23:59:59 UTC");
    assert_eq!(utc_stamp(1_794_614_400), "2026-11-14 00:00:00 UTC");
    // Too far out for the local conversion: the UTC stamp, not garbage.
    let far = local_stamp(1.0e18);
    assert!(far.ends_with(" UTC"), "{far}");
    assert!(!far.starts_with("1900-"), "{far}");
    assert_eq!(local_stamp(0.0).len(), 19);
}

// ------------------------------------------------------------------
// B6. Readable output of five commands.
// ------------------------------------------------------------------

#[test]
fn agent_tree_text_shows_the_tree() {
    let hub = Hub::new("tree");
    hub.run(&["register", "conductor"]);
    hub.run(&["register", "violin", "--parent-id", "conductor"]);
    let out = hub.run(&["agent-tree", "conductor"]);
    assert_eq!(out.code, 0);
    assert!(out.stdout.contains("conductor [active]"), "{}", out.stdout);
    assert!(out.stdout.contains("violin [active]"), "{}", out.stdout);
    assert!(!out.stdout.contains("No agent tree available"), "{}", out.stdout);
    let missing = hub.run(&["agent-tree", "nobody"]);
    assert!(missing.stdout.contains("No agent tree available: Agent not found: nobody"), "{}", missing.stdout);
}

#[test]
fn dashboard_text_lists_agents_locks_and_tasks() {
    let hub = Hub::new("dash");
    hub.run(&["register", "conductor"]);
    hub.run(&["acquire-lock", "score.md", "conductor"]);
    hub.run(&["create-task", "T-1", "conductor", "tune"]);
    let out = hub.run(&["dashboard"]);
    assert_eq!(out.code, 0, "{}", out.stderr);
    assert!(out.stdout.contains("Agents (1):\n  conductor: active"), "{}", out.stdout);
    assert!(out.stdout.contains("Locks (1):"), "{}", out.stdout);
    assert!(out.stdout.contains("score.md — conductor (exclusive)"), "{}", out.stdout);
    assert!(out.stdout.contains("Tasks (1):\n  [pending] T-1"), "{}", out.stdout);
    // --json still prints the status counts.
    let counts = hub.json(&["dashboard"]);
    assert_eq!(counts.get("active_locks"), Some(&json::i(1)));
}

#[test]
fn agent_status_text_lists_each_lock() {
    let hub = Hub::new("astatus");
    hub.run(&["register", "conductor"]);
    hub.run(&["acquire-lock", "score.md", "conductor"]);
    hub.run(&["acquire-lock", "parts.md", "conductor", "--region-start", "3", "--region-end", "9", "--lock-type", "shared"]);
    let out = hub.run(&["agent-status", "conductor"]);
    assert!(out.stdout.contains("  Locks: 2\n"), "{}", out.stdout);
    assert!(out.stdout.contains("score.md (exclusive, expires "), "{}", out.stdout);
    assert!(out.stdout.contains("parts.md (shared, lines 3-9, expires "), "{}", out.stdout);
    assert!(!out.stdout.contains(" ()"), "{}", out.stdout);
    let reply = hub.json(&["agent-status", "conductor"]);
    assert_eq!(reply.get("active_locks").and_then(Value::as_array).map(|items| items.len()), Some(2));
    let details = reply.get("locks").and_then(Value::as_array).unwrap();
    assert!(details.iter().all(|lock| lock.get("expires_at").and_then(Value::as_f64).is_some()));
}

#[test]
fn load_and_validate_spec_fail_with_exit_one() {
    let hub = Hub::new("spec");
    let missing = hub.run(&["load-spec", "nowhere.json"]);
    assert_eq!(missing.code, 1, "{}", missing.stdout);
    assert!(missing.stdout.contains("Coordination spec NOT loaded"), "{}", missing.stdout);
    assert!(missing.stdout.contains("Coordination spec not found: nowhere.json"), "{}", missing.stdout);
    assert!(!missing.stdout.contains("Loaded coordination spec"), "{}", missing.stdout);
    assert_eq!(hub.run(&["load-spec", "nowhere.json", "--json"]).code, 1);
    let invalid = hub.run(&["validate-spec"]);
    assert!(invalid.stdout.contains("Coordination spec is INVALID"), "{}", invalid.stdout);
    assert_eq!(invalid.code, 1);
    std::fs::write(
        hub.project.join("coordination_spec.json"),
        r#"{"agents":[{"id":"planner","role":"plan","responsibilities":["plan"]}],"handoffs":[]}"#,
    )
    .unwrap();
    let loaded = hub.run(&["load-spec"]);
    assert_eq!(loaded.code, 0, "{}", loaded.stdout);
    assert!(loaded.stdout.contains("Loaded coordination spec"), "{}", loaded.stdout);
    assert_eq!(hub.run(&["validate-spec"]).code, 0);
}

#[test]
fn dead_letter_queue_text_says_when() {
    let hub = Hub::new("dlq");
    hub.run(&["register", "conductor"]);
    hub.run(&["create-task", "T-9", "conductor", "tune"]);
    for status in ["failed", "pending", "failed", "pending", "failed"] {
        hub.run(&["update-task-status", "T-9", status, "--error", "snapped"]);
    }
    let reply = hub.json(&["dead-letter-queue"]);
    let rows = reply.get("dead_letter_tasks").and_then(Value::as_array).unwrap();
    assert_eq!(rows.len(), 1, "{}", json::dumps(&reply));
    let at = rows[0].get("dead_letter_at").and_then(Value::as_f64).expect("dead_letter_at");
    assert_eq!(rows[0].get("orphan"), Some(&json::b(false)));
    let out = hub.run(&["dead-letter-queue"]);
    let want = format!("  T-9 — failed at {}", coordinationhub::util::local_stamp(at));
    assert!(out.stdout.contains(&want), "{}\nwant {want}", out.stdout);
}

// ------------------------------------------------------------------
// B7. Exit codes, the watch screen clear, init's backup.
// ------------------------------------------------------------------

#[test]
fn doctor_exits_one_when_a_check_fails() {
    let hub = Hub::new("doctor");
    let home = hub.project.join("home");
    std::fs::create_dir_all(&home).unwrap();
    let run = |json: bool| {
        let mut command = std::process::Command::new(env!("CARGO_BIN_EXE_coordinationhub"));
        command
            .args(["doctor", "--storage-dir"])
            .arg(hub.project.join("no-store"))
            .env("HOME", &home)
            .env("USERPROFILE", &home);
        if json {
            command.arg("--json");
        }
        command.output().unwrap()
    };
    let text = run(false);
    assert!(String::from_utf8_lossy(&text.stdout).contains("[FAIL] storage_dir"));
    assert_eq!(text.status.code(), Some(1));
    let json_out = run(true);
    assert!(String::from_utf8_lossy(&json_out.stdout).contains("\"all_ok\": false"));
    assert_eq!(json_out.status.code(), Some(1));
}

#[test]
fn watch_does_not_clear_a_pipe() {
    let hub = Hub::new("watch");
    hub.run(&["register", "conductor"]);
    let mut child = std::process::Command::new(env!("CARGO_BIN_EXE_coordinationhub"))
        .args(["watch", "--interval", "1", "--storage-dir"])
        .arg(&hub.store)
        .arg("--project-root")
        .arg(&hub.project)
        .stdout(std::process::Stdio::piped())
        .spawn()
        .unwrap();
    std::thread::sleep(std::time::Duration::from_millis(2500));
    let _ = child.kill();
    let out = child.wait_with_output().unwrap();
    let text = String::from_utf8_lossy(&out.stdout);
    assert!(text.contains("conductor [active]"), "{text}");
    assert!(!text.contains('\u{1b}'), "{text:?}");
}

#[test]
fn init_keeps_a_differing_hooks_file() {
    let hub = Hub::new("init");
    let home = hub.project.join("home");
    let hooks = home.join(".coordinationhub").join("hooks.json");
    std::fs::create_dir_all(hooks.parent().unwrap()).unwrap();
    std::fs::write(&hooks, "{\"mine\": true}\n").unwrap();
    let run = || {
        std::process::Command::new(env!("CARGO_BIN_EXE_coordinationhub"))
            .args(["init", "--storage-dir"])
            .arg(&hub.store)
            .arg("--project-root")
            .arg(&hub.project)
            .env("HOME", &home)
            .env("USERPROFILE", &home)
            .output()
            .unwrap()
    };
    let first = run();
    let text = String::from_utf8_lossy(&first.stdout).to_string();
    let backup = home.join(".coordinationhub").join("hooks.json.bak");
    assert!(text.contains("Previous hooks file kept at"), "{text}");
    assert_eq!(std::fs::read_to_string(&backup).unwrap(), "{\"mine\": true}\n");
    assert!(std::fs::read_to_string(&hooks).unwrap().contains("SessionStart"));
    // Running init again writes the same file: no new backup, no notice.
    std::fs::remove_file(&backup).unwrap();
    let second = String::from_utf8_lossy(&run().stdout).to_string();
    assert!(!second.contains("Previous hooks file kept at"), "{second}");
    assert!(!backup.exists());
}

#[test]
fn no_op_flags_say_so_in_help() {
    let hub = Hub::new("noop");
    let out = hub.run_bare(&["list-locks", "--help"], Some("200"));
    assert!(out.stdout.contains("--replica             No effect since 1.0"), "{}", out.stdout);
    assert!(out.stdout.contains("--force-refresh       No effect since 1.0"), "{}", out.stdout);
    assert!(!out.stdout.contains("WAL") && !out.stdout.contains("SQLite"), "{}", out.stdout);
    // Still accepted.
    hub.run(&["register", "conductor"]);
    assert_eq!(hub.run(&["list-locks", "--replica", "--force-refresh"]).code, 0);
}

// ------------------------------------------------------------------
// B8. scan_project defaults; size caps.
// ------------------------------------------------------------------

#[test]
fn scan_finds_rust_skips_target_and_symlinked_dirs() {
    let (mut engine, dir) = engine("scan");
    let project = dir.join("proj");
    for (path, text) in [
        ("src/main.rs", "fn main() {}\n"),
        ("Cargo.toml", "[package]\n"),
        ("target/release/build/out.rs", "// generated\n"),
        ("target/debug/notes.md", "x\n"),
    ] {
        let file = project.join(path);
        std::fs::create_dir_all(file.parent().unwrap()).unwrap();
        std::fs::write(file, text).unwrap();
    }
    let outside = dir.join("outside");
    std::fs::create_dir_all(&outside).unwrap();
    std::fs::write(outside.join("far.rs"), "x\n").unwrap();
    #[cfg(unix)]
    let linked = std::os::unix::fs::symlink(&outside, project.join("linked")).is_ok();
    #[cfg(windows)]
    let linked = std::os::windows::fs::symlink_dir(&outside, project.join("linked")).is_ok();
    ok(&mut engine, "register_agent", obj(vec![("agent_id", json::s("conductor"))]));
    let scanned = ok(&mut engine, "scan_project", obj(vec![]));
    assert_eq!(scanned.get("scanned"), Some(&json::i(2)), "{} (symlink made: {linked})", json::dumps(&scanned));
    let map = json::dumps(&ok(&mut engine, "get_file_agent_map", obj(vec![])));
    assert!(map.contains("src/main.rs"), "{map}");
    assert!(!map.contains("target/"), "{map}");
    assert!(!map.contains("far.rs"), "{map}");
}

#[test]
fn oversized_spec_and_suite_files_are_refused() {
    let (mut engine, dir) = engine("caps");
    let big = dir.join("big.json");
    let mut text = String::from("{\"agents\":[],\"pad\":\"");
    text.push_str(&"a".repeat(1024 * 1024));
    text.push_str("\"}");
    std::fs::write(&big, &text).unwrap();
    let spec = ok(&mut engine, "load_coordination_spec", obj(vec![("path", json::s(big.display().to_string()))]));
    assert_eq!(spec.get("loaded"), Some(&json::b(false)));
    let err = spec.get("error").and_then(Value::as_str).unwrap();
    assert!(err.contains("bytes; the limit is 1048576 bytes"), "{err}");
    let huge = dir.join("huge.json");
    let file = std::fs::File::create(&huge).unwrap();
    file.set_len(16 * 1024 * 1024 + 1).unwrap();
    let suite = ok(&mut engine, "run_assessment", obj(vec![("suite_path", json::s(huge.display().to_string()))]));
    let err = suite.get("error").and_then(Value::as_str).unwrap_or("");
    assert!(err.contains("assessment suite") && err.contains("the limit is 16777216 bytes"), "{}", json::dumps(&suite));
}

// ------------------------------------------------------------------
// B9. check_zero_deps refuses every dependency table.
// ------------------------------------------------------------------

#[test]
fn zero_deps_check_sees_target_and_workspace_tables() {
    let base = "[package]\nname = \"x\"\nversion = \"0.1.0\"\n\n[dependencies]\n\n[dev-dependencies]\n";
    assert!(check_zero_deps::problems(base).is_empty());
    assert!(check_zero_deps::problems(include_str!("../Cargo.toml")).is_empty());
    let cases = [
        ("[target.'cfg(unix)'.dependencies]\nlibc = \"0.2\"\n", "target.'cfg(unix)'.dependencies must be empty: {'libc': '0.2'}"),
        ("[target.x86_64-pc-windows-gnu.dev-dependencies]\nwinapi = \"0.3\"\n", "target.x86_64-pc-windows-gnu.dev-dependencies must be empty"),
        ("[target.'cfg(windows)'.build-dependencies]\ncc = \"1\"\n", "target.'cfg(windows)'.build-dependencies must be empty"),
        ("[workspace.dependencies]\nserde = \"1\"\n", "workspace.dependencies must be empty: {'serde': '1'}"),
        ("[dev_dependencies]\nrand = \"0.8\"\n", "dev_dependencies must be empty"),
        ("[dependencies.libc]\nversion = \"0.2\"\n", "dependency tables must be empty: {'libc': {'version': '0.2'}}"),
    ];
    for (extra, want) in cases {
        let found = check_zero_deps::problems(&format!("{base}\n{extra}"));
        assert!(found.iter().any(|line| line.starts_with(want)), "{extra}: {found:?}");
    }
    let build = check_zero_deps::problems("[package]\nname = \"x\"\nbuild = \"gen.rs\"\n");
    assert!(build.iter().any(|line| line.starts_with("package.build must be empty")), "{build:?}");
    assert!(check_zero_deps::problems("[package]\nname = \"x\"\nbuild = false\n").is_empty());
}
