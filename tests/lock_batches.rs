//! 1.4.0: several paths per `acquire_lock` / `release_lock` call (all or
//! nothing by default, `best_effort` to take the free ones), and the
//! restart fields of `register_agent` (`held_locks`, `unread_messages`,
//! `messages`). Listed in MIGRATION.md.

mod common;

use common::Hub;
use coordinationhub::json::{self, Value};
use coordinationhub::Engine;
use std::path::{Path, PathBuf};

fn dir(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!(
        "chub-b14-{name}-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    std::fs::create_dir_all(dir.join("proj")).unwrap();
    dir
}

thread_local! {
    static PROJECT: std::cell::RefCell<PathBuf> = std::cell::RefCell::new(PathBuf::new());
}

/// An engine on the test's store. Relative paths resolve against the
/// working directory (as in 0.8.0), so the tests name files through `at`.
fn open(dir: &Path) -> Engine {
    PROJECT.with(|root| *root.borrow_mut() = dir.join("proj"));
    Engine::open(Some(&dir.join("store")), Some(&dir.join("proj")), "hub").unwrap()
}

/// A file in the project, as an absolute path.
fn at(name: &str) -> Value {
    PROJECT.with(|root| json::s(root.borrow().join(name).to_string_lossy()))
}

fn s(text: &str) -> Value {
    json::s(text)
}

fn paths(items: &[&str]) -> Value {
    json::arr(items.iter().map(|item| at(item)).collect())
}

fn ok(engine: &mut Engine, tool: &str, args: Vec<(&str, Value)>) -> Value {
    engine
        .call(tool, &json::obj(args))
        .unwrap_or_else(|err| panic!("{tool}: {err}"))
}

fn strings(value: &Value, key: &str) -> Vec<String> {
    value
        .get(key)
        .and_then(Value::as_array)
        .unwrap_or_else(|| panic!("no {key} in {}", json::dumps(value)))
        .iter()
        .filter_map(Value::as_str)
        .map(str::to_string)
        .collect()
}

fn held_by(engine: &mut Engine, agent: &str) -> Vec<String> {
    let listed = ok(engine, "list_locks", vec![("agent_id", s(agent))]);
    let mut out: Vec<String> = listed
        .get("locks")
        .and_then(Value::as_array)
        .unwrap()
        .iter()
        .filter_map(|lock| lock.get("document_path").and_then(Value::as_str))
        .map(str::to_string)
        .collect();
    out.sort();
    out
}

fn register(engine: &mut Engine, agent: &str) -> Value {
    ok(engine, "register_agent", vec![("agent_id", s(agent))])
}

#[test]
fn a_list_of_free_paths_is_locked_in_one_call() {
    let dir = dir("free");
    let mut hub = open(&dir);
    register(&mut hub, "ana");
    let reply = ok(
        &mut hub,
        "acquire_lock",
        vec![("document_path", paths(&["src/a.rs", "src/b.rs", "src/a.rs"])), ("agent_id", s("ana"))],
    );
    assert_eq!(reply.get("acquired"), Some(&json::b(true)), "{}", json::dumps(&reply));
    assert_eq!(reply.get("mode").and_then(Value::as_str), Some("all_or_nothing"));
    assert_eq!(reply.get("locked_by").and_then(Value::as_str), Some("ana"));
    assert_eq!(strings(&reply, "acquired_paths"), vec!["src/a.rs", "src/b.rs"], "duplicates count once");
    assert!(strings(&reply, "failed_paths").is_empty());
    assert!(reply.get("expires_at").and_then(Value::as_f64).is_some());
    assert_eq!(held_by(&mut hub, "ana"), vec!["src/a.rs", "src/b.rs"]);
    // Taking them again renews them, as one path does.
    let again = ok(
        &mut hub,
        "acquire_lock",
        vec![("document_path", paths(&["src/a.rs", "src/b.rs"])), ("agent_id", s("ana"))],
    );
    assert_eq!(again.get("acquired"), Some(&json::b(true)));
    assert_eq!(held_by(&mut hub, "ana").len(), 2);
}

#[test]
fn one_held_path_takes_none_and_names_every_holder() {
    let dir = dir("rollback");
    let mut hub = open(&dir);
    for agent in ["ana", "bo", "cy"] {
        register(&mut hub, agent);
    }
    ok(&mut hub, "acquire_lock", vec![("document_path", at("src/b.rs")), ("agent_id", s("bo"))]);
    ok(&mut hub, "acquire_lock", vec![("document_path", at("src/d.rs")), ("agent_id", s("cy"))]);
    let conflicts_before = ok(&mut hub, "get_conflicts", vec![]).get("conflicts").and_then(Value::as_array).unwrap().len();
    let reply = ok(
        &mut hub,
        "acquire_lock",
        vec![
            ("document_path", paths(&["src/a.rs", "src/b.rs", "src/c.rs", "src/d.rs"])),
            ("agent_id", s("ana")),
        ],
    );
    assert_eq!(reply.get("acquired"), Some(&json::b(false)), "{}", json::dumps(&reply));
    assert!(strings(&reply, "acquired_paths").is_empty());
    assert_eq!(strings(&reply, "failed_paths"), vec!["src/b.rs", "src/d.rs"]);
    let conflicts = reply.get("conflicts").and_then(Value::as_array).unwrap();
    let named: Vec<(String, String)> = conflicts
        .iter()
        .map(|row| {
            (
                row.get("document_path").and_then(Value::as_str).unwrap().to_string(),
                row.get("locked_by").and_then(Value::as_str).unwrap().to_string(),
            )
        })
        .collect();
    assert_eq!(named, vec![("src/b.rs".into(), "bo".into()), ("src/d.rs".into(), "cy".into())]);
    assert_eq!(reply.get("locked_by").and_then(Value::as_str), Some("bo"), "first holder, as one path names it");
    assert!(reply.get("hint").and_then(Value::as_str).unwrap().contains("best_effort"));
    // Rolled back: the free paths were not taken, and nothing else changed.
    assert!(held_by(&mut hub, "ana").is_empty());
    let status = ok(&mut hub, "get_lock_status", vec![("document_path", at("src/a.rs"))]);
    assert_eq!(status.get("locked"), Some(&json::b(false)));
    // Each denial is recorded, as for one path.
    let conflicts_after = ok(&mut hub, "get_conflicts", vec![]).get("conflicts").and_then(Value::as_array).unwrap().len();
    assert_eq!(conflicts_after, conflicts_before + 2);
}

#[test]
fn best_effort_takes_the_free_paths_and_reports_the_rest() {
    let dir = dir("best");
    let mut hub = open(&dir);
    register(&mut hub, "ana");
    register(&mut hub, "bo");
    ok(&mut hub, "acquire_lock", vec![("document_path", at("src/b.rs")), ("agent_id", s("bo"))]);
    let reply = ok(
        &mut hub,
        "acquire_lock",
        vec![
            ("document_path", paths(&["src/a.rs", "src/b.rs", "src/c.rs"])),
            ("agent_id", s("ana")),
            ("best_effort", json::b(true)),
        ],
    );
    assert_eq!(reply.get("acquired"), Some(&json::b(false)), "not every path");
    assert_eq!(reply.get("mode").and_then(Value::as_str), Some("best_effort"));
    assert_eq!(strings(&reply, "acquired_paths"), vec!["src/a.rs", "src/c.rs"]);
    assert_eq!(strings(&reply, "failed_paths"), vec!["src/b.rs"]);
    assert_eq!(held_by(&mut hub, "ana"), vec!["src/a.rs", "src/c.rs"]);
}

#[test]
fn shared_and_force_work_per_path() {
    let dir = dir("shared");
    let mut hub = open(&dir);
    register(&mut hub, "ana");
    register(&mut hub, "bo");
    ok(&mut hub, "acquire_lock", vec![("document_path", at("doc.md")), ("agent_id", s("bo")), ("lock_type", s("shared"))]);
    let shared = ok(
        &mut hub,
        "acquire_lock",
        vec![("document_path", paths(&["doc.md", "x.md"])), ("agent_id", s("ana")), ("lock_type", s("shared"))],
    );
    assert_eq!(shared.get("acquired"), Some(&json::b(true)), "{}", json::dumps(&shared));
    ok(&mut hub, "acquire_lock", vec![("document_path", at("y.md")), ("agent_id", s("bo"))]);
    let forced = ok(
        &mut hub,
        "acquire_lock",
        vec![("document_path", paths(&["y.md", "z.md"])), ("agent_id", s("ana")), ("force", json::b(true))],
    );
    assert_eq!(forced.get("acquired"), Some(&json::b(true)), "{}", json::dumps(&forced));
    assert_eq!(held_by(&mut hub, "bo"), vec!["doc.md"]);
}

#[test]
fn a_list_is_checked_before_anything_is_taken() {
    let dir = dir("checks");
    let mut hub = open(&dir);
    register(&mut hub, "ana");
    let empty = hub.call("acquire_lock", &json::obj(vec![("document_path", json::arr(vec![])), ("agent_id", s("ana"))]));
    assert!(empty.unwrap_err().contains("empty list"));
    let region = hub.call(
        "acquire_lock",
        &json::obj(vec![
            ("document_path", paths(&["a.rs", "b.rs"])),
            ("agent_id", s("ana")),
            ("region_start", json::i(1)),
            ("region_end", json::i(5)),
        ]),
    );
    assert!(region.unwrap_err().contains("one path per call"));
    let bad = hub.call("acquire_lock", &json::obj(vec![("document_path", json::arr(vec![at("a.rs"), json::i(3)])), ("agent_id", s("ana"))]));
    assert!(bad.unwrap_err().contains("3 is not of type 'string'"));
    let many: Vec<String> = (0..501).map(|n| format!("/f{n}.rs")).collect();
    let too_many = hub.call(
        "acquire_lock",
        &json::obj(vec![("document_path", json::string_list(&many)), ("agent_id", s("ana"))]),
    );
    assert!(too_many.unwrap_err().contains("at most 500"));
    assert!(held_by(&mut hub, "ana").is_empty());
    // A list of one with a region is fine.
    let one = ok(
        &mut hub,
        "acquire_lock",
        vec![("document_path", paths(&["a.rs"])), ("agent_id", s("ana")), ("region_start", json::i(1)), ("region_end", json::i(5))],
    );
    assert_eq!(one.get("acquired"), Some(&json::b(true)), "{}", json::dumps(&one));
}

#[test]
fn retry_waits_for_the_whole_list() {
    let dir = dir("retry");
    let mut hub = open(&dir);
    register(&mut hub, "ana");
    register(&mut hub, "bo");
    ok(&mut hub, "acquire_lock", vec![("document_path", at("b.rs")), ("agent_id", s("bo")), ("ttl", json::f(0.4))]);
    let reply = ok(
        &mut hub,
        "acquire_lock",
        vec![
            ("document_path", paths(&["a.rs", "b.rs"])),
            ("agent_id", s("ana")),
            ("retry", json::b(true)),
            ("max_retries", json::i(20)),
            ("backoff_ms", json::f(50.0)),
            ("timeout_ms", json::f(5000.0)),
        ],
    );
    assert_eq!(reply.get("acquired"), Some(&json::b(true)), "{}", json::dumps(&reply));
    assert!(reply.get("attempts").and_then(Value::as_i64).unwrap() > 1);
}

#[test]
fn release_takes_a_list_and_says_why_a_path_was_not_released() {
    let dir = dir("release");
    let mut hub = open(&dir);
    register(&mut hub, "ana");
    register(&mut hub, "bo");
    ok(&mut hub, "acquire_lock", vec![("document_path", paths(&["a.rs", "b.rs"])), ("agent_id", s("ana"))]);
    ok(&mut hub, "acquire_lock", vec![("document_path", at("c.rs")), ("agent_id", s("bo"))]);
    let reply = ok(
        &mut hub,
        "release_lock",
        vec![("document_path", paths(&["a.rs", "b.rs", "c.rs", "d.rs"])), ("agent_id", s("ana"))],
    );
    assert_eq!(reply.get("released"), Some(&json::b(false)), "{}", json::dumps(&reply));
    assert_eq!(reply.get("count").and_then(Value::as_i64), Some(2));
    assert_eq!(strings(&reply, "released_paths"), vec!["a.rs", "b.rs"]);
    let reasons: Vec<(String, String)> = reply
        .get("failed")
        .and_then(Value::as_array)
        .unwrap()
        .iter()
        .map(|row| {
            (
                row.get("document_path").and_then(Value::as_str).unwrap().to_string(),
                row.get("reason").and_then(Value::as_str).unwrap().to_string(),
            )
        })
        .collect();
    assert_eq!(reasons, vec![("c.rs".into(), "not_owner".into()), ("d.rs".into(), "not_locked".into())]);
    assert!(held_by(&mut hub, "ana").is_empty());
    assert_eq!(held_by(&mut hub, "bo"), vec!["c.rs"]);
}

#[test]
fn one_path_keeps_the_0_8_0_reply() {
    let dir = dir("single");
    let mut hub = open(&dir);
    register(&mut hub, "ana");
    let reply = ok(&mut hub, "acquire_lock", vec![("document_path", at("a.rs")), ("agent_id", s("ana"))]);
    let keys: Vec<&str> = reply.as_object().unwrap().iter().map(|(key, _)| key.as_str()).collect();
    assert_eq!(keys, ["acquired", "document_path", "locked_by", "expires_at", "region_start", "region_end", "attempts"]);
    let released = ok(&mut hub, "release_lock", vec![("document_path", at("a.rs")), ("agent_id", s("ana"))]);
    assert_eq!(json::dumps(&released), r#"{"released": true, "count": 1}"#);
}

/// Engines on one store, each on its own thread, race for overlapping
/// lists in opposite orders. Every call is one transaction under the store
/// lock, so for each pair of racers exactly one gets both files and the
/// other holds nothing: no partial lists, no deadlock.
#[test]
fn concurrent_lists_are_all_or_nothing() {
    let dir = dir("race");
    {
        let mut hub = open(&dir);
        for n in 0..8 {
            register(&mut hub, &format!("racer-{n}"));
        }
    }
    let barrier = std::sync::Arc::new(std::sync::Barrier::new(8));
    let handles: Vec<_> = (0..8)
        .map(|n| {
            let dir = dir.clone();
            let barrier = barrier.clone();
            std::thread::spawn(move || {
                let mut engine = open(&dir);
                let agent = format!("racer-{n}");
                let own = format!("own-{n}.rs");
                let list = if n % 2 == 0 {
                    vec![at("shared.rs"), at(&own), at("common.rs")]
                } else {
                    vec![at(&own), at("common.rs"), at("shared.rs")]
                };
                barrier.wait();
                let reply = ok(&mut engine, "acquire_lock", vec![("document_path", json::arr(list)), ("agent_id", s(&agent))]);
                (agent, reply.get("acquired") == Some(&json::b(true)))
            })
        })
        .collect();
    let results: Vec<(String, bool)> = handles.into_iter().map(|h| h.join().unwrap()).collect();
    let winners: Vec<&String> = results.iter().filter(|(_, won)| *won).map(|(agent, _)| agent).collect();
    assert_eq!(winners.len(), 1, "{results:?}");
    let mut hub = open(&dir);
    for (agent, won) in &results {
        let held = held_by(&mut hub, agent);
        if *won {
            assert_eq!(held.len(), 3, "{agent}: {held:?}");
        } else {
            assert!(held.is_empty(), "{agent} kept part of a refused list: {held:?}");
        }
    }
}

#[test]
fn cli_takes_several_paths_before_the_agent() {
    let hub = Hub::new("b14-cli");
    hub.json(&["register", "ana"]);
    hub.json(&["register", "bo"]);
    hub.json(&["acquire-lock", "src/b.rs", "bo"]);
    let refused = hub.run(&["acquire-lock", "src/a.rs", "src/b.rs", "src/c.rs", "ana", "--ttl", "60", "-j"]);
    assert_eq!(refused.code, 4, "{}{}", refused.stdout, refused.stderr);
    let value = json::parse(&refused.stdout).unwrap();
    assert_eq!(strings(&value, "failed_paths"), vec!["src/b.rs"]);
    assert!(strings(&value, "acquired_paths").is_empty());
    let listed = hub.json(&["list-locks", "--agent-id", "ana"]);
    assert_eq!(listed.get("locks").and_then(Value::as_array).map(|rows| rows.len()), Some(0));
    let partial = hub.run(&["acquire-lock", "src/a.rs", "src/b.rs", "src/c.rs", "ana", "--best-effort", "-j"]);
    assert_eq!(partial.code, 4);
    let value = json::parse(&partial.stdout).unwrap();
    assert_eq!(strings(&value, "acquired_paths"), vec!["src/a.rs", "src/c.rs"]);
    // Readable output: one line per path.
    let prose = hub.run(&["release-lock", "src/a.rs", "src/c.rs", "ana"]);
    assert_eq!(prose.code, 0, "{}", prose.stderr);
    assert_eq!(prose.stdout, "RELEASED: src/a.rs\nRELEASED: src/c.rs\n");
    let taken = hub.run(&["acquire-lock", "src/a.rs", "src/c.rs", "ana"]);
    assert_eq!(taken.code, 0, "{}", taken.stderr);
    assert!(taken.stdout.starts_with("LOCKED: src/a.rs\nLOCKED: src/c.rs\n  Agent: ana\n"), "{}", taken.stdout);
    // One path: the 0.8.0 reply and flags in any place.
    let one = hub.json(&["acquire-lock", "src/d.rs", "--ttl", "30", "ana"]);
    assert_eq!(one.get("document_path").and_then(Value::as_str), Some("src/d.rs"));
    assert!(one.get("acquired_paths").is_none());
    let missing = hub.run(&["acquire-lock", "src/d.rs"]);
    assert_eq!(missing.code, 2);
    assert!(missing.stderr.ends_with("error: the following arguments are required: agent_id\n"), "{}", missing.stderr);
}

#[test]
fn register_returns_held_locks_and_unread_messages() {
    let dir = dir("register");
    let mut hub = open(&dir);
    let fresh = register(&mut hub, "ana");
    assert!(fresh.get("held_locks").is_none(), "a fresh registration replies as 0.8.0");
    assert!(fresh.get("unread_messages").is_none());
    register(&mut hub, "bo");
    ok(&mut hub, "acquire_lock", vec![("document_path", paths(&["a.rs", "b.rs"])), ("agent_id", s("ana"))]);
    ok(&mut hub, "acquire_lock", vec![("document_path", at("c.rs")), ("agent_id", s("bo"))]);
    for n in 0..12 {
        ok(
            &mut hub,
            "send_message",
            vec![("from_agent_id", s("bo")), ("to_agent_id", s("ana")), ("message_type", s("note")), ("payload", json::obj(vec![("n", json::i(n))]))],
        );
    }
    let back = register(&mut hub, "ana");
    let held: Vec<String> = back
        .get("held_locks")
        .and_then(Value::as_array)
        .unwrap()
        .iter()
        .map(|lock| lock.get("document_path").and_then(Value::as_str).unwrap().to_string())
        .collect();
    assert_eq!(held, vec!["a.rs", "b.rs"], "only ana's own locks");
    assert_eq!(back.get("unread_messages").and_then(Value::as_i64), Some(12));
    let shown = back.get("messages").and_then(Value::as_array).unwrap();
    assert_eq!(shown.len(), 10, "bounded");
    assert_eq!(shown[0].get("payload").and_then(|p| p.get("n")).and_then(Value::as_i64), Some(11), "newest first");
    // Showing them does not mark them read.
    let inbox = ok(&mut hub, "manage_messages", vec![("action", s("get")), ("agent_id", s("ana")), ("unread_only", json::b(true))]);
    assert_eq!(inbox.get("count").and_then(Value::as_i64), Some(12));
}

#[test]
fn cli_register_after_a_restart_shows_locks_and_messages() {
    let hub = Hub::new("b14-reg");
    hub.json(&["register", "ana"]);
    hub.json(&["register", "bo"]);
    hub.json(&["acquire-lock", "a.rs", "b.rs", "ana"]);
    hub.json(&["send-message", "bo", "ana", "ping", "--payload", "hi"]);
    hub.json(&["deregister", "bo"]);
    // Another process: the record is still active under the old pid.
    let again = hub.json(&["register", "ana"]);
    assert_eq!(again.get("reason").and_then(Value::as_str), Some("collision"));
    assert_eq!(again.get("held_locks").and_then(Value::as_array).map(|rows| rows.len()), Some(2));
    assert_eq!(again.get("unread_messages").and_then(Value::as_i64), Some(1));
    // A clean restart: deregister released the locks; the message is still there.
    hub.json(&["deregister", "ana"]);
    let back = hub.json(&["register", "ana"]);
    assert_eq!(back.get("registered"), None);
    assert!(back.get("held_locks").is_none());
    assert_eq!(back.get("unread_messages").and_then(Value::as_i64), Some(1));
    let prose = hub.run(&["register", "bo"]);
    assert!(!prose.stdout.contains("Unread"), "{}", prose.stdout);
}
