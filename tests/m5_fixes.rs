//! 1.2.0 fixes from Specula's M5 lanes, each listed in MIGRATION.md: locks
//! that lapse are reported as expired, heartbeats renew held locks, a quiet
//! agent comes back on its next heartbeat, waits are capped and never hold
//! up other calls, and the store lock cannot hang a call.

mod common;

use common::{tool_text, Hub, Server};
use coordinationhub::json::{self, Value};
use std::io::{Read, Write};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

fn reply(result: &Value) -> Value {
    let (text, is_error) = tool_text(result);
    assert!(!is_error, "tool error: {text}");
    json::parse(&text).unwrap_or_else(|err| panic!("{err}: {text}"))
}

fn args(pairs: Vec<(&str, Value)>) -> Value {
    json::obj(pairs)
}

fn reg(id: &str) -> (&'static str, Value) {
    ("register_agent", args(vec![("agent_id", json::s(id))]))
}

fn lock(path: &str, agent: &str, ttl: f64) -> (&'static str, Value) {
    (
        "acquire_lock",
        args(vec![("document_path", json::s(path)), ("agent_id", json::s(agent)), ("ttl", json::f(ttl))]),
    )
}

fn release(path: &str, agent: &str) -> (&'static str, Value) {
    ("release_lock", args(vec![("document_path", json::s(path)), ("agent_id", json::s(agent))]))
}

fn beat(agent: &str) -> (&'static str, Value) {
    ("heartbeat", args(vec![("agent_id", json::s(agent))]))
}

fn str_of<'a>(value: &'a Value, key: &str) -> &'a str {
    value.get(key).and_then(Value::as_str).unwrap_or("")
}

fn paths(value: &Value, key: &str) -> Vec<String> {
    value
        .get(key)
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                // Relative paths resolve against the server's directory;
                // the file name is enough here.
                .map(|item| str_of(item, "document_path").rsplit('/').next().unwrap_or("").to_string())
                .collect()
        })
        .unwrap_or_default()
}

fn call(server: &mut Server, step: (&str, Value)) -> Value {
    reply(&server.call(step.0, step.1))
}

/// Inputguard's repro with a short TTL: acquire, wait past the TTL,
/// release. 1.1 answered `not_locked` or `not_owner`.
#[test]
fn release_after_the_ttl_says_expired() {
    let hub = Hub::new("expired-release");
    let mut s = Server::start(&hub, &[]);
    call(&mut s, reg("guard"));
    call(&mut s, reg("other"));
    call(&mut s, lock("src/a.rs", "guard", 0.4));
    call(&mut s, lock("src/b.rs", "guard", 0.4));
    call(&mut s, lock("src/c.rs", "guard", 0.4));
    std::thread::sleep(Duration::from_millis(600));
    // The row is still there: the release reports when it ran out.
    let first = call(&mut s, release("src/a.rs", "guard"));
    assert_eq!(first.get("released"), Some(&json::b(false)));
    assert_eq!(str_of(&first, "reason"), "expired");
    assert!(first.get("expired_at").and_then(Value::as_f64).is_some());
    assert!(str_of(&first, "error").contains("acquire_lock again"), "{first:?}");
    // Told once: a second release is an ordinary not_locked.
    assert_eq!(str_of(&call(&mut s, release("src/a.rs", "guard")), "reason"), "not_locked");
    // The reaper removed the row first: still expired, not not_locked.
    call(&mut s, ("admin_locks", args(vec![("action", json::s("reap_expired"))])));
    let reaped = call(&mut s, release("src/b.rs", "guard"));
    assert_eq!(str_of(&reaped, "reason"), "expired", "{reaped:?}");
    // Another agent took the file after the lapse: expired, naming the
    // new holder, instead of not_owner.
    call(&mut s, lock("src/c.rs", "other", 600.0));
    let taken = call(&mut s, release("src/c.rs", "guard"));
    assert_eq!(str_of(&taken, "reason"), "expired", "{taken:?}");
    assert_eq!(str_of(&taken, "locked_by"), "other");
    assert!(str_of(&taken, "error").contains("other holds it now"));
    // A lock that never existed is still not_locked / not_owner.
    assert_eq!(str_of(&call(&mut s, release("src/none.rs", "guard")), "reason"), "not_locked");
    assert_eq!(str_of(&call(&mut s, release("src/c.rs", "guard")), "reason"), "not_owner");
    s.stop();
}

#[test]
fn refresh_after_the_ttl_says_expired() {
    let hub = Hub::new("expired-refresh");
    let mut s = Server::start(&hub, &[]);
    call(&mut s, reg("r"));
    call(&mut s, lock("x.rs", "r", 0.3));
    std::thread::sleep(Duration::from_millis(500));
    let refreshed = call(
        &mut s,
        ("refresh_lock", args(vec![("document_path", json::s("x.rs")), ("agent_id", json::s("r"))])),
    );
    assert_eq!(refreshed.get("refreshed"), Some(&json::b(false)));
    assert_eq!(str_of(&refreshed, "reason"), "expired");
    s.stop();
}

/// One lane got `locks_released: 1` after taking about eight: the others
/// had run out and the reaper had dropped them without a word. Now the
/// live ones are counted and the lapsed ones listed.
#[test]
fn deregister_reports_released_and_expired_locks_apart() {
    let hub = Hub::new("expired-deregister");
    let mut s = Server::start(&hub, &[]);
    call(&mut s, reg("lane"));
    for path in ["a.rs", "b.rs", "c.rs"] {
        call(&mut s, lock(path, "lane", 0.3));
    }
    call(&mut s, lock("live.rs", "lane", 600.0));
    std::thread::sleep(Duration::from_millis(500));
    // The reaper drops two rows; the third is still in the table.
    call(&mut s, ("admin_locks", args(vec![("action", json::s("reap_expired"))])));
    call(&mut s, lock("d.rs", "lane", 0.2));
    std::thread::sleep(Duration::from_millis(300));
    let gone = call(&mut s, ("deregister_agent", args(vec![("agent_id", json::s("lane"))])));
    assert_eq!(gone.get("locks_released"), Some(&json::i(1)), "{gone:?}");
    let mut expired = paths(&gone, "locks_expired");
    expired.sort();
    assert_eq!(expired, ["a.rs", "b.rs", "c.rs", "d.rs"]);
    let status = call(&mut s, ("list_locks", args(vec![])));
    assert_eq!(status.get("count"), Some(&json::i(0)));
    s.stop();
}

#[test]
fn release_by_agent_lists_expired_locks() {
    let hub = Hub::new("expired-admin");
    let replies = hub.mcp(&[
        reg("w"),
        lock("a.rs", "w", 600.0),
        lock("b.rs", "w", -1.0),
        ("admin_locks", args(vec![("action", json::s("release_by_agent")), ("agent_id", json::s("w"))])),
        ("admin_locks", args(vec![("action", json::s("release_by_agent")), ("agent_id", json::s("w"))])),
    ]);
    let first = reply(&replies[3]);
    assert_eq!(first.get("released"), Some(&json::i(1)));
    assert_eq!(paths(&first, "expired"), ["b.rs"]);
    // Reported once; with nothing left the reply is 0.8.0's.
    assert_eq!(tool_text(&replies[4]).0, r#"{"released":0}"#);
}

/// Heartbeats renew held locks once less than half the TTL is left, so a
/// lane that keeps heartbeating keeps its locks through a long gate.
#[test]
fn heartbeat_renews_held_locks() {
    let hub = Hub::new("renew");
    let mut s = Server::start(&hub, &[]);
    call(&mut s, reg("gate"));
    call(&mut s, lock("long.rs", "gate", 1.0));
    call(&mut s, lock("fresh.rs", "gate", 600.0));
    // Right after acquiring, nothing needs renewing.
    let early = call(&mut s, beat("gate"));
    assert!(early.get("locks_renewed").is_none(), "{early:?}");
    for _ in 0..4 {
        std::thread::sleep(Duration::from_millis(600));
        let beaten = call(&mut s, beat("gate"));
        assert_eq!(beaten.get("locks_renewed"), Some(&json::i(1)), "{beaten:?}");
        // A 1 s lock runs out before the next heartbeat (30 s) is due.
        assert_eq!(paths(&beaten, "locks_expiring"), ["long.rs"]);
    }
    // 2.4 s after taking a 1 s lock it is still held.
    let held = call(&mut s, ("get_lock_status", args(vec![("document_path", json::s("long.rs"))])));
    assert_eq!(held.get("locked"), Some(&json::b(true)));
    s.stop();
}

#[test]
fn heartbeat_and_notifications_warn_about_lapsed_and_expiring_locks() {
    let hub = Hub::new("warn");
    let mut s = Server::start(&hub, &[]);
    call(&mut s, reg("w"));
    call(&mut s, lock("soon.rs", "w", 2.0));
    call(&mut s, lock("gone.rs", "w", 0.3));
    std::thread::sleep(Duration::from_millis(1100));
    // get_notifications with agent_id names the caller's lock under half
    // its TTL and the one that ran out.
    let notes = call(&mut s, ("get_notifications", args(vec![("agent_id", json::s("w")), ("limit", json::i(1))])));
    assert_eq!(paths(&notes, "locks_expiring"), ["soon.rs"], "{notes:?}");
    assert_eq!(paths(&notes, "locks_expired"), ["gone.rs"], "{notes:?}");
    // Without agent_id (exclude_agent only filters) the reply is 0.8.0's.
    for extra in [vec![], vec![("exclude_agent", json::s("w"))]] {
        let mut pairs = vec![("limit", json::i(1))];
        pairs.extend(extra);
        let plain = call(&mut s, ("get_notifications", args(pairs)));
        assert!(plain.get("locks_expired").is_none() && plain.get("locks_expiring").is_none(), "{plain:?}");
    }
    let beaten = call(&mut s, beat("w"));
    assert_eq!(paths(&beaten, "locks_expired"), ["gone.rs"], "{beaten:?}");
    assert!(str_of(&beaten, "hint").contains("acquire_lock again"));
    assert_eq!(beaten.get("locks_renewed"), Some(&json::i(1)));
    // Taking the file again clears the warning.
    call(&mut s, lock("gone.rs", "w", 600.0));
    let after = call(&mut s, beat("w"));
    assert!(after.get("locks_expired").is_none(), "{after:?}");
    s.stop();
}

/// The real reaper (housekeeping) drops an expired lock; the holder is
/// still told it expired.
#[test]
fn housekeeping_reaped_lock_still_reports_expired() {
    let hub = Hub::new("house");
    let mut s = Server::start(
        &hub,
        &[("COORDINATIONHUB_HOUSEKEEPING", "1"), ("COORDINATIONHUB_HOUSEKEEPING_AGENTS_INTERVAL_S", "1")],
    );
    call(&mut s, reg("h"));
    call(&mut s, lock("h.rs", "h", 0.3));
    std::thread::sleep(Duration::from_millis(2500));
    let locks = call(&mut s, ("list_locks", args(vec![])));
    assert_eq!(locks.get("count"), Some(&json::i(0)));
    let released = call(&mut s, release("h.rs", "h"));
    assert_eq!(str_of(&released, "reason"), "expired", "{released:?}");
    s.stop();
}

/// The l10n lane saw `list_agents` return `[]` while lanes worked: each
/// had been reaped as stale during a long build, and every later heartbeat
/// was refused with `agent_stopped`.
#[test]
fn a_reaped_agent_comes_back_on_heartbeat_and_list_agents_says_who_is_quiet() {
    let hub = Hub::new("quiet");
    let mut s = Server::start(&hub, &[]);
    call(&mut s, reg("busy"));
    call(&mut s, reg("done"));
    call(&mut s, ("deregister_agent", args(vec![("agent_id", json::s("done"))])));
    std::thread::sleep(Duration::from_millis(400));
    call(&mut s, ("admin_locks", args(vec![("action", json::s("reap_stale")), ("timeout", json::f(0.2))])));
    let listed = call(&mut s, ("list_agents", args(vec![])));
    assert_eq!(listed.get("agents"), Some(&json::arr(vec![])));
    assert_eq!(listed.get("stale_hidden"), Some(&json::arr(vec![json::s("busy")])));
    assert!(str_of(&listed, "hint").contains("active_only=false"), "{listed:?}");
    let back = call(&mut s, beat("busy"));
    assert_eq!(back.get("updated"), Some(&json::b(true)), "{back:?}");
    assert_eq!(back.get("revived"), Some(&json::b(true)));
    let listed = call(&mut s, ("list_agents", args(vec![])));
    let ids: Vec<&str> = listed
        .get("agents")
        .and_then(Value::as_array)
        .unwrap()
        .iter()
        .map(|row| str_of(row, "agent_id"))
        .collect();
    assert_eq!(ids, ["busy"]);
    assert!(listed.get("stale_hidden").is_none());
    // A deregistered agent stays stopped.
    let stopped = call(&mut s, beat("done"));
    assert_eq!(str_of(&stopped, "reason"), "agent_stopped");
    // Active but silent past stale_timeout counts as quiet too.
    std::thread::sleep(Duration::from_millis(300));
    let listed = call(&mut s, ("list_agents", args(vec![("stale_timeout", json::f(0.1))])));
    assert_eq!(listed.get("stale_hidden"), Some(&json::arr(vec![json::s("busy")])));
    s.stop();
}

/// One server answered calls in order, so a long poll held up every other
/// call from every agent on it (heartbeat and update_task_status hung).
#[test]
fn a_long_poll_does_not_hold_up_other_calls() {
    let hub = Hub::new("concurrent");
    let mut s = Server::start(&hub, &[]);
    call(&mut s, reg("p"));
    call(
        &mut s,
        ("create_task", args(vec![("task_id", json::s("t")), ("parent_agent_id", json::s("p")), ("description", json::s("d"))])),
    );
    let started = Instant::now();
    let polls: Vec<i64> = (0..3)
        .map(|_| s.send("get_notifications", args(vec![("timeout_s", json::f(3.0)), ("agent_id", json::s("p"))])))
        .collect();
    let waiting = s.send("wait_for_task", args(vec![("task_id", json::s("t")), ("timeout_s", json::f(3.0))]));
    let plain = Instant::now();
    call(&mut s, beat("p"));
    call(
        &mut s,
        ("update_task_status", args(vec![("task_id", json::s("t")), ("status", json::s("completed")), ("agent_id", json::s("p"))])),
    );
    call(&mut s, ("list_agents", args(vec![])));
    assert!(plain.elapsed() < Duration::from_secs(2), "plain calls took {:?}", plain.elapsed());
    // The task wait sees the update made while it waited.
    let done = reply(&s.wait_for(waiting, Duration::from_secs(10)).unwrap());
    assert_eq!(str_of(&done, "status"), "completed");
    for id in polls {
        let poll = reply(&s.wait_for(id, Duration::from_secs(10)).unwrap());
        assert!(poll.get("timed_out").is_some(), "{poll:?}");
    }
    assert!(started.elapsed() < Duration::from_secs(8));
    s.stop();
}

/// Every wait is capped at 600 s (lower through COORDINATIONHUB_MAX_WAIT_S)
/// and answers `timed_out` instead of outliving the client.
#[test]
fn waits_are_capped_and_answer_timed_out() {
    let hub = Hub::new("cap");
    let mut s = Server::start(&hub, &[("COORDINATIONHUB_MAX_WAIT_S", "1")]);
    call(&mut s, reg("c"));
    call(
        &mut s,
        ("create_task", args(vec![("task_id", json::s("t")), ("parent_agent_id", json::s("c")), ("description", json::s("d"))])),
    );
    let started = Instant::now();
    let task = call(&mut s, ("wait_for_task", args(vec![("task_id", json::s("t")), ("timeout_s", json::f(1800.0))])));
    assert_eq!(task.get("timed_out"), Some(&json::b(true)));
    assert_eq!(task.get("wait_capped_s"), Some(&json::f(1.0)));
    let notes = call(&mut s, ("get_notifications", args(vec![("timeout_s", json::f(1800.0))])));
    assert_eq!(notes.get("timed_out"), Some(&json::b(true)));
    assert_eq!(notes.get("wait_capped_s"), Some(&json::f(1.0)));
    let agent = call(&mut s, ("await_agent", args(vec![("agent_id", json::s("c")), ("timeout_s", json::f(7200.0))])));
    assert_eq!(str_of(&agent, "status"), "timeout");
    let spawn = call(
        &mut s,
        ("await_subagent_registration", args(vec![("parent_agent_id", json::s("c")), ("timeout", json::f(5000.0))])),
    );
    assert_eq!(spawn.get("timed_out"), Some(&json::b(true)));
    assert!(started.elapsed() < Duration::from_secs(10), "{:?}", started.elapsed());
    // Under the cap nothing changes.
    let short = call(&mut s, ("wait_for_task", args(vec![("task_id", json::s("t")), ("timeout_s", json::f(0.2))])));
    assert!(short.get("wait_capped_s").is_none());
    s.stop();
}

/// A cancelled wait stops and sends no response; the server goes on.
#[test]
fn a_cancelled_wait_stops_without_a_reply() {
    let hub = Hub::new("cancel");
    let mut s = Server::start(&hub, &[]);
    call(&mut s, reg("k"));
    let poll = s.send("get_notifications", args(vec![("timeout_s", json::f(60.0))]));
    std::thread::sleep(Duration::from_millis(200));
    s.notify(&format!(
        r#"{{"jsonrpc":"2.0","method":"notifications/cancelled","params":{{"requestId":{poll},"reason":"user"}}}}"#
    ));
    assert!(s.wait_for(poll, Duration::from_millis(800)).is_none());
    call(&mut s, beat("k"));
    let stopping = Instant::now();
    s.stop();
    assert!(stopping.elapsed() < Duration::from_secs(5), "server lingered {:?}", stopping.elapsed());
}

/// A process that never lets go of the store lock fails a call with an
/// actionable error after COORDINATIONHUB_LOCK_WAIT_S instead of hanging.
#[test]
fn a_held_store_lock_fails_the_call_instead_of_hanging() {
    let hub = Hub::new("busy");
    hub.run(&["register", "solo"]);
    let held = coordinationhub::util::FileLock::exclusive(&hub.store.join("coordination.lock")).unwrap();
    let started = Instant::now();
    let out = Command::new(env!("CARGO_BIN_EXE_coordinationhub"))
        .args(["heartbeat", "solo", "--storage-dir"])
        .arg(&hub.store)
        .arg("--project-root")
        .arg(&hub.project)
        .env("COORDINATIONHUB_HOUSEKEEPING", "0")
        .env("COORDINATIONHUB_LOCK_WAIT_S", "0.5")
        .output()
        .unwrap();
    let text = format!("{}{}", String::from_utf8_lossy(&out.stdout), String::from_utf8_lossy(&out.stderr));
    assert!(started.elapsed() < Duration::from_secs(5));
    assert!(text.contains("store busy"), "{text}");
    drop(held);
    assert_eq!(hub.run(&["heartbeat", "solo"]).code, 0);
}

/// A call that cannot read the journal lets go of the store lock at once,
/// instead of holding it until that server's next call.
#[test]
fn a_failed_read_releases_the_store_lock() {
    let hub = Hub::new("corrupt");
    let mut s = Server::start(&hub, &[]);
    call(&mut s, reg("x"));
    let journal = hub.store.join("coordination.jsonl");
    let mut file = std::fs::OpenOptions::new().append(true).open(&journal).unwrap();
    file.write_all(b"{not json\n").unwrap();
    drop(file);
    let (text, is_error) = tool_text(&s.call("heartbeat", args(vec![("agent_id", json::s("x"))])));
    assert!(is_error, "{text}");
    let started = Instant::now();
    let lock = coordinationhub::util::FileLock::exclusive(&hub.store.join("coordination.lock"));
    assert!(lock.is_ok() && started.elapsed() < Duration::from_secs(2));
    drop(lock);
    s.stop();
}

/// The HTTP server runs a waiting /call beside other calls too.
#[test]
fn http_waits_do_not_hold_up_other_calls() {
    let hub = Hub::new("http");
    let mut child = Command::new(env!("CARGO_BIN_EXE_coordinationhub"))
        .args(["serve", "--port", "9874", "--no-auth", "--storage-dir"])
        .arg(&hub.store)
        .arg("--project-root")
        .arg(&hub.project)
        .env("COORDINATIONHUB_HOUSEKEEPING", "0")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .unwrap();
    let post = |body: &str| -> String {
        let mut sock = None;
        for _ in 0..50 {
            if let Ok(stream) = std::net::TcpStream::connect("127.0.0.1:9874") {
                sock = Some(stream);
                break;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        let mut sock = sock.expect("server did not start");
        let request = format!(
            "POST /call HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
            body.len()
        );
        sock.write_all(request.as_bytes()).unwrap();
        sock.set_read_timeout(Some(Duration::from_secs(10))).unwrap();
        let mut out = String::new();
        let _ = sock.read_to_string(&mut out);
        out
    };
    post(r#"{"tool":"register_agent","arguments":{"agent_id":"h"}}"#);
    let waiter = std::thread::spawn(move || {
        let mut sock = std::net::TcpStream::connect("127.0.0.1:9874").unwrap();
        let body = r#"{"tool":"get_notifications","arguments":{"timeout_s":3}}"#;
        let request = format!("POST /call HTTP/1.1\r\nHost: x\r\nContent-Length: {}\r\n\r\n{body}", body.len());
        sock.write_all(request.as_bytes()).unwrap();
        let mut out = String::new();
        let _ = sock.read_to_string(&mut out);
        out
    });
    std::thread::sleep(Duration::from_millis(300));
    let started = Instant::now();
    let answer = post(r#"{"tool":"heartbeat","arguments":{"agent_id":"h"}}"#);
    assert!(answer.contains("\"updated\": true"), "{answer}");
    assert!(started.elapsed() < Duration::from_secs(2), "{:?}", started.elapsed());
    assert!(waiter.join().unwrap().contains("timed_out"));
    let _ = child.kill();
    let _ = child.wait();
}
