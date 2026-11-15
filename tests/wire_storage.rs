//! 1.3.0, wire and storage: numbers that are not finite, nesting depth,
//! strict JSON over MCP, a torn journal tail, retention and compaction.
//! Each is listed in MIGRATION.md.

mod common;

use common::Hub;
use coordinationhub::json::{self, Value};
use coordinationhub::{act_identity, act_lock, house, Engine};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::{channel, Receiver};
use std::time::Duration;

/// A `serve-mcp` child that takes raw lines, so a test can send text that
/// is not valid JSON and read whole JSON-RPC messages back.
struct Raw {
    child: Child,
    stdin: Option<ChildStdin>,
    lines: Receiver<String>,
    next: i64,
}

impl Raw {
    fn start(hub: &Hub, env: &[(&str, &str)]) -> Raw {
        let mut command = Command::new(env!("CARGO_BIN_EXE_coordinationhub"));
        command
            .args(["serve-mcp", "--storage-dir"])
            .arg(&hub.store)
            .arg("--project-root")
            .arg(&hub.project)
            .env("COORDINATIONHUB_HOUSEKEEPING", "0")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        for (key, value) in env {
            command.env(key, value);
        }
        let mut child = command.spawn().unwrap();
        let stdout = child.stdout.take().unwrap();
        let (sender, lines) = channel();
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines() {
                let Ok(line) = line else { break };
                if sender.send(line).is_err() {
                    break;
                }
            }
        });
        let stdin = child.stdin.take();
        let mut raw = Raw { child, stdin, lines, next: 1 };
        raw.send(r#"{"jsonrpc":"2.0","id":0,"method":"initialize","params":{}}"#);
        raw.recv();
        raw
    }

    fn send(&mut self, line: &str) {
        let stdin = self.stdin.as_mut().unwrap();
        stdin.write_all(line.as_bytes()).unwrap();
        stdin.write_all(b"\n").unwrap();
        stdin.flush().unwrap();
    }

    fn send_bytes(&mut self, bytes: &[u8]) {
        let stdin = self.stdin.as_mut().unwrap();
        stdin.write_all(bytes).unwrap();
        stdin.flush().unwrap();
    }

    /// The next message from the server, parsed.
    fn recv(&mut self) -> Value {
        let line = self
            .lines
            .recv_timeout(Duration::from_secs(60))
            .expect("the server stopped answering");
        json::parse(&line).unwrap_or_else(|err| panic!("reply is not strict JSON ({err}): {line}"))
    }

    /// Sends a tools/call whose arguments are given as JSON text, and
    /// returns the whole reply message.
    fn call_text(&mut self, tool: &str, arguments: &str) -> Value {
        let id = self.next;
        self.next += 1;
        self.send(&format!(
            r#"{{"jsonrpc":"2.0","id":{id},"method":"tools/call","params":{{"name":"{tool}","arguments":{arguments}}}}}"#
        ));
        self.recv()
    }

    /// A tools/call that must succeed: its reply text, parsed.
    fn ok(&mut self, tool: &str, arguments: &str) -> Value {
        let message = self.call_text(tool, arguments);
        let (text, is_error) = tool_text(&message);
        assert!(!is_error, "{tool} failed: {text}");
        json::parse(&text).unwrap_or_else(|err| panic!("{err}: {text}"))
    }

    fn alive(&mut self) -> bool {
        matches!(self.child.try_wait(), Ok(None))
    }
}

impl Drop for Raw {
    fn drop(&mut self) {
        drop(self.stdin.take());
        let _ = self.child.wait();
    }
}

/// The text and isError flag of a tools/call reply message.
fn tool_text(message: &Value) -> (String, bool) {
    let result = message.get("result").unwrap_or_else(|| panic!("no result in {}", json::dumps(message)));
    common::tool_text(result)
}

/// The code and message of a JSON-RPC error reply.
fn rpc_error(message: &Value) -> (i64, String) {
    let error = message.get("error").unwrap_or_else(|| panic!("no error in {}", json::dumps(message)));
    (
        error.get("code").and_then(Value::as_i64).unwrap_or(0),
        error.get("message").and_then(Value::as_str).unwrap_or("").to_string(),
    )
}

/// Runs the CLI on `hub` with extra environment variables.
fn cli(hub: &Hub, args: &[&str], env: &[(&str, &str)]) -> common::Output {
    let mut command = Command::new(env!("CARGO_BIN_EXE_coordinationhub"));
    command
        .args(args)
        .arg("--storage-dir")
        .arg(&hub.store)
        .arg("--project-root")
        .arg(&hub.project)
        .current_dir(&hub.project)
        .env("COORDINATIONHUB_HOUSEKEEPING", "0");
    for (key, value) in env {
        command.env(key, value);
    }
    let out = command.output().unwrap();
    common::Output {
        code: out.status.code().unwrap_or(-1),
        stdout: String::from_utf8_lossy(&out.stdout).into_owned(),
        stderr: String::from_utf8_lossy(&out.stderr).into_owned(),
    }
}

fn journal(hub: &Hub) -> PathBuf {
    hub.store.join("coordination.jsonl")
}

fn journal_text(hub: &Hub) -> String {
    String::from_utf8(std::fs::read(journal(hub)).unwrap()).expect("the journal is UTF-8")
}

/// Every line of the journal is strict JSON and the file ends on a line
/// boundary.
fn assert_strict(hub: &Hub) {
    let text = journal_text(hub);
    assert!(text.is_empty() || text.ends_with('\n'), "journal does not end with a line feed");
    for (index, line) in text.lines().enumerate() {
        if let Err(err) = json::parse(line) {
            panic!("journal line {} is not strict JSON ({err}): {line}", index + 1);
        }
    }
}

fn torn_files(hub: &Hub) -> Vec<PathBuf> {
    coordinationhub::db::torn_files(&hub.store)
}

fn append(path: &Path, bytes: &[u8]) {
    let mut file = std::fs::OpenOptions::new().append(true).open(path).unwrap();
    file.write_all(bytes).unwrap();
}

// ---------------------------------------------------------------- A1

/// 1.2.0 accepted `"ttl": 1e400`, answered with `"expires_at":Infinity` and
/// wrote `Infinity` into the journal, after which nothing could read it.
#[test]
fn a_ttl_that_is_not_finite_never_reaches_the_journal() {
    let hub = Hub::new("nonfinite");
    let mut s = Raw::start(&hub, &[]);
    s.ok("register_agent", r#"{"agent_id":"a"}"#);
    // 1e400 does not fit a double: the request is refused as JSON.
    let reply = s.call_text("acquire_lock", r#"{"document_path":"x.txt","agent_id":"a","ttl":1e400}"#);
    let (code, message) = rpc_error(&reply);
    assert_eq!(code, -32700);
    assert!(message.contains("too large for a 64-bit float"), "{message}");
    // The bare words are refused too.
    for word in ["Infinity", "-Infinity", "NaN"] {
        let reply = s.call_text("acquire_lock", &format!(r#"{{"document_path":"x.txt","agent_id":"a","ttl":{word}}}"#));
        let (code, message) = rpc_error(&reply);
        assert_eq!(code, -32700, "{word}");
        assert!(message.contains("is not a JSON number"), "{message}");
    }
    // A finite number past the bound is a tool error that names the field
    // and the bound.
    let reply = s.call_text("acquire_lock", r#"{"document_path":"x.txt","agent_id":"a","ttl":1e300}"#);
    let (text, is_error) = tool_text(&reply);
    assert!(is_error, "{text}");
    assert!(text.contains("ttl must be a finite number between -1e15 and 1e15 (got 1e+300)"), "{text}");
    // Nested values are checked as well, with their path.
    let reply = s.call_text(
        "send_message",
        r#"{"from_agent_id":"a","to_agent_id":"a","message_type":"note","payload":{"sizes":[1,2e200]}}"#,
    );
    let (text, is_error) = tool_text(&reply);
    assert!(is_error && text.contains("payload.sizes[1] must be a finite number"), "{text}");
    // The bound itself is accepted and the sum stays finite.
    let locked = s.ok("acquire_lock", r#"{"document_path":"x.txt","agent_id":"a","ttl":1e15}"#);
    assert_eq!(locked.get("acquired"), Some(&json::b(true)));
    let expires = locked.get("expires_at").and_then(Value::as_f64).unwrap();
    assert!(expires.is_finite() && expires > 1e15, "{expires}");
    drop(s);
    assert!(!journal_text(&hub).contains("Infinity") && !journal_text(&hub).contains("NaN"));
    assert_strict(&hub);
    // A fresh process still reads the store.
    assert_eq!(hub.run(&["status"]).code, 0);
}

/// argparse takes `--ttl inf` and `--ttl nan`; the CLI path is checked too.
#[test]
fn the_cli_refuses_a_ttl_that_is_not_finite() {
    let hub = Hub::new("nonfinite-cli");
    assert_eq!(hub.run(&["register", "a"]).code, 0);
    for ttl in ["inf", "nan", "1e400", "1e300"] {
        let out = hub.run(&["acquire-lock", "x.txt", "a", "--ttl", ttl]);
        assert_ne!(out.code, 0, "--ttl {ttl}");
        assert!(out.stderr.contains("ttl must be a finite number between -1e15 and 1e15"), "{ttl}: {}", out.stderr);
    }
    assert_strict(&hub);
    assert_eq!(hub.json(&["list-locks"]).get("count"), Some(&json::i(0)));
}

/// The journal the released 1.2.0 binary left after `"ttl": 1e400` (with
/// two more rows for `-Infinity` and `NaN`). Every 1.2.0 command on it
/// failed with `Error: unexpected JSON byte I`.
const BRICKED: &str = r#"{"t":"put","table":"agents","row":{"agent_id": "root", "parent_id": null, "worktree_root": "/work/p2", "pid": 44960, "started_at": 1794731900.5540824, "last_heartbeat": 1794731900.5540824, "status": "active", "raw_ide_id": null, "ide_vendor": null, "stop_requested_at": null}}
{"t":"put","table":"hub_events","row":{"id": 1, "topic": "agent.registered", "payload": "{\"agent_id\": \"root\", \"parent_id\": null}", "created_at": 1794731900.554089}}
{"t":"ids","u":{"locks": 0, "conflicts": 0, "notifications": 0, "messages": 0, "handoffs": 0, "failures": 0, "dependencies": 0, "broadcasts": 0, "claims": 0, "claim_events": 0, "hub_events": 1}}
{"t":"put","table":"locks","row":{"id": 1, "document_path": "x.txt", "locked_by": "root", "locked_at": 1794731900.6022453, "lock_ttl": Infinity, "lock_type": "exclusive", "region_start": null, "region_end": null, "worktree_root": "/work/p2"}}
{"t":"put","table":"locks","row":{"id": 2, "document_path": "y.txt", "locked_by": "root", "locked_at": 1794731900.6022453, "lock_ttl": -Infinity, "lock_type": "exclusive", "region_start": null, "region_end": null, "worktree_root": "/work/p2"}}
{"t":"put","table":"leases","row":{"lease_name": "side", "holder_id": "root", "acquired_at": 1794731900.6, "ttl": NaN, "expires_at": NaN}}
{"t":"put","table":"notifications","row":{"id": 1, "document_path": "x.txt", "change_type": "locked", "agent_id": "root", "worktree_root": "/work/p2", "created_at": 1794731900.6022532}}
{"t":"put","table":"hub_events","row":{"id": 2, "topic": "lock.acquired", "payload": "{\"document_path\": \"x.txt\", \"agent_id\": \"root\", \"lock_type\": \"exclusive\", \"region_start\": null, \"region_end\": null}", "created_at": 1794731900.602265}}
{"t":"ids","u":{"locks": 2, "conflicts": 0, "notifications": 1, "messages": 0, "handoffs": 0, "failures": 0, "dependencies": 0, "broadcasts": 0, "claims": 0, "claim_events": 0, "hub_events": 2}}
"#;

#[test]
fn a_store_bricked_by_1_2_0_opens_with_no_hand_edit() {
    let hub = Hub::new("bricked");
    std::fs::write(journal(&hub), BRICKED).unwrap();
    // A read-only command is enough: it opens, repairs and rewrites.
    let first = hub.run(&["status"]);
    assert_eq!(first.code, 0, "{}", first.stderr);
    assert!(first.stdout.contains("Registered Agents: 1"), "{}", first.stdout);
    assert!(first.stderr.contains("Infinity or NaN") && first.stderr.contains("rewritten"), "{}", first.stderr);
    let text = journal_text(&hub);
    assert!(!text.contains("Infinity") && !text.contains("NaN"), "{text}");
    assert_strict(&hub);
    // The lock that had an infinite TTL is still held, with a large finite
    // one; the row count did not change.
    let locks = hub.json(&["list-locks"]);
    let rows = locks.get("locks").and_then(Value::as_array).unwrap();
    let held = rows
        .iter()
        .find(|row| row.get("document_path").and_then(Value::as_str) == Some("x.txt"))
        .expect("x.txt is still locked");
    let expires = held.get("expires_at").and_then(Value::as_f64).unwrap();
    assert!(expires.is_finite() && expires > 1e15, "{expires}");
    // The repair is said once, and later writes keep the file strict.
    let second = hub.run(&["register", "second"]);
    assert_eq!(second.code, 0, "{}", second.stderr);
    assert!(!second.stderr.contains("Infinity"), "{}", second.stderr);
    assert_eq!(hub.run(&["acquire-lock", "z.txt", "second"]).code, 0);
    assert_strict(&hub);
    assert!(hub.run(&["doctor"]).stdout.contains("[OK  ] schema_version"));
}

/// A 1.2.0 process sharing the store writes `Infinity` while a 1.3.0
/// server is running: the server reads it, carries on, and its next write
/// leaves a strict file.
#[test]
fn a_running_server_repairs_what_an_old_writer_appends() {
    let hub = Hub::new("bricked-live");
    let mut s = Raw::start(&hub, &[]);
    s.ok("register_agent", r#"{"agent_id":"a"}"#);
    append(
        &journal(&hub),
        b"{\"t\":\"put\",\"table\":\"locks\",\"row\":{\"id\": 7, \"document_path\": \"old.txt\", \"locked_by\": \"a\", \"locked_at\": 1794731900.0, \"lock_ttl\": Infinity, \"lock_type\": \"exclusive\", \"region_start\": null, \"region_end\": null, \"worktree_root\": null}}\n",
    );
    let locks = s.ok("list_locks", "{}");
    assert_eq!(locks.get("count"), Some(&json::i(1)), "{}", json::dumps(&locks));
    s.ok("heartbeat", r#"{"agent_id":"a"}"#);
    assert!(!journal_text(&hub).contains("Infinity"));
    assert_strict(&hub);
}

// ---------------------------------------------------------------- A2

/// 1.2.0: a request nested 100,000 deep ended the server with `thread
/// 'main' has overflowed its stack`.
#[test]
fn a_deeply_nested_request_gets_an_error_and_the_server_lives() {
    let hub = Hub::new("deep");
    let mut s = Raw::start(&hub, &[]);
    for depth in [129, 10_000, 100_000] {
        let nested = format!("{}{}", "[".repeat(depth), "]".repeat(depth));
        let reply = s.call_text("register_agent", &format!(r#"{{"agent_id":"deep","x":{nested}}}"#));
        let (code, message) = rpc_error(&reply);
        assert_eq!(code, -32700, "depth {depth}");
        assert!(message.contains("nested deeper than 128 levels"), "{message}");
        assert!(s.alive(), "the server died at depth {depth}");
    }
    // Objects, and brackets that never close.
    let objects = format!("{}1{}", r#"{"a":"#.repeat(50_000), "}".repeat(50_000));
    let (code, _) = rpc_error(&s.call_text("register_agent", &objects));
    assert_eq!(code, -32700);
    s.send(&"[".repeat(200_000));
    let (code, message) = rpc_error(&s.recv());
    assert_eq!(code, -32700);
    assert!(message.contains("nested deeper than 128 levels"), "{message}");
    // The same server still does real work.
    let done = s.ok("register_agent", r#"{"agent_id":"after"}"#);
    assert_eq!(done.get("agent_id").and_then(Value::as_str), Some("after"));
}

/// The deepest value the parser lets through passes the schema check, the
/// journal writer and the reply writer: nothing after the parser recurses
/// further than the parser allowed.
#[test]
fn the_deepest_accepted_value_goes_all_the_way_through() {
    let hub = Hub::new("deep-ok");
    let mut s = Raw::start(&hub, &[]);
    s.ok("register_agent", r#"{"agent_id":"a"}"#);
    // request(1) > params(2) > arguments(3) > payload: 124 objects (levels
    // 4 to 127) around one array (level 128).
    let nested = format!("{}[]{}", r#"{"k":"#.repeat(124), "}".repeat(124));
    let sent = s.ok(
        "send_message",
        &format!(r#"{{"from_agent_id":"a","to_agent_id":"a","message_type":"deep","payload":{nested}}}"#),
    );
    assert!(sent.get("message_id").is_some(), "{}", json::dumps(&sent));
    let got = s.call_text("manage_messages", r#"{"action":"get","agent_id":"a"}"#);
    let (text, is_error) = tool_text(&got);
    assert!(!is_error, "{text}");
    assert!(text.contains("deep"), "{text}");
    // One level more is refused.
    let over = format!("{}[]{}", r#"{"k":"#.repeat(125), "}".repeat(125));
    let reply = s.call_text(
        "send_message",
        &format!(r#"{{"from_agent_id":"a","to_agent_id":"a","message_type":"deep","payload":{over}}}"#),
    );
    assert_eq!(rpc_error(&reply).0, -32700);
    drop(s);
    assert_strict(&hub);
}

// ---------------------------------------------------------------- A3

#[test]
fn requests_must_be_strict_json() {
    let hub = Hub::new("strict");
    let mut s = Raw::start(&hub, &[]);
    for (arguments, why) in [
        (r#"{"agent_id":"a","agent_id":"b"}"#, "duplicate object key \"agent_id\""),
        (r#"{"agent_id":"a","stale_timeout":01}"#, "leading zero"),
        (r#"{"agent_id":"a","stale_timeout":1.}"#, "digit after '.'"),
        (r#"{"agent_id":"a","stale_timeout":-.5}"#, "digit before"),
        ("{\"agent_id\":\"a\tb\"}", "raw control character 0x09"),
        (r#"{"agent_id":"a"} trailing"#, "expected ',' or '}'"),
    ] {
        let reply = s.call_text("register_agent", arguments);
        let (code, message) = rpc_error(&reply);
        assert_eq!(code, -32700, "{arguments}");
        assert!(message.contains(why), "{arguments}: {message}");
    }
    // Trailing bytes after the whole request.
    s.send(r#"{"jsonrpc":"2.0","id":90,"method":"ping"} {"again":1}"#);
    let (code, message) = rpc_error(&s.recv());
    assert_eq!(code, -32700);
    assert!(message.contains("trailing data"), "{message}");
    // Nothing above registered an agent; a well-formed request still works.
    let agents = s.ok("list_agents", "{}");
    assert_eq!(agents.get("agents").and_then(Value::as_array).map(|rows| rows.len()), Some(0));
    // The same control character written as an escape is fine.
    s.ok("register_agent", r#"{"agent_id":"a"}"#);
    s.ok("create_task", r#"{"task_id":"t","parent_agent_id":"a","description":"tab\there"}"#);
    drop(s);
    assert_eq!(open(&hub).store.state.tasks[0].description, "tab\there");
}

// ---------------------------------------------------------------- A4

/// A `Content-Length` frame may carry more headers; 1.2.0 read exactly one
/// line after the length, so `Content-Type` broke the frame.
#[test]
fn a_content_length_frame_may_have_more_headers() {
    let hub = Hub::new("frames");
    let mut s = Raw::start(&hub, &[]);
    let body = r#"{"jsonrpc":"2.0","id":41,"method":"ping"}"#;
    s.send_bytes(
        format!(
            "Content-Length: {}\r\nContent-Type: application/vscode-jsonrpc; charset=utf-8\r\n\r\n{body}",
            body.len()
        )
        .as_bytes(),
    );
    let reply = s.recv();
    assert_eq!(reply.get("id"), Some(&json::i(41)));
    assert!(reply.get("result").is_some(), "{}", json::dumps(&reply));
    // Header order does not matter, and names are case-insensitive.
    s.send_bytes(
        format!("content-type: application/json\r\nCONTENT-LENGTH: {}\r\n\r\n{body}", body.len()).as_bytes(),
    );
    assert_eq!(s.recv().get("id"), Some(&json::i(41)));
    // A newline-delimited request right after a frame still works.
    s.send(r#"{"jsonrpc":"2.0","id":42,"method":"ping"}"#);
    assert_eq!(s.recv().get("id"), Some(&json::i(42)));
}

/// 1.2.0 allocated whatever `Content-Length` said. A length past the cap
/// is refused without allocating it, and the server keeps running.
#[test]
fn an_oversized_frame_is_refused() {
    let hub = Hub::new("frames-big");
    let mut s = Raw::start(&hub, &[]);
    for bad in ["Content-Length: twelve\r\n\r\n", "Content-Length: -5\r\n\r\n", "Content-Type: text/plain\r\n\r\n"] {
        s.send_bytes(bad.as_bytes());
        let (code, message) = rpc_error(&s.recv());
        assert_eq!(code, -32600, "{bad}");
        assert!(message.contains("Content-Length"), "{message}");
    }
    // A body past the cap is skipped, not stored, and the frame after it
    // is read as a new message.
    let mut big = b"Content-Length: 1500000\r\n\r\n".to_vec();
    big.extend(std::iter::repeat(b' ').take(1_500_000));
    s.send_bytes(&big);
    let (code, message) = rpc_error(&s.recv());
    assert_eq!(code, -32600);
    assert!(message.contains("Content-Length 1500000") && message.contains("1000000"), "{message}");
    s.send(r#"{"jsonrpc":"2.0","id":43,"method":"ping"}"#);
    assert_eq!(s.recv().get("id"), Some(&json::i(43)));
    // A single line longer than the cap is refused as well.
    let mut long = vec![b' '; 1_200_000];
    long.push(b'\n');
    s.send_bytes(&long);
    let (code, message) = rpc_error(&s.recv());
    assert_eq!(code, -32600);
    assert!(message.contains("longer than 1000000 bytes"), "{message}");
    s.send(r#"{"jsonrpc":"2.0","id":44,"method":"ping"}"#);
    assert_eq!(s.recv().get("id"), Some(&json::i(44)));
    // Bytes that are not UTF-8 are a parse error, not the end of the server.
    s.send_bytes(b"{\"jsonrpc\":\"2.0\",\"id\":45,\"method\":\"\xff\"}\n");
    assert_eq!(rpc_error(&s.recv()).0, -32700);
    // A length no machine has is answered at once (1.2.0 tried to allocate
    // it), and the server is still there.
    s.send_bytes(b"Content-Length: 99999999999\r\n\r\n");
    let (code, message) = rpc_error(&s.recv());
    assert_eq!(code, -32600);
    assert!(message.contains("Content-Length 99999999999") && message.contains("1000000"), "{message}");
    assert!(s.alive());
}

// ---------------------------------------------------------------- A10

/// 1.2.0: half a record at the end of the journal made every command fail
/// with `Error: unterminated string`, and the next append was glued to it.
#[test]
fn a_torn_final_record_is_cut_off_and_kept() {
    let hub = Hub::new("torn");
    assert_eq!(hub.run(&["register", "a"]).code, 0);
    let before = journal_text(&hub);
    let torn = "{\"t\":\"put\",\"table\":\"locks\",\"row\":{\"id\": 9, \"document_path\": \"half";
    append(&journal(&hub), torn.as_bytes());
    let out = hub.run(&["status"]);
    assert_eq!(out.code, 0, "{}", out.stderr);
    assert!(out.stdout.contains("Registered Agents: 1"), "{}", out.stdout);
    assert!(out.stderr.contains("incomplete") && out.stderr.contains(".torn"), "{}", out.stderr);
    // The journal is back to its good bytes; the cut ones are beside it.
    assert_eq!(journal_text(&hub), before);
    let kept = torn_files(&hub);
    assert_eq!(kept.len(), 1, "{kept:?}");
    assert_eq!(std::fs::read(&kept[0]).unwrap(), torn.as_bytes());
    let name = kept[0].file_name().unwrap().to_string_lossy().into_owned();
    assert!(name.starts_with("coordination.jsonl.20") && name.ends_with("Z.torn"), "{name}");
    // Said once: the next command is quiet, and writes land on their own
    // lines.
    let again = hub.run(&["register", "b"]);
    assert_eq!(again.code, 0);
    assert!(!again.stderr.contains("incomplete"), "{}", again.stderr);
    assert_strict(&hub);
    assert_eq!(torn_files(&hub).len(), 1);
    // doctor reports it.
    let doctor = hub.run(&["doctor"]).stdout;
    assert!(doctor.contains("journal_tail") && doctor.contains(&name), "{doctor}");
}

#[test]
fn an_unreadable_last_record_is_moved_out_and_named_for_what_it_is() {
    // A multi-byte character split by the cut: the tail is not UTF-8.
    let hub = Hub::new("torn-utf8");
    assert_eq!(hub.run(&["register", "a"]).code, 0);
    let before = journal_text(&hub);
    let mut tail = b"{\"t\":\"put\",\"table\":\"agents\",\"row\":{\"agent_id\": \"caf".to_vec();
    tail.push(0xC3);
    append(&journal(&hub), &tail);
    assert_eq!(hub.run(&["status"]).code, 0);
    assert_eq!(journal_text(&hub), before);
    assert_eq!(std::fs::read(&torn_files(&hub)[0]).unwrap(), tail);

    // A whole line that is not a record, with only blank space after it:
    // moved out as well, but named for what it is, not as a torn write.
    let hub = Hub::new("unreadable-line");
    assert_eq!(hub.run(&["register", "a"]).code, 0);
    let before = journal_text(&hub);
    append(&journal(&hub), b"garbage\n\n  \n");
    let out = hub.run(&["heartbeat", "a"]);
    assert_eq!(out.code, 0, "{}", out.stderr);
    assert!(out.stderr.contains("is complete but is not a record this hub can read"), "{}", out.stderr);
    assert!(out.stderr.contains(".unreadable"), "{}", out.stderr);
    assert!(!out.stderr.contains("did not finish"), "a whole line was called an unfinished write: {}", out.stderr);
    assert!(journal_text(&hub).starts_with(&before));
    assert_strict(&hub);
    assert!(torn_files(&hub).is_empty(), "a whole line was filed as a torn write");
    let kept = coordinationhub::db::unreadable_files(&hub.store);
    assert_eq!(kept.len(), 1, "{kept:?}");
    assert_eq!(std::fs::read(&kept[0]).unwrap(), b"garbage\n\n  \n");
    // doctor says which kind each kept file is.
    let doctor = hub.run(&["doctor"]).stdout;
    let line = doctor.lines().find(|line| line.contains("journal_tail")).unwrap_or("");
    assert!(line.contains("1 complete final line(s) that were not readable records"), "{doctor}");
    assert!(!line.contains("unfinished write"), "{doctor}");
}

/// `doctor` reads the journal as the hub does. 1.3.0's first build said
/// `[FAIL] schema_version ... Infinity is not a JSON number` about a store
/// the hub opens and repairs, while the hub's own error sends people to
/// `doctor`.
#[test]
fn doctor_agrees_with_the_hub_about_the_journal() {
    // A store 1.2.0 bricked: the hub opens it, so doctor does not fail,
    // says what is in it and what happens next, and changes nothing.
    let hub = Hub::new("doctor-bricked");
    std::fs::write(journal(&hub), BRICKED).unwrap();
    let out = hub.run(&["doctor"]);
    assert_eq!(out.code, 0, "{}{}", out.stdout, out.stderr);
    let line = out.stdout.lines().find(|line| line.contains("schema_version")).unwrap_or("");
    assert!(line.contains("[OK  ]"), "{}", out.stdout);
    assert!(line.contains("3 record(s) hold numbers that are not JSON (Infinity or NaN"), "{line}");
    assert!(line.contains("the next coordinationhub command on this store replaces them"), "{line}");
    assert_eq!(journal_text(&hub), BRICKED, "doctor changed the journal");
    assert_eq!(hub.run(&["status"]).code, 0);
    let after = hub.run(&["doctor"]).stdout;
    assert!(after.contains("[OK  ] schema_version: journal parses; schema is current"), "{after}");

    // An unfinished last record: reported, not failed, and left for the
    // next command to cut off.
    let hub = Hub::new("doctor-torn");
    assert_eq!(hub.run(&["register", "a"]).code, 0);
    append(&journal(&hub), b"{\"t\":\"put\",\"table\":\"locks\",\"row\":{\"id\": 9");
    let pending = journal_text(&hub);
    let out = hub.run(&["doctor"]);
    assert_eq!(out.code, 0, "{}", out.stdout);
    let line = out.stdout.lines().find(|line| line.contains("schema_version")).unwrap_or("");
    assert!(line.contains("[OK  ]") && line.contains("the last record is an unfinished write (41 bytes)"), "{line}");
    assert!(line.contains(".torn"), "{line}");
    assert_eq!(journal_text(&hub), pending, "doctor changed the journal");
    assert!(torn_files(&hub).is_empty());

    // What the hub refuses, doctor fails, with the hub's own words.
    let hub = Hub::new("doctor-bad");
    assert_eq!(hub.run(&["register", "a"]).code, 0);
    let lines = journal_text(&hub).lines().count();
    append(&journal(&hub), b"garbage\n{\"t\":\"del\",\"table\":\"locks\",\"id\":\"9\"}\n");
    let refused = hub.run(&["status"]);
    assert_ne!(refused.code, 0);
    let out = hub.run(&["doctor"]);
    assert_eq!(out.code, 1, "{}", out.stdout);
    let line = out.stdout.lines().find(|line| line.contains("schema_version")).unwrap_or("");
    assert!(line.contains("[FAIL]"), "{}", out.stdout);
    assert!(line.contains(&format!("line {} cannot be read", lines + 1)), "{line}");
    assert!(refused.stderr.contains(&format!("line {} cannot be read", lines + 1)), "{}", refused.stderr);
}

/// A whole last record without a line feed is applied exactly once, even
/// when the line feed cannot be written at first. Applied before the write
/// (as 1.3.0's first build did), every failed call applied it again, and
/// the acknowledgement tables, which are appended to, grew a row each time.
#[test]
fn a_last_record_without_a_line_feed_is_applied_exactly_once() {
    let hub = Hub::new("once");
    let mut engine = open(&hub);
    call(&mut engine, "register_agent", r#"{"agent_id":"a"}"#);
    // Another writer leaves a whole record with no line feed.
    append(
        &journal(&hub),
        b"{\"t\":\"put\",\"table\":\"handoff_acks\",\"row\":{\"handoff_id\": 1, \"agent_id\": \"a\", \"acknowledged_at\": 5.0}}",
    );
    // While the journal cannot be written, the line feed cannot be added.
    let writable = std::fs::metadata(journal(&hub)).unwrap().permissions();
    let mut locked = writable.clone();
    locked.set_readonly(true);
    std::fs::set_permissions(journal(&hub), locked).unwrap();
    let blocked = std::fs::OpenOptions::new().append(true).open(journal(&hub)).is_err();
    let mut failures = 0;
    for _ in 0..3 {
        if let Err(err) = engine.call("list_agents", &json::obj(vec![])) {
            assert!(err.contains("cannot end the last record"), "{err}");
            failures += 1;
        }
    }
    #[allow(clippy::permissions_set_readonly_false)]
    {
        let mut restored = writable;
        restored.set_readonly(false);
        std::fs::set_permissions(journal(&hub), restored).unwrap();
    }
    // (A process that may write any file, root in a container, is never
    // blocked; the count below must hold either way.)
    assert_eq!(failures, if blocked { 3 } else { 0 });
    assert!(engine.store.state.handoff_acks.len() <= 1, "{:?}", engine.store.state.handoff_acks);
    call(&mut engine, "list_agents", "{}");
    assert_eq!(engine.store.state.handoff_acks.len(), 1, "{:?}", engine.store.state.handoff_acks);
    call(&mut engine, "heartbeat", r#"{"agent_id":"a"}"#);
    assert_strict(&hub);
    assert_eq!(open(&hub).store.state.handoff_acks.len(), 1);
}

/// What an older version stored must stay readable. 1.2.0 stored a
/// payload with a repeated key or 300 levels of nesting; read back with
/// the strict request parser, both came out as `"payload": null`, with no
/// warning.
#[test]
fn payloads_an_older_version_stored_stay_readable() {
    let hub = Hub::new("stored");
    let deep = |levels: usize| format!("{{\"deep\": {}{}}}", "[".repeat(levels), "]".repeat(levels));
    let stored: Vec<(&str, String)> = vec![
        ("repeated", "{\"k\": 1, \"k\": 2}".to_string()),
        ("deep", deep(300)),
        ("plain", "{\"note\": \"hello\"}".to_string()),
        ("too-deep", deep(400)),
        ("not-finite", "{\"big\": Infinity}".to_string()),
        ("not-json", "{oops".to_string()),
    ];
    {
        let mut engine = open(&hub);
        call(&mut engine, "register_agent", r#"{"agent_id":"a1"}"#);
        call(&mut engine, "register_agent", r#"{"agent_id":"a2"}"#);
        engine.store.begin().unwrap();
        for (kind, text) in &stored {
            let id = engine.store.state.next("messages");
            engine.store.state.messages.push(coordinationhub::db::Message {
                id,
                from_agent_id: "a1".into(),
                to_agent_id: "a2".into(),
                message_type: kind.to_string(),
                payload_json: Some(text.clone()),
                created_at: 1_794_000_000.0 + id as f64,
                read_at: None,
            });
        }
        // An event payload with a repeated key and a number that is not
        // finite: the lifecycle reader must still see it.
        engine.store.state.record_event("agent.reaped", json::s("replaced below"));
        engine.store.state.hub_events.last_mut().unwrap().payload =
            "{\"agent_id\": \"a1\", \"agent_id\": \"a1\", \"at\": Infinity}".to_string();
        engine.store.commit().unwrap();
        engine.store.end();
    }
    // A fresh process, over the CLI and over MCP.
    let cli = hub.run(&["get-messages", "a2", "--json"]);
    assert_eq!(cli.code, 0, "{}", cli.stderr);
    let mut s = Raw::start(&hub, &[]);
    let (mcp, is_error) = tool_text(&s.call_text("manage_messages", r#"{"action":"get","agent_id":"a2"}"#));
    assert!(!is_error, "{mcp}");
    for (how, text) in [("cli", cli.stdout.as_str()), ("mcp", mcp.as_str())] {
        // The reply nests deeper than a request may: read it as stored text.
        let reply = json::parse_stored(text).unwrap_or_else(|err| panic!("{how}: {err}"));
        let messages = reply.get("messages").and_then(Value::as_array).unwrap();
        assert_eq!(messages.len(), stored.len(), "{how}");
        let of = |kind: &str| {
            messages
                .iter()
                .find(|row| row.get("message_type").and_then(Value::as_str) == Some(kind))
                .unwrap_or_else(|| panic!("{how}: no {kind} message"))
        };
        for row in messages {
            assert_ne!(row.get("payload"), Some(&Value::Null), "{how}: a stored payload came back null: {}", json::dumps(row));
        }
        // A repeated key: the last value, as 0.8.0 kept.
        assert_eq!(of("repeated").get("payload"), Some(&json::obj(vec![("k", json::i(2))])), "{how}");
        assert_eq!(of("plain").get("payload"), Some(&json::obj(vec![("note", json::s("hello"))])), "{how}");
        // 300 levels: all of them.
        let mut level = of("deep").get("payload").and_then(|payload| payload.get("deep")).unwrap();
        let mut levels = 0;
        while let Some(inner) = level.as_array() {
            levels += 1;
            match inner.first() {
                Some(next) => level = next,
                None => break,
            }
        }
        assert_eq!(levels, 300, "{how}");
        for kind in ["repeated", "plain", "deep"] {
            assert!(of(kind).get("payload_unreadable").is_none(), "{how}: {kind}");
        }
        // What cannot be shown as JSON comes back as its stored text, with
        // the reason beside it.
        for (kind, why) in [("too-deep", "nested deeper than 320 levels"), ("not-finite", "not finite"), ("not-json", "object key must be a string")] {
            let row = of(kind);
            let text = &stored.iter().find(|(name, _)| *name == kind).unwrap().1;
            assert_eq!(row.get("payload").and_then(Value::as_str), Some(text.as_str()), "{how}: {kind}");
            let note = row.get("payload_unreadable").and_then(Value::as_str).unwrap_or("");
            assert!(note.contains(why) && note.contains("its text as stored"), "{how}: {kind}: {note}");
        }
    }
    drop(s);
    let engine = open(&hub);
    assert_eq!(act_identity::last_lifecycle(&engine.store.state, "a1"), Some("agent.reaped"));
}

/// A last record that is whole but lost its line feed (a hand edit, say)
/// is kept, and the next append does not land on it.
#[test]
fn a_whole_last_record_without_a_line_feed_is_kept() {
    let hub = Hub::new("no-newline");
    assert_eq!(hub.run(&["register", "a"]).code, 0);
    assert_eq!(hub.run(&["register", "b"]).code, 0);
    let text = journal_text(&hub);
    std::fs::write(journal(&hub), text.trim_end()).unwrap();
    assert_eq!(hub.run(&["register", "c"]).code, 0);
    assert_strict(&hub);
    assert!(torn_files(&hub).is_empty());
    let agents = hub.json(&["list-agents"]);
    assert_eq!(agents.get("agents").and_then(Value::as_array).map(|rows| rows.len()), Some(3));
}

/// A bad record with records after it is not a torn tail: the error names
/// the line and the recovery step, and the file is left alone.
#[test]
fn a_bad_record_in_the_middle_is_an_error_that_names_the_line() {
    let hub = Hub::new("bad-middle");
    assert_eq!(hub.run(&["register", "a"]).code, 0);
    let good = journal_text(&hub);
    let lines = good.lines().count();
    let mut broken = good.clone();
    broken.push_str("{\"t\":\"put\",\"table\":\"locks\",\"row\":{\"id\": 9, \"document_path\": \"half\n");
    broken.push_str("{\"t\":\"del\",\"table\":\"locks\",\"id\":\"9\"}\n");
    std::fs::write(journal(&hub), &broken).unwrap();
    let out = hub.run(&["status"]);
    assert_ne!(out.code, 0);
    assert!(out.stderr.contains(&format!("line {} cannot be read", lines + 1)), "{}", out.stderr);
    assert!(out.stderr.contains("To recover") && out.stderr.contains("doctor"), "{}", out.stderr);
    assert_eq!(journal_text(&hub), broken, "the journal was changed");
    assert!(torn_files(&hub).is_empty());
}

/// A server that is already running meets a torn tail left by a writer
/// that died: its next call works and its own record is not glued on.
#[test]
fn a_running_server_survives_a_torn_tail() {
    let hub = Hub::new("torn-live");
    let mut s = Raw::start(&hub, &[]);
    s.ok("register_agent", r#"{"agent_id":"a"}"#);
    append(&journal(&hub), b"{\"t\":\"put\",\"table\":\"tasks\",\"row\":{\"id\": \"t");
    let beat = s.ok("heartbeat", r#"{"agent_id":"a"}"#);
    assert_eq!(beat.get("updated"), Some(&json::b(true)));
    s.ok("create_task", r#"{"task_id":"t1","parent_agent_id":"a","description":"after the tear"}"#);
    drop(s);
    assert_strict(&hub);
    assert_eq!(torn_files(&hub).len(), 1);
    let fresh = open(&hub);
    assert_eq!(fresh.store.state.tasks.len(), 1);
    assert_eq!(fresh.store.state.tasks[0].description, "after the tear");
}

/// `COORDINATIONHUB_FSYNC=1` syncs after each commit; the calls behave the
/// same (the sync itself cannot be observed from a test).
#[test]
fn commits_work_with_fsync_on() {
    let hub = Hub::new("fsync");
    let env = [("COORDINATIONHUB_FSYNC", "1")];
    assert_eq!(cli(&hub, &["register", "a"], &env).code, 0);
    assert_eq!(cli(&hub, &["acquire-lock", "x.txt", "a"], &env).code, 0);
    assert_eq!(hub.json(&["list-locks"]).get("count"), Some(&json::i(1)));
    assert_strict(&hub);
}

// ---------------------------------------------------------------- A11

fn open(hub: &Hub) -> Engine {
    Engine::open(Some(&hub.store), Some(&hub.project), "hub").unwrap()
}

fn call(engine: &mut Engine, tool: &str, arguments: &str) -> Value {
    engine
        .call(tool, &json::parse(arguments).unwrap())
        .unwrap_or_else(|err| panic!("{tool}: {err}"))
}

/// What the two readers of old events answer for each agent.
fn answers(engine: &Engine, agents: &[&str]) -> Vec<(String, Vec<act_lock::Lapse>, Option<&'static str>)> {
    agents
        .iter()
        .map(|agent| {
            (
                agent.to_string(),
                act_lock::lapses(&engine.store.state, agent),
                act_identity::last_lifecycle(&engine.store.state, agent),
            )
        })
        .collect()
}

/// Retention drops old events and notifications, but not the ones the
/// lock-lapse warning and the reaped-or-deregistered check still read.
#[test]
fn pruning_keeps_what_live_answers_are_read_from() {
    let hub = Hub::new("prune");
    let mut engine = open(&hub);
    let agents = ["lapsed", "relocked", "reaped", "gone", "busy"];
    for agent in agents {
        call(&mut engine, "register_agent", &format!(r#"{{"agent_id":"{agent}"}}"#));
    }
    // `lapsed` and `relocked` each let a lock run out; `relocked` takes its
    // lock again, which closes that lapse.
    call(&mut engine, "acquire_lock", r#"{"document_path":"a.txt","agent_id":"lapsed","ttl":0.05}"#);
    call(&mut engine, "acquire_lock", r#"{"document_path":"b.txt","agent_id":"relocked","ttl":0.05}"#);
    std::thread::sleep(Duration::from_millis(120));
    engine.store.begin().unwrap();
    act_lock::reap_expired(&mut engine.store.state, 0.0);
    engine.store.commit().unwrap();
    engine.store.end();
    call(&mut engine, "acquire_lock", r#"{"document_path":"b.txt","agent_id":"relocked","ttl":300}"#);
    // `busy` produces the bulk of the journal: locks taken and released.
    for round in 0..40 {
        let path = format!(r#"{{"document_path":"busy-{round}.txt","agent_id":"busy"}}"#);
        call(&mut engine, "acquire_lock", &path);
        call(&mut engine, "release_lock", &path);
        call(&mut engine, "notify_change", &format!(r#"{{"document_path":"busy-{round}.txt","change_type":"modified","agent_id":"busy"}}"#));
    }
    call(&mut engine, "deregister_agent", r#"{"agent_id":"gone"}"#);
    // The reaper marks `reaped` stopped (heartbeat older than the timeout).
    engine.store.begin().unwrap();
    engine.store.state.agent_mut("reaped").unwrap().last_heartbeat -= 10_000.0;
    act_identity::reap_stale(&mut engine.store.state, 600.0);
    // Everything so far happened thirty days ago.
    let month = 30.0 * 24.0 * 3600.0;
    for event in &mut engine.store.state.hub_events {
        event.created_at -= month;
    }
    for note in &mut engine.store.state.notifications {
        note.created_at -= month;
    }
    engine.store.commit().unwrap();
    engine.store.end();
    // And one thing happened just now.
    call(&mut engine, "notify_change", r#"{"document_path":"fresh.txt","change_type":"modified","agent_id":"busy"}"#);

    let before = answers(&engine, &agents);
    assert_eq!(before[0].1.len(), 1, "lapsed has one open lapse");
    assert!(before[1].1.is_empty(), "relocked closed its lapse");
    assert_eq!(before[2].2, Some("agent.reaped"));
    assert_eq!(before[3].2, Some("agent.deregistered"));
    let (events_before, notes_before) =
        (engine.store.state.hub_events.len(), engine.store.state.notifications.len());

    engine.store.begin().unwrap();
    let cutoff = coordinationhub::util::now() - 7.0 * 24.0 * 3600.0;
    let dropped = house::prune_before(&mut engine.store.state, cutoff);
    engine.store.commit().unwrap();
    engine.store.end();

    let state = &engine.store.state;
    assert!(dropped > 200, "only {dropped} rows were dropped");
    assert!(state.hub_events.len() < events_before / 10, "{} of {events_before} events left", state.hub_events.len());
    assert_eq!(state.notifications.len(), 1, "of {notes_before} notifications only the fresh one stays");
    assert_eq!(answers(&engine, &agents), before, "pruning changed a live answer");
    // What is left is exactly: each agent's last lifecycle event, the one
    // open lapse, and the fresh event.
    assert_eq!(state.hub_events.len(), agents.len() + 1 + 1, "{:?}", state.hub_events);

    // A second process reads the same answers from the journal, and the
    // reaped agent still comes back on a heartbeat while the deregistered
    // one does not.
    let mut second = open(&hub);
    assert_eq!(answers(&second, &agents), before);
    let beat = call(&mut second, "heartbeat", r#"{"agent_id":"reaped"}"#);
    assert_eq!(beat.get("revived"), Some(&json::b(true)), "{}", json::dumps(&beat));
    let beat = call(&mut second, "heartbeat", r#"{"agent_id":"gone"}"#);
    assert_eq!(beat.get("updated"), Some(&json::b(false)));
    let beat = call(&mut second, "heartbeat", r#"{"agent_id":"lapsed"}"#);
    assert!(json::dumps(&beat).contains("a.txt"), "the lapse warning is gone: {}", json::dumps(&beat));
}

/// The CLI alone (no server, so no housekeeping thread) prunes too, once
/// the journal passes the compaction size; `COORDINATIONHUB_EVENTS_MAX_AGE_S`
/// sets the window and 0 keeps everything.
#[test]
fn the_retention_window_is_configurable() {
    let hub = Hub::new("retention");
    {
        let mut engine = open(&hub);
        call(&mut engine, "register_agent", r#"{"agent_id":"a"}"#);
        for round in 0..20 {
            call(&mut engine, "notify_change", &format!(r#"{{"document_path":"old-{round}.txt","change_type":"modified","agent_id":"a"}}"#));
        }
        engine.store.begin().unwrap();
        for event in &mut engine.store.state.hub_events {
            event.created_at -= 3.0 * 24.0 * 3600.0;
        }
        for note in &mut engine.store.state.notifications {
            note.created_at -= 3.0 * 24.0 * 3600.0;
        }
        engine.store.commit().unwrap();
        engine.store.end();
    }
    let notes = |hub: &Hub| open(hub).store.state.notifications.len();
    assert_eq!(notes(&hub), 20);
    // Three days old, seven-day default: kept.
    let small = ("COORDINATIONHUB_COMPACT_MIN_BYTES", "1");
    assert_eq!(cli(&hub, &["heartbeat", "a"], &[small]).code, 0);
    assert_eq!(notes(&hub), 20);
    // Window off: kept.
    assert_eq!(cli(&hub, &["heartbeat", "a"], &[small, ("COORDINATIONHUB_EVENTS_MAX_AGE_S", "0")]).code, 0);
    assert_eq!(notes(&hub), 20);
    // Below the compaction size nothing is scanned.
    assert_eq!(cli(&hub, &["heartbeat", "a"], &[("COORDINATIONHUB_EVENTS_MAX_AGE_S", "3600")]).code, 0);
    assert_eq!(notes(&hub), 20);
    // One-hour window: gone, and the agent's registration event stays.
    assert_eq!(cli(&hub, &["heartbeat", "a"], &[small, ("COORDINATIONHUB_EVENTS_MAX_AGE_S", "3600")]).code, 0);
    assert_eq!(notes(&hub), 0);
    let engine = open(&hub);
    assert_eq!(act_identity::last_lifecycle(&engine.store.state, "a"), Some("agent.registered"));
    assert_strict(&hub);
}

fn first_line(hub: &Hub) -> String {
    journal_text(hub).lines().next().unwrap_or("").to_string()
}

const GENERATION: &str = r#"{"t":"del","table":"generation","id":""#;

/// The journal is rewritten as a snapshot once it is large against the
/// live rows; state is unchanged and the file is far shorter.
#[test]
fn a_journal_of_mostly_dead_records_is_compacted() {
    let hub = Hub::new("compact");
    let mut s = Raw::start(&hub, &[("COORDINATIONHUB_COMPACT_MIN_BYTES", "1")]);
    s.ok("register_agent", r#"{"agent_id":"a"}"#);
    for n in 0..10 {
        s.ok("create_task", &format!(r#"{{"task_id":"t{n}","parent_agent_id":"a","description":"task {n}"}}"#));
    }
    s.ok("acquire_lock", r#"{"document_path":"held.txt","agent_id":"a","ttl":3600}"#);
    let mut longest = 0;
    for _ in 0..400 {
        s.ok("heartbeat", r#"{"agent_id":"a"}"#);
        longest = longest.max(journal_text(&hub).lines().count());
    }
    let text = journal_text(&hub);
    assert!(text.starts_with(GENERATION), "never compacted: {}", first_line(&hub));
    // 400 heartbeats alone are 400 records; compaction kept it near the
    // live rows (about 40) plus the slack before the next rewrite.
    assert!(longest < 300, "the journal reached {longest} records");
    assert!(text.lines().count() < 300, "{} records", text.lines().count());
    assert_strict(&hub);
    // No temporary file is left behind.
    let leftovers: Vec<_> = std::fs::read_dir(&hub.store)
        .unwrap()
        .filter_map(|entry| entry.ok())
        .map(|entry| entry.file_name().to_string_lossy().into_owned())
        .filter(|name| name.contains("compact"))
        .collect();
    assert!(leftovers.is_empty(), "{leftovers:?}");
    // The same server and a fresh process agree on the state.
    let tasks = s.ok("query_tasks", r#"{"query_type":"all"}"#);
    assert_eq!(tasks.get("tasks").and_then(Value::as_array).map(|rows| rows.len()), Some(10));
    drop(s);
    assert_eq!(hub.json(&["list-locks"]).get("count"), Some(&json::i(1)));
    let fresh = open(&hub);
    assert_eq!(fresh.store.state.tasks.len(), 10);
    assert_eq!(fresh.store.state.agents.len(), 1);
    // 1.2.0 skips a `del` on a table it does not know, so the first line
    // is the only record it has not seen before; every other record is a
    // kind it already reads.
    for line in journal_text(&hub).lines().skip(1) {
        let record = json::parse(line).unwrap();
        let kind = record.get("t").and_then(Value::as_str).unwrap();
        assert!(matches!(kind, "put" | "del" | "ids"), "{line}");
        assert_ne!(record.get("table").and_then(Value::as_str), Some("generation"), "{line}");
    }
    // Compaction can be turned off.
    let off = Hub::new("compact-off");
    let mut s = Raw::start(&off, &[("COORDINATIONHUB_COMPACT_MIN_BYTES", "0")]);
    s.ok("register_agent", r#"{"agent_id":"a"}"#);
    for _ in 0..300 {
        s.ok("heartbeat", r#"{"agent_id":"a"}"#);
    }
    assert!(!journal_text(&off).starts_with(GENERATION));
    assert!(journal_text(&off).lines().count() > 300);
}

/// A second process holds an old byte offset when the journal is
/// compacted, and the new file is LONGER than that offset, so length alone
/// would look like an ordinary append. The idle process must notice and
/// reload: it must drop the lock that was released, see the new tasks, and
/// both processes must go on reading each other's writes.
#[test]
fn an_idle_process_reloads_after_another_process_compacts() {
    let hub = Hub::new("compact-multi");
    // `idle` never compacts by itself.
    let mut idle = Raw::start(&hub, &[("COORDINATIONHUB_COMPACT_MIN_BYTES", "0")]);
    let mut busy = Raw::start(&hub, &[("COORDINATIONHUB_COMPACT_MIN_BYTES", "1")]);
    idle.ok("register_agent", r#"{"agent_id":"idle"}"#);
    busy.ok("register_agent", r#"{"agent_id":"busy"}"#);
    busy.ok("acquire_lock", r#"{"document_path":"shared.txt","agent_id":"busy","ttl":3600}"#);
    // `idle` reads the journal up to here, lock included, then goes quiet.
    let seen = idle.ok("list_locks", "{}");
    assert_eq!(seen.get("count"), Some(&json::i(1)));
    let idle_offset = std::fs::metadata(journal(&hub)).unwrap().len();
    assert!(!journal_text(&hub).starts_with(GENERATION));

    for n in 0..40 {
        busy.ok("create_task", &format!(r#"{{"task_id":"t{n}","parent_agent_id":"busy","description":"task number {n} with a description long enough to take some room"}}"#));
    }
    busy.ok("release_lock", r#"{"document_path":"shared.txt","agent_id":"busy"}"#);
    for _ in 0..500 {
        busy.ok("heartbeat", r#"{"agent_id":"busy"}"#);
    }
    assert!(journal_text(&hub).starts_with(GENERATION), "never compacted: {}", first_line(&hub));
    let compacted = std::fs::metadata(journal(&hub)).unwrap().len();
    assert!(
        compacted > idle_offset,
        "the test needs a compacted journal ({compacted} bytes) longer than the idle offset ({idle_offset})"
    );

    // `idle` wakes up.
    let locks = idle.ok("list_locks", "{}");
    assert_eq!(locks.get("count"), Some(&json::i(0)), "a released lock survived: {}", json::dumps(&locks));
    let tasks = idle.ok("query_tasks", r#"{"query_type":"all"}"#);
    assert_eq!(tasks.get("tasks").and_then(Value::as_array).map(|rows| rows.len()), Some(40));
    let agents = idle.ok("list_agents", "{}");
    assert_eq!(agents.get("agents").and_then(Value::as_array).map(|rows| rows.len()), Some(2));
    // It writes, and the compacting process reads that write.
    let taken = idle.ok("acquire_lock", r#"{"document_path":"shared.txt","agent_id":"idle","ttl":3600}"#);
    assert_eq!(taken.get("acquired"), Some(&json::b(true)), "{}", json::dumps(&taken));
    let refused = busy.ok("acquire_lock", r#"{"document_path":"shared.txt","agent_id":"busy"}"#);
    assert_eq!(refused.get("acquired"), Some(&json::b(false)));
    assert_eq!(refused.get("locked_by").and_then(Value::as_str), Some("idle"));
    // More compactions while `idle` keeps calling: still consistent.
    for round in 0..300 {
        busy.ok("heartbeat", r#"{"agent_id":"busy"}"#);
        if round % 50 == 0 {
            let tasks = idle.ok("query_tasks", r#"{"query_type":"all"}"#);
            assert_eq!(tasks.get("tasks").and_then(Value::as_array).map(|rows| rows.len()), Some(40));
        }
    }
    drop(idle);
    drop(busy);
    assert_strict(&hub);
    let fresh = open(&hub);
    assert_eq!(fresh.store.state.tasks.len(), 40);
    assert_eq!(fresh.store.state.locks.len(), 1);
}

/// Two engines in one process (a server's shared engine and a wait
/// engine): one compacts, the other holds a stale offset.
#[test]
fn a_second_engine_in_the_same_process_reloads_after_compaction() {
    let hub = Hub::new("compact-engines");
    let mut first = open(&hub);
    let mut second = open(&hub);
    call(&mut first, "register_agent", r#"{"agent_id":"a"}"#);
    call(&mut second, "list_agents", "{}");
    for n in 0..30 {
        call(&mut first, "create_task", &format!(r#"{{"task_id":"t{n}","parent_agent_id":"a","description":"task {n}"}}"#));
    }
    let (records, rows) = first.store.journal_shape();
    assert!(records > rows, "{records} records for {rows} rows");
    first.store.begin().unwrap();
    first.store.compact().unwrap();
    first.store.end();
    let (records, rows) = first.store.journal_shape();
    assert_eq!(records, rows + 2, "a snapshot is one record per row, the ids and the generation line");
    assert!(journal_text(&hub).starts_with(GENERATION));
    let tasks = call(&mut second, "query_tasks", r#"{"query_type":"all"}"#);
    assert_eq!(tasks.get("tasks").and_then(Value::as_array).map(|rows| rows.len()), Some(30));
    // Compacting twice gives two different generations.
    let one = first_line(&hub);
    first.store.begin().unwrap();
    first.store.compact().unwrap();
    first.store.end();
    assert_ne!(first_line(&hub), one);
    call(&mut second, "create_task", r#"{"task_id":"late","parent_agent_id":"a","description":"after two compactions"}"#);
    let tasks = call(&mut first, "query_tasks", r#"{"query_type":"all"}"#);
    assert_eq!(tasks.get("tasks").and_then(Value::as_array).map(|rows| rows.len()), Some(31));
    assert_strict(&hub);
}
