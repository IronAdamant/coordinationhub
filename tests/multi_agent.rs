//! A planner and two workers, each with its own `serve-mcp` process on one
//! shared store, the way separate agent sessions share a project. They
//! register, contend for the coordinator lease, split dependent tasks,
//! contend for a file, wait on each other across processes, hand work off
//! and message each other. Run with `--nocapture` to print per-call
//! latency and reply size.

use coordinationhub::json::{self, Value};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::time::Instant;

struct Session {
    name: &'static str,
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
    seq: i64,
}

struct Reply {
    value: Value,
    text: String,
    is_error: bool,
}

struct Timing {
    agent: &'static str,
    tool: String,
    millis: f64,
    bytes: usize,
}

impl Session {
    fn open(name: &'static str, store: &Path, project: &Path) -> Session {
        let mut child = Command::new(env!("CARGO_BIN_EXE_coordinationhub"))
            .arg("serve-mcp")
            .arg("--storage-dir")
            .arg(store)
            .arg("--project-root")
            .arg(project)
            .env("COORDINATIONHUB_HOUSEKEEPING", "0")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .unwrap();
        let stdin = child.stdin.take().unwrap();
        let stdout = BufReader::new(child.stdout.take().unwrap());
        let mut session = Session { name, child, stdin, stdout, seq: 0 };
        session.send("initialize", json::obj(vec![]));
        session
    }

    fn send(&mut self, method: &str, params: Value) -> Value {
        self.seq += 1;
        let request = json::obj(vec![
            ("jsonrpc", json::s("2.0")),
            ("id", json::i(self.seq)),
            ("method", json::s(method)),
            ("params", params),
        ]);
        writeln!(self.stdin, "{}", json::dumps(&request)).unwrap();
        self.stdin.flush().unwrap();
        let mut line = String::new();
        self.stdout.read_line(&mut line).unwrap();
        json::parse(line.trim_end()).unwrap()
    }

    fn call(&mut self, log: &mut Vec<Timing>, tool: &str, args: Value) -> Reply {
        let started = Instant::now();
        let raw = self.send(
            "tools/call",
            json::obj(vec![("name", json::s(tool)), ("arguments", args)]),
        );
        let millis = started.elapsed().as_secs_f64() * 1000.0;
        let result = raw.get("result").cloned().unwrap_or(Value::Null);
        let text = result
            .get("content")
            .and_then(Value::as_array)
            .and_then(|items| items.first())
            .and_then(|item| item.get("text"))
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let is_error = result.get("isError").and_then(Value::as_bool).unwrap_or(false);
        log.push(Timing { agent: self.name, tool: tool.to_string(), millis, bytes: text.len() });
        let value = json::parse(&text).unwrap_or(Value::Null);
        Reply { value, text, is_error }
    }

    fn close(mut self) {
        drop(self.stdin);
        let _ = self.child.wait();
    }
}

fn s(text: &str) -> Value {
    json::s(text)
}

fn obj(pairs: Vec<(&str, Value)>) -> Value {
    json::obj(pairs)
}

fn ids(reply: &Reply, key: &str) -> Vec<String> {
    reply
        .value
        .get(key)
        .and_then(Value::as_array)
        .unwrap_or(&[])
        .iter()
        .filter_map(|row| row.get("id").and_then(Value::as_str).map(str::to_string))
        .collect()
}

fn workspace() -> (PathBuf, PathBuf) {
    let base = std::env::temp_dir().join(format!(
        "chub-swarm-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    let store = base.join("store");
    let project = base.join("project");
    std::fs::create_dir_all(&store).unwrap();
    std::fs::create_dir_all(project.join("src")).unwrap();
    std::fs::write(project.join("src/api.rs"), "pub fn fetch() {}\n").unwrap();
    (store, project)
}

#[test]
fn planner_and_two_workers_share_one_hub() {
    let (store, project) = workspace();
    let mut log: Vec<Timing> = Vec::new();
    let mut planner = Session::open("planner", &store, &project);
    let mut w1 = Session::open("w1", &store, &project);
    let mut w2 = Session::open("w2", &store, &project);

    // Identity: each session registers; the last sees all three.
    planner.call(&mut log, "register_agent", obj(vec![("agent_id", s("planner"))]));
    w1.call(&mut log, "register_agent", obj(vec![("agent_id", s("w1")), ("parent_id", s("planner"))]));
    let joined = w2.call(&mut log, "register_agent", obj(vec![("agent_id", s("w2")), ("parent_id", s("planner"))]));
    assert_eq!(joined.value.get("registered_agents").and_then(Value::as_array).map(|a| a.len()), Some(3));

    // Leadership: the planner leads; a worker is refused and told by whom and until when.
    let lease = planner.call(&mut log, "acquire_coordinator_lease", obj(vec![("agent_id", s("planner")), ("ttl", json::i(60))]));
    assert_eq!(lease.value.get("acquired"), Some(&Value::Bool(true)));
    let refused = w1.call(&mut log, "acquire_coordinator_lease", obj(vec![("agent_id", s("w1"))]));
    assert_eq!(refused.value.get("acquired"), Some(&Value::Bool(false)));
    let holder = refused.value.get("holder").cloned().unwrap();
    assert_eq!(holder.get("holder_id").and_then(Value::as_str), Some("planner"));
    assert!(holder.get("expires_at").and_then(Value::as_f64).is_some());

    // Work board: three tasks, one depending on another.
    for (id, desc, deps) in [
        ("T-api", "add retry to fetch()", vec![]),
        ("T-tests", "test the retry path", vec![s("T-api")]),
        ("T-docs", "document the retry flag", vec![]),
    ] {
        let mut args = vec![
            ("task_id", s(id)),
            ("parent_agent_id", s("planner")),
            ("description", s(desc)),
        ];
        if !deps.is_empty() {
            args.push(("depends_on", json::arr(deps)));
        }
        let made = planner.call(&mut log, "create_task", obj(args));
        assert!(!made.is_error, "{}", made.text);
    }
    let open = w1.call(&mut log, "get_available_tasks", obj(vec![]));
    let mut available = ids(&open, "tasks");
    available.sort();
    assert_eq!(available, vec!["T-api".to_string(), "T-docs".to_string()]);

    // Claims: each worker takes a task and starts it. Assignment alone keeps
    // a task pending (and listed for its assignee); in_progress takes it
    // off the available list.
    w1.call(&mut log, "assign_task", obj(vec![("task_id", s("T-api")), ("assigned_agent_id", s("w1"))]));
    let mine = w1.call(&mut log, "get_available_tasks", obj(vec![("agent_id", s("w1"))]));
    assert_eq!(ids(&mine, "tasks"), vec!["T-api".to_string()]);
    w1.call(&mut log, "update_task_status", obj(vec![("task_id", s("T-api")), ("status", s("in_progress"))]));
    w2.call(&mut log, "assign_task", obj(vec![("task_id", s("T-docs")), ("assigned_agent_id", s("w2"))]));
    w2.call(&mut log, "update_task_status", obj(vec![("task_id", s("T-docs")), ("status", s("in_progress"))]));
    let open = w2.call(&mut log, "get_available_tasks", obj(vec![]));
    assert!(ids(&open, "tasks").is_empty(), "{}", open.text);

    // File ownership: w1 locks the file; w2 is refused and told who holds it.
    let locked = w1.call(&mut log, "acquire_lock", obj(vec![("document_path", s("src/api.rs")), ("agent_id", s("w1"))]));
    assert_eq!(locked.value.get("acquired"), Some(&Value::Bool(true)));
    let blocked = w2.call(&mut log, "acquire_lock", obj(vec![("document_path", s("src/api.rs")), ("agent_id", s("w2"))]));
    assert_eq!(blocked.value.get("acquired"), Some(&Value::Bool(false)));
    assert_eq!(blocked.value.get("locked_by").and_then(Value::as_str), Some("w1"));

    // w1 finishes; the dependent task opens up for w2.
    w1.call(&mut log, "notify_change", obj(vec![("document_path", s("src/api.rs")), ("change_type", s("modified")), ("agent_id", s("w1"))]));
    w1.call(&mut log, "update_task_status", obj(vec![("task_id", s("T-api")), ("status", s("completed")), ("summary", s("retry with backoff, 3 tries"))]));
    w1.call(&mut log, "release_lock", obj(vec![("document_path", s("src/api.rs")), ("agent_id", s("w1"))]));
    let changes = w2.call(&mut log, "get_notifications", obj(vec![("exclude_agent", s("w2")), ("limit", json::i(5))]));
    assert!(changes.text.contains("src/api.rs"));
    let open = w2.call(&mut log, "get_available_tasks", obj(vec![]));
    assert_eq!(ids(&open, "tasks"), vec!["T-tests".to_string()]);
    w2.call(&mut log, "assign_task", obj(vec![("task_id", s("T-tests")), ("assigned_agent_id", s("w2"))]));
    w2.call(&mut log, "update_task_status", obj(vec![("task_id", s("T-tests")), ("status", s("in_progress"))]));
    let relocked = w2.call(&mut log, "acquire_lock", obj(vec![("document_path", s("src/api.rs")), ("agent_id", s("w2"))]));
    assert_eq!(relocked.value.get("acquired"), Some(&Value::Bool(true)));

    // Cross-process wait: the planner blocks on T-docs while w2 finishes it.
    let finisher = {
        let store = store.clone();
        let project = project.clone();
        std::thread::spawn(move || {
            std::thread::sleep(std::time::Duration::from_millis(300));
            let mut late = Session::open("w2-late", &store, &project);
            let mut sink = Vec::new();
            late.call(&mut sink, "update_task_status", obj(vec![("task_id", s("T-docs")), ("status", s("completed")), ("summary", s("README section"))]));
            late.close();
        })
    };
    let waited = planner.call(&mut log, "wait_for_task", obj(vec![("task_id", s("T-docs")), ("timeout_s", json::f(10.0))]));
    finisher.join().unwrap();
    assert_eq!(waited.value.get("status").and_then(Value::as_str), Some("completed"), "{}", waited.text);
    let waited_s = waited.value.get("waited_s").and_then(Value::as_f64).unwrap();
    assert!(waited_s < 3.0, "woke after {waited_s}s");

    // Handoff: w1 hands src/api.rs to w2; w2 sees it in its inbox and acknowledges.
    let handoff = w1.call(&mut log, "broadcast", obj(vec![
        ("agent_id", s("w1")),
        ("handoff_targets", json::arr(vec![s("w2")])),
        ("document_path", s("src/api.rs")),
    ]));
    let handoff_id = handoff.value.get("handoff_id").and_then(Value::as_i64).unwrap();
    let inbox = w2.call(&mut log, "manage_messages", obj(vec![("action", s("get")), ("agent_id", s("w2")), ("unread_only", json::b(true))]));
    let payload = inbox
        .value
        .get("messages")
        .and_then(Value::as_array)
        .and_then(|rows| rows.iter().find(|row| row.get("message_type").and_then(Value::as_str) == Some("handoff")))
        .and_then(|row| row.get("payload"))
        .cloned()
        .unwrap();
    assert_eq!(payload.get("handoff_type").and_then(Value::as_str), Some("scope_transfer"));
    assert_eq!(payload.get("handoff_id").and_then(Value::as_i64), Some(handoff_id));
    let ack = w2.call(&mut log, "wait_for_handoff", obj(vec![("handoff_id", json::i(handoff_id)), ("mode", s("ack")), ("agent_id", s("w2"))]));
    assert!(!ack.is_error, "{}", ack.text);
    let done = w1.call(&mut log, "wait_for_handoff", obj(vec![("handoff_id", json::i(handoff_id)), ("mode", s("complete"))]));
    assert!(!done.is_error, "{}", done.text);

    // Messages: the planner asks w1 for a review note; w1 reads and clears it.
    planner.call(&mut log, "send_message", obj(vec![
        ("from_agent_id", s("planner")),
        ("to_agent_id", s("w1")),
        ("message_type", s("review")),
        ("payload", obj(vec![("task", s("T-api")), ("ask", s("note the retry limits"))])),
    ]));
    let unread = w1.call(&mut log, "manage_messages", obj(vec![("action", s("get")), ("agent_id", s("w1")), ("unread_only", json::b(true))]));
    assert_eq!(unread.value.get("count").and_then(Value::as_i64), Some(1));
    w1.call(&mut log, "manage_messages", obj(vec![("action", s("mark_read")), ("agent_id", s("w1"))]));

    // Errors an agent can act on.
    let dup = planner.call(&mut log, "create_task", obj(vec![("task_id", s("T-api")), ("parent_agent_id", s("planner")), ("description", s("again"))]));
    assert!(dup.is_error && dup.text.contains("T-api already exists"));
    let missing = planner.call(&mut log, "assign_task", obj(vec![("task_id", s("T-api"))]));
    assert!(missing.is_error && missing.text.contains("assigned_agent_id"), "{}", missing.text);

    // The board, compact and full.
    let who = planner.call(&mut log, "who", obj(vec![]));
    assert!(who.text.contains("w2"));
    let status = planner.call(&mut log, "status", obj(vec![]));
    assert_eq!(status.value.get("registered_agents").and_then(Value::as_i64), Some(3));
    let tree = planner.call(&mut log, "get_agent_tree", obj(vec![("agent_id", s("planner"))]));
    assert!(tree.text.contains("w1") && tree.text.contains("w2"));

    // Leaving releases the leaver's locks.
    let gone = w2.call(&mut log, "deregister_agent", obj(vec![("agent_id", s("w2"))]));
    assert_eq!(gone.value.get("locks_released").and_then(Value::as_i64), Some(1), "{}", gone.text);

    planner.close();
    w1.close();
    w2.close();

    let mut by_tool: Vec<(String, Vec<f64>, usize)> = Vec::new();
    for row in &log {
        match by_tool.iter_mut().find(|(tool, _, _)| *tool == row.tool) {
            Some(entry) => {
                entry.1.push(row.millis);
                entry.2 = entry.2.max(row.bytes);
            }
            None => by_tool.push((row.tool.clone(), vec![row.millis], row.bytes)),
        }
    }
    println!("calls: {}", log.len());
    println!("{:<28} {:>5} {:>10} {:>10} {:>11}", "tool", "calls", "median ms", "max ms", "max bytes");
    for (tool, mut times, bytes) in by_tool {
        times.sort_by(|a, b| a.partial_cmp(b).unwrap());
        let median = times[times.len() / 2];
        let max = times[times.len() - 1];
        println!("{tool:<28} {:>5} {median:>10.2} {max:>10.2} {bytes:>11}", times.len());
    }
    let total: f64 = log.iter().filter(|row| row.tool != "wait_for_task").map(|row| row.millis).sum();
    println!("total ms excluding the wait: {total:.1}; agents: {:?}", ["planner", "w1", "w2"]);
    let bytes: usize = log.iter().map(|row| row.bytes).sum();
    println!("total reply text bytes over {} calls: {bytes}", log.len());
    let _ = log.iter().map(|row| row.agent).count();
}
