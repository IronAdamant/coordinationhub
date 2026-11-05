//! Compare CoordinationHub 0.8.0 (Python) and the Rust binary.
//!
//! Usage: cargo run --release --example parity -- [RUST_BINARY] [--python EXE]
//!
//! The Python side is the unmodified 0.8.0 tree from the `python-legacy`
//! branch, run with PYTHONPATH and the `mcp` 1.30 package (the version the
//! legacy stdio server works with). Neither side is patched; both use the
//! real clock. Time fields, pids, temp paths and correlation ids are masked;
//! everything else is compared. A difference whose title matches ALLOWED is
//! a documented one (MIGRATION.md) and is recorded in the ledger; for those,
//! the Rust side is also checked against the documented reply so a change
//! there still fails. Any other difference fails the run (exit 1).
//!
//! Python writes CRLF and the ANSI code page to pipes on Windows; the
//! Python child runs with PYTHONUTF8=1 and line endings are normalised.
//!
//! Since 1.1.0 the Rust MCP replies are compact JSON. They are compared by
//! value: each Rust reply that parses is re-serialised with the 0.8.0
//! layout (indent 2, ASCII escapes) before the text comparison, and one
//! check confirms the Rust text itself is compact.

use coordinationhub::json::{self, Value};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};

/// Time fields masked in tool replies (type kept: int vs float).
const TIME_FIELDS: &[&str] = &[
    "started_at", "last_heartbeat", "expires_at", "created_at", "locked_at",
    "run_at", "assigned_at", "declared_at", "updated_at", "spawned_at",
    "acknowledged_at", "completed_at", "satisfied_at", "first_attempt_at",
    "last_attempt_at", "dead_letter_at", "acquired_at", "consumed_at", "read_at",
    "waited_s", "pid",
];

/// Ledger titles that MIGRATION.md documents as deliberate differences.
const ALLOWED: &[&str] = &[
    "initialize.serverInfo.version",
    "status.tools",
    "register_agent.worktree_root",
    "satisfy unknown dependency",
    "force exclusive evicts every shared holder",
    "validation wording",
    "duplicate task id",
    "unknown tool",
    "integer-valued float",
    "readable output fix",
    "doctor",
    "help layout of Python 3.13",
    "compact MCP replies",
    "explicit handoff tools",
    "assign_task refuses a held task",
    "lease get without agent_id",
    "expired lease reads as no leader",
    "refresh of an expired lease is refused",
    "version flag and assign-task --force",
];

/// Tools in the Rust tools/list that 0.8.0 does not have.
const RUST_ONLY_TOOLS: &[&str] = &["who", "acknowledge_handoff", "complete_handoff", "cancel_handoff"];

/// The Rust `status.tools` count: 0.8.0's 50 plus RUST_ONLY_TOOLS.
const RUST_TOOLS: usize = 54;

/// The 1.1.0 parser additions, removed from Rust CLI output before it is
/// compared with 0.8.0's: `-V/--version` at the top and `assign-task --force`.
/// Returns the text and whether anything was removed.
fn without_new_flags(text: &str) -> (String, bool) {
    // acquire-lock has a --force of its own; only assign-task's is new.
    let assign = text.contains("coordinationhub assign-task");
    let mut out = String::new();
    for line in text.split_inclusive('\n') {
        let trimmed = line.trim();
        if trimmed.starts_with("-V, --version ")
            || (assign && (trimmed == "[--force]" || trimmed.starts_with("--force ")))
        {
            continue;
        }
        let line = line.replacen("coordinationhub [-h] [-V]", "coordinationhub [-h]", 1);
        out.push_str(&if assign { line.replacen(" [--force]", "", 1) } else { line });
    }
    let changed = out != text;
    (out, changed)
}

fn strip_new_flags(out: &(i32, String, String)) -> ((i32, String, String), bool) {
    let (stdout, a) = without_new_flags(&out.1);
    let (stderr, b) = without_new_flags(&out.2);
    ((out.0, stdout, stderr), a || b)
}

// ------------------------------------------------------------------
// Ledger
// ------------------------------------------------------------------

#[derive(Default)]
struct Ledger {
    items: Vec<String>,
}

impl Ledger {
    fn diff(&mut self, title: &str, python: &str, rust: &str) {
        println!("DIFF {title}");
        println!("--- python ---");
        println!("{}", clip(python, 800));
        println!("--- rust ---");
        println!("{}", clip(rust, 800));
        self.items.push(title.to_string());
    }

    fn note(&mut self, title: &str, why: &str) {
        println!("LEDGER {title} {why}");
        self.items.push(title.to_string());
    }

    fn unexpected(&self) -> Vec<&String> {
        self.items
            .iter()
            .filter(|item| !ALLOWED.iter().any(|token| item.contains(token)))
            .collect()
    }
}

fn clip(text: &str, max: usize) -> String {
    text.chars().take(max).collect()
}

// ------------------------------------------------------------------
// Text normalisation
// ------------------------------------------------------------------

/// Replace `"field": <number>` for time fields with the number's type.
fn mask_time_literals(text: &str) -> String {
    let bytes = text.as_bytes();
    let mut out = String::with_capacity(text.len());
    let mut index = 0;
    'scan: while index < bytes.len() {
        if bytes[index] == b'"' {
            for field in TIME_FIELDS {
                let quoted = format!("\"{field}\"");
                if text[index..].starts_with(&quoted) {
                    let mut cursor = index + quoted.len();
                    while cursor < bytes.len() && bytes[cursor].is_ascii_whitespace() {
                        cursor += 1;
                    }
                    if cursor < bytes.len() && bytes[cursor] == b':' {
                        cursor += 1;
                        while cursor < bytes.len() && bytes[cursor].is_ascii_whitespace() {
                            cursor += 1;
                        }
                        let start = cursor;
                        if cursor < bytes.len() && bytes[cursor] == b'-' {
                            cursor += 1;
                        }
                        let digits = cursor;
                        while cursor < bytes.len()
                            && (bytes[cursor].is_ascii_digit()
                                || matches!(bytes[cursor], b'.' | b'e' | b'E' | b'+' | b'-'))
                        {
                            cursor += 1;
                        }
                        if cursor > digits {
                            let literal = &text[start..cursor];
                            let kind = if literal.contains(['.', 'e', 'E']) { "float" } else { "int" };
                            out.push_str(&format!("\"{field}\":<{kind}>"));
                            index = cursor;
                            continue 'scan;
                        }
                    }
                }
            }
        }
        let ch = text[index..].chars().next().unwrap();
        out.push(ch);
        index += ch.len_utf8();
    }
    out
}

/// CLI output: CRLF, temp paths, epoch stamps, ISO stamps and pids masked.
fn normalise(text: &str, dirs: &[(&Path, &str)]) -> String {
    let mut text = text.replace("\r\n", "\n");
    for (dir, tag) in dirs {
        let mut forms: Vec<String> = Vec::new();
        for base in [dir.to_path_buf(), coordinationhub::util::soft_resolve(dir)] {
            let plain = base.display().to_string();
            forms.push(plain.replace('\\', "\\\\"));
            forms.push(plain.replace('\\', "/"));
            forms.push(plain);
        }
        forms.sort_by_key(|form| std::cmp::Reverse(form.len()));
        for form in forms {
            text = text.replace(&form, tag);
        }
    }
    let mut out = String::with_capacity(text.len());
    let chars: Vec<char> = text.chars().collect();
    let mut index = 0;
    while index < chars.len() {
        let at_boundary = index == 0 || !chars[index - 1].is_ascii_alphanumeric();
        if at_boundary && chars[index].is_ascii_digit() {
            let run = chars[index..].iter().take_while(|ch| ch.is_ascii_digit()).count();
            let after = index + run;
            // 2026-11-05 13:05:00(.123)?
            if run == 4 && chars.get(after) == Some(&'-') && is_datetime(&chars[index..]) {
                {
                    let mut end = index + 19;
                    if chars.get(end) == Some(&'.') {
                        end += 1;
                        while end < chars.len() && chars[end].is_ascii_digit() {
                            end += 1;
                        }
                    }
                    out.push_str("<DT>");
                    index = end;
                    continue;
                }
            }
            if run == 10 {
                let mut end = after;
                if chars.get(end) == Some(&'.') {
                    end += 1;
                    while end < chars.len() && chars[end].is_ascii_digit() {
                        end += 1;
                    }
                }
                if end == chars.len() || !chars[end].is_ascii_alphanumeric() {
                    out.push_str("<T>");
                    index = end;
                    continue;
                }
            }
            out.extend(&chars[index..after]);
            index = after;
            continue;
        }
        out.push(chars[index]);
        index += 1;
    }
    let mut masked = String::with_capacity(out.len());
    let mut rest = out.as_str();
    while let Some(at) = rest.find("\"pid\": ") {
        masked.push_str(&rest[..at + 7]);
        let tail = &rest[at + 7..];
        let digits = tail.chars().take_while(char::is_ascii_digit).count();
        masked.push_str("<pid>");
        rest = &tail[digits..];
    }
    masked.push_str(rest);
    masked
}

/// `dddd-dd-dd[ T]dd:dd:dd` at the start of `chars`.
fn is_datetime(chars: &[char]) -> bool {
    let shape = "dddd-dd-dd?dd:dd:dd";
    chars.len() >= shape.len()
        && shape.chars().zip(chars).all(|(want, got)| match want {
            'd' => got.is_ascii_digit(),
            '?' => *got == ' ' || *got == 'T',
            other => *got == other,
        })
}

fn tool_text(reply: &Value) -> String {
    reply
        .get("result")
        .and_then(|result| result.get("content"))
        .and_then(Value::as_array)
        .and_then(|items| items.first())
        .and_then(|item| item.get("text"))
        .and_then(Value::as_str)
        .map(str::to_string)
        .unwrap_or_else(|| json::dumps(reply))
}

/// The Rust reply text in the 0.8.0 layout: compact JSON is parsed and
/// written again with indent 2, so the comparison is by value.
fn rust_text(reply: &Value) -> String {
    let text = tool_text(reply);
    match json::parse(&text) {
        Ok(value) if text.starts_with('{') || text.starts_with('[') => json::dumps_pretty(&value),
        _ => text,
    }
}

fn is_error(reply: &Value) -> bool {
    reply
        .get("result")
        .and_then(|result| result.get("isError"))
        .and_then(Value::as_bool)
        .unwrap_or(false)
}

// ------------------------------------------------------------------
// Processes
// ------------------------------------------------------------------

struct Stdio2 {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
    seq: i64,
}

impl Stdio2 {
    fn start(mut command: Command) -> Stdio2 {
        let mut child = command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .unwrap_or_else(|err| panic!("cannot start {command:?}: {err}"));
        let stdin = child.stdin.take().unwrap();
        let stdout = BufReader::new(child.stdout.take().unwrap());
        Stdio2 { child, stdin, stdout, seq: 0 }
    }

    fn request(&mut self, method: &str, params: Option<Value>) -> Value {
        self.seq += 1;
        let mut pairs = vec![
            ("jsonrpc", json::s("2.0")),
            ("id", json::i(self.seq)),
            ("method", json::s(method)),
        ];
        if let Some(params) = params {
            pairs.push(("params", params));
        }
        writeln!(self.stdin, "{}", json::dumps(&json::obj(pairs))).unwrap();
        self.stdin.flush().unwrap();
        let mut line = String::new();
        if self.stdout.read_line(&mut line).unwrap_or(0) == 0 {
            panic!("stdio closed during {method}");
        }
        json::parse(line.trim_end()).unwrap_or_else(|err| panic!("{err}: {line}"))
    }

    fn call(&mut self, tool: &str, args: Value) -> Value {
        self.request(
            "tools/call",
            Some(json::obj(vec![("name", json::s(tool)), ("arguments", args)])),
        )
    }

    fn initialize(&mut self) {
        self.request(
            "initialize",
            Some(json::obj(vec![
                ("protocolVersion", json::s("2024-11-05")),
                ("capabilities", json::obj(vec![])),
                (
                    "clientInfo",
                    json::obj(vec![("name", json::s("harness")), ("version", json::s("0"))]),
                ),
            ])),
        );
    }

    fn close(mut self) {
        drop(self.stdin);
        let _ = self.child.wait();
    }
}

struct Sides {
    python: String,
    legacy: PathBuf,
    rust: PathBuf,
    tmp: PathBuf,
    py_minor: u32,
}

impl Sides {
    fn env(&self, command: &mut Command) {
        command
            .env_remove("COORDINATIONHUB_NOW")
            .env_remove("COORDINATIONHUB_PID")
            .env_remove("COLUMNS")
            .env_remove("LINES")
            .env("COORDINATIONHUB_HOUSEKEEPING", "0")
            .env("PYTHONPATH", &self.legacy)
            .env("PYTHONUTF8", "1");
    }

    fn command(&self, side: Side) -> Command {
        let mut command = match side {
            Side::Python => {
                let mut command = Command::new(&self.python);
                command.args(["-m", "coordinationhub"]);
                command
            }
            Side::Rust => Command::new(&self.rust),
        };
        self.env(&mut command);
        command
    }

    fn server(&self, side: Side, store: &Path, project: &Path) -> Stdio2 {
        let mut command = self.command(side);
        command
            .arg("serve-mcp")
            .arg("--storage-dir")
            .arg(store)
            .arg("--project-root")
            .arg(project);
        let mut server = Stdio2::start(command);
        server.initialize();
        server
    }

    /// Run a CLI command with optional storage flags; return (rc, stdout, stderr).
    fn cli(&self, side: Side, args: &[String], dirs: Option<(&Path, &Path)>, cwd: &Path) -> (i32, String, String) {
        let mut command = self.command(side);
        command.args(args).current_dir(cwd);
        if let Some((store, project)) = dirs {
            command.arg("--storage-dir").arg(store).arg("--project-root").arg(project);
        }
        let out = command.output().expect("cli run");
        (
            out.status.code().unwrap_or(-1),
            String::from_utf8_lossy(&out.stdout).replace("\r\n", "\n"),
            String::from_utf8_lossy(&out.stderr).replace("\r\n", "\n"),
        )
    }

    fn fresh(&self, label: &str) -> (PathBuf, PathBuf) {
        let base = self.tmp.join(label);
        let store = base.join("store");
        let project = base.join("project");
        std::fs::create_dir_all(&store).unwrap();
        std::fs::create_dir_all(&project).unwrap();
        (store, project)
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Side {
    Python,
    Rust,
}

fn words(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut current = String::new();
    let mut quote: Option<char> = None;
    let mut started = false;
    for ch in text.chars() {
        match quote {
            Some(q) if ch == q => quote = None,
            Some(_) => current.push(ch),
            None if ch == '\'' || ch == '"' => {
                quote = Some(ch);
                started = true;
            }
            None if ch.is_whitespace() => {
                if started {
                    out.push(std::mem::take(&mut current));
                    started = false;
                }
            }
            None => {
                current.push(ch);
                started = true;
            }
        }
    }
    if started {
        out.push(current);
    }
    out
}

/// Outside Python 3.12, argparse wraps usage lines differently. Two CLI
/// results differ only in layout when the exit code, stdout and the final
/// stderr line (the error sentence) agree.
fn layout_only(left: &(i32, String, String), right: &(i32, String, String), py_minor: u32) -> bool {
    let last = |text: &str| text.lines().rev().find(|line| !line.trim().is_empty()).map(str::to_string);
    py_minor != 12 && left.0 == right.0 && left.1 == right.1 && last(&left.2) == last(&right.2)
}

fn obj(pairs: Vec<(&str, Value)>) -> Value {
    json::obj(pairs)
}

fn s(text: &str) -> Value {
    json::s(text)
}

// ------------------------------------------------------------------
// Checks ported from parity/harness.py
// ------------------------------------------------------------------

fn base_calls() -> Vec<(&'static str, Value)> {
    vec![
        ("register_agent", obj(vec![("agent_id", s("root"))])),
        ("register_agent", obj(vec![("agent_id", s("child")), ("parent_id", s("root"))])),
        ("register_agent", obj(vec![("agent_id", s("agent-\u{fc}-\u{6771}\u{4eac}"))])),
        ("heartbeat", obj(vec![("agent_id", s("root"))])),
        ("list_agents", obj(vec![("active_only", json::b(true))])),
        ("acquire_lock", obj(vec![("document_path", s("notes.md")), ("agent_id", s("root"))])),
        ("acquire_lock", obj(vec![("document_path", s("notes.md")), ("agent_id", s("child"))])),
        (
            "notify_change",
            obj(vec![("document_path", s("notes.md")), ("change_type", s("modified")), ("agent_id", s("root"))]),
        ),
        ("get_notifications", obj(vec![("limit", json::i(-1))])),
        (
            "create_task",
            obj(vec![("task_id", s("t-low")), ("parent_agent_id", s("root")), ("description", s("low")), ("priority", json::i(1))]),
        ),
        (
            "create_task",
            obj(vec![("task_id", s("t-high")), ("parent_agent_id", s("root")), ("description", s("high")), ("priority", json::i(5))]),
        ),
        ("query_tasks", obj(vec![("query_type", s("child")), ("parent_agent_id", s("root"))])),
        ("get_available_tasks", obj(vec![("agent_id", s("child"))])),
        ("status", obj(vec![])),
        ("get_agent_tree", obj(vec![])),
    ]
}

fn harness_checks(sides: &Sides, ledger: &mut Ledger) {
    let project = sides.tmp.join("project");
    let py_store = sides.tmp.join("py");
    let rs_store = sides.tmp.join("rs");
    for dir in [&project, &py_store, &rs_store] {
        std::fs::create_dir_all(dir).unwrap();
    }
    let mut py = sides.server(Side::Python, &py_store, &project);
    let mut rs = sides.server(Side::Rust, &rs_store, &project);
    let rust_count = format!("\"tools\": {RUST_TOOLS}");
    let mut pretty = 0;
    let mut compact = 0;
    for (index, (name, args)) in base_calls().into_iter().enumerate() {
        let left = tool_text(&py.call(name, args.clone()));
        let right_reply = rs.call(name, args);
        let raw = tool_text(&right_reply);
        if json::parse(&raw).is_ok() {
            if raw.contains('\n') || raw.contains("\": ") {
                pretty += 1;
            } else {
                compact += 1;
            }
        }
        let right = rust_text(&right_reply);
        let title = format!("{}:{name}", index + 1);
        let (mut left, mut right) = (left, right);
        if name == "status" && left.contains("\"tools\": 50") && right.contains(&rust_count) {
            ledger.note("status.tools", "rust also counts who and the three handoff tools");
            left = left.replace("\"tools\": 50", "\"tools\": 0");
            right = right.replace(&rust_count, "\"tools\": 0");
        }
        if mask_time_literals(&left) != mask_time_literals(&right) {
            ledger.diff(&title, &left, &right);
        }
    }
    if pretty > 0 || compact == 0 {
        ledger.items.push(format!("rust MCP replies are not compact JSON ({pretty} indented, {compact} compact)"));
    } else {
        ledger.note("compact MCP replies", "rust sends JSON without indentation; compared by value");
    }
    let custom = obj(vec![
        ("agent_id", s("rooted")),
        ("worktree_root", s(&sides.tmp.join("custom").display().to_string())),
    ]);
    let left = json::parse(&tool_text(&py.call("register_agent", custom.clone()))).unwrap_or(Value::Null);
    let right = json::parse(&tool_text(&rs.call("register_agent", custom.clone()))).unwrap_or(Value::Null);
    if left.get("worktree_root") == right.get("worktree_root") {
        ledger.items.push("register_agent.worktree_root unexpectedly matched".into());
    } else if right.get("worktree_root") != custom.get("worktree_root") {
        ledger.diff("rust register_agent reply lost the caller worktree_root", &json::dumps(&left), &json::dumps(&right));
    } else {
        ledger.note("register_agent.worktree_root", "python replies with the project root; rust with the caller value");
    }
    let names = |reply: Value| -> Vec<String> {
        reply
            .get("result")
            .and_then(|r| r.get("tools"))
            .and_then(Value::as_array)
            .unwrap_or(&[])
            .iter()
            .filter_map(|tool| tool.get("name").and_then(Value::as_str).map(str::to_string))
            .collect()
    };
    let py_names = names(py.request("tools/list", None));
    let rs_names = names(rs.request("tools/list", None));
    let missing: Vec<&String> = py_names.iter().filter(|n| !rs_names.contains(n)).collect();
    if !missing.is_empty() {
        ledger.items.push(format!("missing tools {missing:?}"));
    }
    let extra: Vec<&String> = rs_names
        .iter()
        .filter(|n| !py_names.contains(n) && !RUST_ONLY_TOOLS.contains(&n.as_str()))
        .collect();
    if !extra.is_empty() {
        ledger.items.push(format!("extra tools {extra:?}"));
    }
    if RUST_ONLY_TOOLS.iter().all(|tool| rs_names.iter().any(|n| n == tool)) {
        ledger.note("explicit handoff tools", "acknowledge_handoff, complete_handoff and cancel_handoff are listed");
    } else {
        ledger.items.push(format!("rust tools/list lacks one of {RUST_ONLY_TOOLS:?}"));
    }
    let safe = |name: &str| -> Value {
        match name {
            "list_agents" => obj(vec![("active_only", json::b(false))]),
            "get_notifications" => obj(vec![("limit", json::i(5))]),
            "query_tasks" => obj(vec![("query_type", s("all"))]),
            "manage_leases" => obj(vec![("action", s("get")), ("agent_id", s("root"))]),
            "manage_work_intents" => obj(vec![("action", s("get")), ("agent_id", s("root"))]),
            "run_assessment" => obj(vec![("format", s("json")), ("graph_agent_id", s("role-not-in-this-hub"))]),
            _ => obj(vec![]),
        }
    };
    let mut sorted = py_names.clone();
    sorted.sort();
    for name in sorted {
        let mut left = tool_text(&py.call(&name, safe(&name)));
        let mut right = rust_text(&rs.call(&name, safe(&name)));
        if name == "run_assessment" {
            for text in [&mut left, &mut right] {
                if let Some(at) = text.find("Run At:** ") {
                    let end = text[at..].find("\\n").map(|e| at + e).unwrap_or(text.len());
                    text.replace_range(at + 10..end, "");
                }
            }
        }
        if name == "status" {
            left = left.replace("\"tools\": 50", "\"tools\": 0");
            right = right.replace(&rust_count, "\"tools\": 0");
        }
        if mask_time_literals(&left) != mask_time_literals(&right) {
            ledger.diff(&format!("tool:{name}"), &left, &right);
        }
    }
    py.close();
    rs.close();

    // Every Python command exists in Rust.
    let commands: Vec<&str> = coordinationhub::cli_catalog::COMMANDS.iter().map(|cmd| cmd.name).collect();
    for command in &commands {
        if matches!(*command, "watch" | "auto-start-dashboard" | "init" | "serve" | "serve-mcp" | "serve-sse") {
            continue;
        }
        let (_, _, err) = sides.cli(Side::Rust, &[command.to_string(), "--help".into()], None, &sides.tmp);
        if err.contains("invalid choice") {
            ledger.items.push(format!("cli missing {command}"));
        }
    }
    for (label, args) in [("cli heartbeat", vec!["heartbeat", "root"]), ("cli cancel-spawn", vec!["cancel-spawn", "missing-row"])] {
        let args: Vec<String> = args.into_iter().map(str::to_string).collect();
        let (py_store, py_project) = sides.fresh(&format!("{label}-py"));
        let (rs_store, rs_project) = sides.fresh(&format!("{label}-rs"));
        let left = sides.cli(Side::Python, &args, Some((&py_store, &py_project)), &sides.tmp);
        let right = sides.cli(Side::Rust, &args, Some((&rs_store, &rs_project)), &sides.tmp);
        if left.0 != right.0 {
            ledger.items.push(format!("{label} exit {} vs {}", left.0, right.0));
        }
        if (left.1.clone(), left.2.clone()) != (right.1.clone(), right.2.clone()) {
            ledger.diff(&format!("{label} output"), &(left.1 + &left.2), &(right.1 + &right.2));
        }
    }
    let help: Vec<String> = vec!["acquire-lock".into(), "--help".into()];
    let left = sides.cli(Side::Python, &help, None, &sides.tmp);
    let right = sides.cli(Side::Rust, &help, None, &sides.tmp);
    if (left.0, &left.1) != (right.0, &right.1) {
        if sides.py_minor == 12 {
            ledger.diff("cli help acquire-lock", &left.1, &right.1);
        } else {
            ledger.note("help layout of Python 3.13: acquire-lock", "argparse wraps differently outside 3.12");
        }
    }
    let unknown: Vec<String> = vec!["heartbeat".into(), "ada".into(), "--nope".into()];
    let left = sides.cli(Side::Python, &unknown, None, &sides.tmp);
    let (right, stripped) = strip_new_flags(&sides.cli(Side::Rust, &unknown, None, &sides.tmp));
    if stripped {
        ledger.note("version flag and assign-task --force (cli unknown flag)", "usage lists the 1.1.0 flags");
    }
    if left.0 != right.0 || left.2 != right.2 {
        if layout_only(&left, &right, sides.py_minor) {
            ledger.note("help layout of Python 3.13: cli unknown flag", "usage wraps differently outside 3.12");
        } else {
            ledger.diff("cli unknown flag", &left.2, &right.2);
        }
    }
    let cancel: Vec<String> = vec!["cancel-spawn".into(), "missing-row".into(), "--json".into()];
    let (py_store, py_project) = sides.fresh("json-py");
    let (rs_store, rs_project) = sides.fresh("json-rs");
    let left = sides.cli(Side::Python, &cancel, Some((&py_store, &py_project)), &sides.tmp);
    let right = sides.cli(Side::Rust, &cancel, Some((&rs_store, &rs_project)), &sides.tmp);
    if left.0 != right.0 || left.1 != right.1 {
        ledger.diff("cli cancel-spawn json", &format!("{} {}", left.0, left.1), &format!("{} {}", right.0, right.1));
    }
}

// ------------------------------------------------------------------
// Extended checks (fifth review)
// ------------------------------------------------------------------

/// Expectation for a documented MCP difference: the Rust reply must keep
/// the documented shape, and Python must still show the 0.8.0 behaviour.
struct Documented {
    title: &'static str,
    rust_error: bool,
    rust_prefix: &'static str,
    python_prefix: &'static str,
}

fn mcp_scenarios(sides: &Sides, ledger: &mut Ledger) {
    let reg = |id: &str| ("register_agent", obj(vec![("agent_id", s(id))]));
    let lock = |path: &str, agent: &str| ("acquire_lock", obj(vec![("document_path", s(path)), ("agent_id", s(agent))]));
    type Step = (&'static str, Value);
    let scenarios: Vec<(&str, Vec<Step>)> = vec![
        (
            "hotspots",
            vec![
                reg("oboe"), reg("tabla"), reg("kazoo-\u{f1}"),
                lock("score/yodel.rs", "oboe"), lock("score/yodel.rs", "tabla"),
                lock("score/bolero.rs", "tabla"), lock("score/bolero.rs", "kazoo-\u{f1}"),
                lock("score/minuet.rs", "kazoo-\u{f1}"), lock("score/minuet.rs", "oboe"), lock("score/minuet.rs", "tabla"),
                ("get_contention_hotspots", obj(vec![])),
                ("get_contention_hotspots", obj(vec![("limit", json::i(1))])),
                ("get_contention_hotspots", obj(vec![("limit", json::i(-1))])),
                ("get_contention_hotspots", obj(vec![("limit", json::i(0))])),
                ("get_conflicts", obj(vec![("limit", json::i(-1))])),
                ("get_conflicts", obj(vec![("agent_id", s("kazoo-\u{f1}")), ("limit", json::i(2))])),
            ],
        ),
        (
            "tasks",
            vec![
                reg("sitar"),
                ("create_task", obj(vec![("task_id", s("RAGA-1")), ("parent_agent_id", s("sitar")), ("description", s("alap"))])),
                ("create_task", obj(vec![("task_id", s("RAGA-1")), ("parent_agent_id", s("sitar")), ("description", s("jor"))])),
                ("create_task", obj(vec![("task_id", s("RAGA-3")), ("parent_agent_id", s("sitar")), ("description", s("d")), ("priority", s("high"))])),
                ("query_tasks", obj(vec![("query_type", s("all"))])),
                // Documented: Rust creates this task, 0.8.0 raises. Last, so
                // the extra row does not reach a later compare.
                ("create_task", obj(vec![("task_id", s("RAGA-2")), ("parent_agent_id", s("sitar")), ("description", s("d")), ("priority", json::f(3.0))])),
            ],
        ),
        (
            "leases",
            vec![
                reg("bugle"), reg("lyre_9"),
                ("acquire_coordinator_lease", obj(vec![("agent_id", s("bugle")), ("ttl", json::i(40))])),
                ("acquire_coordinator_lease", obj(vec![("agent_id", s("lyre_9")), ("ttl", json::i(9))])),
                ("manage_leases", obj(vec![("action", s("release")), ("agent_id", s("bugle"))])),
                ("acquire_coordinator_lease", obj(vec![("agent_id", s("lyre_9"))])),
                ("manage_leases", obj(vec![("action", s("release")), ("agent_id", s("lyre_9"))])),
                ("acquire_coordinator_lease", obj(vec![("agent_id", s("bugle")), ("ttl", json::f(2.5))])),
                ("manage_leases", obj(vec![("action", s("claim")), ("agent_id", s("lyre_9")), ("ttl", json::i(13))])),
                ("manage_leases", obj(vec![("action", s("get"))])),
            ],
        ),
        (
            // The holder's deregistration releases the lease, as in 0.8.0;
            // then a lease whose TTL has run out (ttl 0).
            "lease-holder",
            vec![
                reg("oud_1"),
                ("acquire_coordinator_lease", obj(vec![("agent_id", s("oud_1")), ("ttl", json::i(300))])),
                ("deregister_agent", obj(vec![("agent_id", s("oud_1"))])),
                ("manage_leases", obj(vec![("action", s("get")), ("agent_id", s("oud_1"))])),
                ("acquire_coordinator_lease", obj(vec![("agent_id", s("viol")), ("ttl", json::i(0))])),
                ("manage_leases", obj(vec![("action", s("get")), ("agent_id", s("expiry-check"))])),
                // Documented: Rust refuses, 0.8.0 renews. Last.
                ("manage_leases", obj(vec![("action", s("refresh")), ("agent_id", s("viol"))])),
            ],
        ),
        (
            "claims",
            vec![
                reg("fife"), reg("harp"),
                ("create_task", obj(vec![("task_id", s("SOLO-1")), ("parent_agent_id", s("fife")), ("description", s("cadenza"))])),
                ("assign_task", obj(vec![("task_id", s("SOLO-1")), ("assigned_agent_id", s("fife"))])),
                ("assign_task", obj(vec![("task_id", s("SOLO-1")), ("assigned_agent_id", s("fife"))])),
                // Documented: Rust refuses, 0.8.0 reassigns. Last, so the
                // states do not diverge before a later compare.
                ("assign_task", obj(vec![("task_id", s("SOLO-1")), ("assigned_agent_id", s("harp"))])),
            ],
        ),
        (
            "messages",
            vec![
                reg("gong"),
                ("register_agent", obj(vec![("agent_id", s("zither-\u{fc}")), ("parent_id", s("gong"))])),
                ("send_message", obj(vec![("from_agent_id", s("gong")), ("to_agent_id", s("zither-\u{fc}")), ("message_type", s("strum")), ("payload", json::arr(vec![json::i(1), s("two"), Value::Null]))])),
                ("broadcast", obj(vec![("agent_id", s("gong")), ("message", s("hold")), ("handoff_targets", json::arr(vec![s("zither-\u{fc}")])), ("document_path", s("score/aria.md"))])),
                // The explicit tools against 0.8.0's wait_for_handoff modes.
                ("acknowledge_handoff", obj(vec![("handoff_id", json::i(1)), ("agent_id", s("zither-\u{fc}"))])),
                ("complete_handoff", obj(vec![("handoff_id", json::i(1))])),
                ("broadcast", obj(vec![("agent_id", s("gong")), ("handoff_targets", json::arr(vec![s("zither-\u{fc}")]))])),
                ("cancel_handoff", obj(vec![("handoff_id", json::i(2))])),
                ("manage_messages", obj(vec![("action", s("get")), ("agent_id", s("zither-\u{fc}"))])),
                ("manage_messages", obj(vec![("action", s("get")), ("agent_id", s("zither-\u{fc}")), ("limit", json::i(-1))])),
                ("get_notifications", obj(vec![("agent_id", s("zither-\u{fc}")), ("timeout_s", json::i(-1))])),
                ("get_notifications", obj(vec![("limit", json::i(-1))])),
                ("acquire_lock", obj(vec![("document_path", s("score/aria.md")), ("agent_id", s("zither-\u{fc}")), ("ttl", json::i(-4))])),
                ("no_such_tool_x", obj(vec![])),
                ("report_subagent_spawned", obj(vec![("parent_agent_id", s("gong")), ("child_agent_id", s("gong/k-2")), ("subagent_type", s("q")), ("caller_agent_id", s("zither-\u{fc}"))])),
                ("deregister_agent", obj(vec![("agent_id", s("gong"))])),
            ],
        ),
    ];
    let documented = |tool: &str, args: &Value| -> Option<Documented> {
        match tool {
            "create_task" if args.get("description").and_then(Value::as_str) == Some("jor") => Some(Documented {
                title: "duplicate task id",
                rust_error: true,
                rust_prefix: "Error: task RAGA-1 already exists; ",
                python_prefix: "Error: Internal tool execution error",
            }),
            "create_task" if matches!(args.get("priority"), Some(Value::Float(_))) => Some(Documented {
                title: "integer-valued float",
                rust_error: false,
                rust_prefix: "{\n  \"created\": true",
                python_prefix: "Error: Internal tool execution error",
            }),
            "no_such_tool_x" => Some(Documented {
                title: "unknown tool",
                rust_error: true,
                rust_prefix: "Error: Unknown tool: 'no_such_tool_x'.",
                python_prefix: "Error: Internal tool execution error",
            }),
            "manage_messages" if args.get("limit") == Some(&json::i(-1)) => Some(Documented {
                title: "validation wording",
                rust_error: true,
                rust_prefix: "Input validation error: ",
                python_prefix: "Input validation error: ",
            }),
            "assign_task" if args.get("assigned_agent_id").and_then(Value::as_str) == Some("harp") => Some(Documented {
                title: "assign_task refuses a held task",
                rust_error: false,
                rust_prefix: "{\n  \"assigned\": false,\n  \"reason\": \"already_assigned\",\n  \"task_id\": \"SOLO-1\",\n  \"assigned_agent_id\": \"fife\"",
                python_prefix: "{\n  \"assigned\": true",
            }),
            "manage_leases" if args.get("agent_id").and_then(Value::as_str) == Some("expiry-check") => Some(Documented {
                title: "expired lease reads as no leader",
                rust_error: false,
                rust_prefix: "{\n  \"leader\": null\n}",
                python_prefix: "{\n  \"leader\": {\n    \"lease_name\": \"COORDINATOR_LEADER\",\n    \"holder_id\": \"viol\"",
            }),
            "manage_leases" if args.get("action").and_then(Value::as_str) == Some("refresh") && args.get("agent_id").and_then(Value::as_str) == Some("viol") => Some(Documented {
                title: "refresh of an expired lease is refused",
                rust_error: false,
                rust_prefix: "{\n  \"refreshed\": false,\n  \"error\": \"Lease expired at ",
                python_prefix: "{\n  \"refreshed\": true",
            }),
            "manage_leases" if args.get("agent_id").is_none() => Some(Documented {
                title: "lease get without agent_id",
                rust_error: false,
                rust_prefix: "{\n  \"leader\": {",
                python_prefix: "Input validation error: ",
            }),
            _ => None,
        }
    };
    for (name, steps) in scenarios {
        let (py_store, project) = sides.fresh(&format!("mcp-{name}-py"));
        let (rs_store, rs_project) = sides.fresh(&format!("mcp-{name}-rs"));
        for root in [&project, &rs_project] {
            std::fs::create_dir_all(root.join("score")).unwrap();
            std::fs::write(root.join("score/aria.md"), "# lento\n").unwrap();
        }
        let mut py = sides.server(Side::Python, &py_store, &project);
        let mut rs = sides.server(Side::Rust, &rs_store, &rs_project);
        for (index, (tool, args)) in steps.into_iter().enumerate() {
            // 0.8.0 reaches the explicit handoff tools as wait_for_handoff modes.
            let (py_tool, py_args) = match tool {
                "acknowledge_handoff" | "complete_handoff" | "cancel_handoff" => {
                    let mode = match tool {
                        "acknowledge_handoff" => "ack",
                        "complete_handoff" => "complete",
                        _ => "cancel",
                    };
                    let mut with_mode = args.clone();
                    if let Value::Object(pairs) = &mut with_mode {
                        pairs.push(("mode".into(), s(mode)));
                    }
                    ("wait_for_handoff", with_mode)
                }
                _ => (tool, args.clone()),
            };
            let left_reply = py.call(py_tool, py_args);
            let right_reply = rs.call(tool, args.clone());
            let left = mask_time_literals(&tool_text(&left_reply)).replace("\\\\", "/");
            let right = mask_time_literals(&rust_text(&right_reply)).replace("\\\\", "/");
            let left = normalise(&left, &[(&project, "<PROJ>"), (&py_store, "<STORE>")]);
            let right = normalise(&right, &[(&rs_project, "<PROJ>"), (&rs_store, "<STORE>")]);
            let title = format!("mcp {name} {index}:{tool}");
            if let Some(doc) = documented(tool, &args) {
                let rust_ok = is_error(&right_reply) == doc.rust_error && right.starts_with(doc.rust_prefix);
                let python_ok = left.starts_with(doc.python_prefix);
                if rust_ok && python_ok {
                    if left != right || is_error(&left_reply) != is_error(&right_reply) {
                        ledger.note(&format!("{} ({title})", doc.title), "documented in MIGRATION.md");
                    }
                } else {
                    ledger.diff(&format!("{title} no longer matches the documented reply"), &left, &right);
                }
                continue;
            }
            if left != right || is_error(&left_reply) != is_error(&right_reply) {
                ledger.diff(&title, &left, &right);
            }
        }
        py.close();
        rs.close();
    }
}

/// A CLI sequence run in JSON and readable modes on both sides.
fn cli_sequence(sides: &Sides, ledger: &mut Ledger) {
    let seq = [
        "status", "list-agents", "get-conflicts", "query-tasks all", "get-leader", "list-pending-spawns ocarina",
        "register ocarina", "register ocarina/reed-1 --parent-id ocarina", "register cello_4",
        "agent-relations ocarina/reed-1", "agent-tree ocarina",
        "acquire-lock stave/clef.ts ocarina/reed-1 --ttl 73", "acquire-lock stave/clef.ts cello_4",
        "acquire-lock stave/bar.ts cello_4 --region-start 4 --region-end 18",
        "acquire-lock stave/neg.ts cello_4 --ttl -7", "acquire-lock stave/neg2.ts cello_4 --region-start -2 --region-end 5",
        "lock-status stave/clef.ts", "list-locks", "list-locks --agent cello_4",
        "get-conflicts", "get-conflicts --limit -1", "get-conflicts --document-path stave/clef.ts",
        "contention-hotspots", "contention-hotspots --limit -1",
        "notify-change stave/clef.ts modified ocarina/reed-1", "notify-change stave/tonic.ts created cello_4",
        "get-notifications --exclude cello_4", "get-notifications --limit -1", "get-notifications --limit=-2",
        "wait-for-notifications ocarina --timeout -1",
        "send-message ocarina cello_4 cue-m --payload 'forte now'", "get-messages cello_4 --unread", "mark-messages-read cello_4",
        "create-task TUN-1 ocarina 'tune the strings'", "create-task TUN-2 ocarina 'rosin bows' --depends-on TUN-1 --priority 8",
        "create-task TUN-3 ocarina 'oil valves' --priority=-4", "create-task TUN-1 ocarina dup",
        "create-subtask TUN-1.x TUN-1 ocarina 'restring a' --priority 6", "assign-task TUN-1 ocarina/reed-1",
        "get-available-tasks", "query-tasks all", "query-tasks task --task-id TUN-2", "query-tasks tree --root-task-id TUN-1",
        "update-task-status TUN-3 failed --error 'cracked bridge'", "task-failure-history TUN-3", "dead-letter-queue",
        "update-task-status TUN-1 completed --summary coda", "wait-for-task TUN-1 --timeout 0.3",
        "declare-dependency cello_4 ocarina --depends-on-task-id TUN-2", "satisfy-dependency 6161", "get-all-dependencies",
        "broadcast ocarina --message 'tempo up'", "broadcast ocarina --handoff-targets cello_4 --document-path stave/fermata.ts",
        "get-messages cello_4", "get-handoffs",
        "acquire-coordinator-lease ocarina --ttl 29", "acquire-coordinator-lease cello_4 --ttl 9", "get-leader", "leader-status",
        "claim-leadership cello_4", "release-coordinator-lease ocarina", "claim-leadership cello_4 --ttl 37",
        "spawn-subagent ocarina copyist-v --description 'copy parts'", "spawn-subagent ocarina copyist-v", "list-pending-spawns ocarina",
        "report-subagent-spawned ocarina ocarina/solo-6 --subagent-type copyist-v", "cancel-spawn nospawn-417",
        "scan-project", "status", "list-agents --all",
    ];
    let documented_prose = [
        "get-conflicts", "query-tasks", "get-available-tasks", "get-leader", "leader-status", "list-pending-spawns",
        "claim-leadership", "wait-for-task", "scan-project", "task-failure-history", "report-subagent-spawned",
    ];
    for json_mode in [true, false] {
        let mode = if json_mode { "json" } else { "prose" };
        let (py_store, py_project) = sides.fresh(&format!("seq-{mode}-py"));
        let (rs_store, rs_project) = sides.fresh(&format!("seq-{mode}-rs"));
        for root in [&py_project, &rs_project] {
            std::fs::create_dir_all(root.join("stave")).unwrap();
            std::fs::write(root.join("stave/clef.ts"), "let a=1\n").unwrap();
            std::fs::write(root.join("stave/r.py"), "x=1\n").unwrap();
        }
        for line in seq {
            let mut args = words(line);
            if json_mode {
                args.push("--json".into());
            }
            let left = sides.cli(Side::Python, &args, Some((&py_store, &py_project)), &py_project);
            let right = sides.cli(Side::Rust, &args, Some((&rs_store, &rs_project)), &rs_project);
            let norm = |out: &(i32, String, String), store: &Path, project: &Path| {
                let dirs = [(store, "<STORE>"), (project, "<PROJ>")];
                (out.0, mask_time_literals(&normalise(&out.1, &dirs)), normalise(&out.2, &dirs))
            };
            let mut left = norm(&left, &py_store, &py_project);
            let mut right = norm(&right, &rs_store, &rs_project);
            let title = format!("cli {mode} `{line}`");
            if line == "status" || line.starts_with("list-agents") {
                left.1 = left.1.replace("\"tools\": 50", "\"tools\": 0").replace("Tools: 50", "Tools: 0");
                right.1 = right
                    .1
                    .replace(&format!("\"tools\": {RUST_TOOLS}"), "\"tools\": 0")
                    .replace(&format!("Tools: {RUST_TOOLS}"), "Tools: 0");
            }
            if left == right {
                continue;
            }
            if line == "satisfy-dependency 6161" {
                if right.1.contains("not_found") || right.1.contains("not found") {
                    ledger.note(&format!("satisfy unknown dependency ({title})"), "documented in MIGRATION.md");
                } else {
                    ledger.diff(&title, &left.1, &right.1);
                }
                continue;
            }
            if line == "create-task TUN-1 ocarina dup" {
                let rust_text = if json_mode { &right.1 } else { &right.2 };
                if right.0 == 1 && rust_text.contains("task TUN-1 already exists") {
                    ledger.note(&format!("duplicate task id ({title})"), "documented in MIGRATION.md");
                } else {
                    ledger.diff(&title, &format!("{:?}", left), &format!("{:?}", right));
                }
                continue;
            }
            let command = args[0].as_str();
            if !json_mode && documented_prose.contains(&command) && right.0 == 0 && left.0 <= 1 {
                ledger.note(&format!("readable output fix ({title})"), "0.8.0 read keys its replies lack");
                continue;
            }
            ledger.diff(&title, &format!("rc {}\n{}{}", left.0, left.1, left.2), &format!("rc {}\n{}{}", right.0, right.1, right.2));
        }
    }
}

/// argparse behaviour: errors, prefixes, types, help.
fn argparse_cases(sides: &Sides, ledger: &mut Ledger) {
    let cases = [
        "query-tasks by_agent --agent-id viola/x", "acquire-lock m/q.ts lute --lock-type sharded",
        "query-tasks bogus_kind", "get-notifications --limit abc", "acquire-lock m/q2.ts lute --ttl ten",
        "broadcast viola --message -loud", "register -dash-id", "-j list-agents", "heartbeat lute --wobble",
        "list-agents -x", "register lute extra-pos", "acquire-lock m/z.c lute --re", "heartbeat lute --json=yes",
        "prune-notifications --max-a 5", "wait-for-locks lute --timeout 1", "satisfy-dependency one",
        "bogus-command", "status --storage-dir",
    ];
    for case in cases {
        let args = words(case);
        let left = sides.cli(Side::Python, &args, None, &sides.tmp);
        let (right, stripped) = strip_new_flags(&sides.cli(Side::Rust, &args, None, &sides.tmp));
        if stripped {
            ledger.note(&format!("version flag and assign-task --force (`{case}`)"), "usage lists the 1.1.0 flags");
        }
        if left != right {
            if sides.py_minor != 12 && left.2.contains("invalid choice") && right.2.contains("invalid choice") {
                ledger.note(&format!("help layout of Python 3.13: `{case}`"), "3.13 quotes choices");
                continue;
            }
            if layout_only(&left, &right, sides.py_minor) {
                ledger.note(&format!("help layout of Python 3.13: `{case}`"), "usage wraps differently outside 3.12");
                continue;
            }
            ledger.diff(&format!("argparse `{case}`"), &format!("rc {}\n{}{}", left.0, left.1, left.2), &format!("rc {}\n{}{}", right.0, right.1, right.2));
        }
    }
    let mut commands: Vec<Option<&str>> = vec![None];
    commands.extend(coordinationhub::cli_catalog::COMMANDS.iter().map(|cmd| Some(cmd.name)));
    for command in commands {
        let mut args: Vec<String> = Vec::new();
        if let Some(name) = command {
            args.push(name.to_string());
        }
        args.push("--help".into());
        let left = sides.cli(Side::Python, &args, None, &sides.tmp);
        let (right, stripped) = strip_new_flags(&sides.cli(Side::Rust, &args, None, &sides.tmp));
        let title = format!("help {}", command.unwrap_or("(top)"));
        if stripped {
            ledger.note(&format!("version flag and assign-task --force ({title})"), "help lists the 1.1.0 flags");
        }
        if left == right {
            continue;
        }
        if sides.py_minor != 12 {
            // Another argparse lays the text out differently; every option
            // Python shows must still be in the Rust help.
            let options = |text: &str| -> Vec<String> {
                text.split(|ch: char| ch.is_whitespace() || matches!(ch, '[' | ']' | ',' | '{' | '}' | '(' | ')'))
                    .filter(|word| word.starts_with('-') && word.len() > 1)
                    .map(str::to_string)
                    .collect()
            };
            let rust_options = options(&right.1);
            let flags_ok = options(&left.1).iter().all(|option| rust_options.contains(option));
            if flags_ok && left.0 == right.0 {
                ledger.note(&format!("help layout of Python 3.13: {title}"), "argparse wraps differently outside 3.12");
                continue;
            }
        }
        ledger.diff(&title, &left.1, &right.1);
    }
}

/// prune-notifications counts after a fixed sequence of event-writing calls.
fn prune_counts(sides: &Sides, ledger: &mut Ledger) {
    let pre = [
        "register viola", "register lute --parent-id viola", "acquire-lock m/a.c lute", "acquire-lock m/a.c viola",
        "notify-change m/b.c modified lute", "release-lock m/a.c lute", "create-task GL-1 viola tune",
        "update-task-status GL-1 completed", "broadcast viola --message gl", "acquire-lock m/c.c viola",
    ];
    for prune in ["--max-entries 2", "--max-entries -1", "--max-age-seconds 0", "--max-age-seconds 3600"] {
        let mut results = Vec::new();
        for side in [Side::Python, Side::Rust] {
            let label = format!("prune-{}-{}", prune.replace(' ', ""), if side == Side::Python { "py" } else { "rs" });
            let (store, project) = sides.fresh(&label);
            std::fs::create_dir_all(project.join("m")).unwrap();
            for line in pre {
                sides.cli(side, &words(line), Some((&store, &project)), &project);
            }
            std::thread::sleep(std::time::Duration::from_millis(1100));
            let mut args = words(&format!("prune-notifications {prune}"));
            args.push("--json".into());
            let first = sides.cli(side, &args, Some((&store, &project)), &project);
            let mut again = words("prune-notifications --max-entries 0");
            again.push("--json".into());
            let second = sides.cli(side, &again, Some((&store, &project)), &project);
            results.push((first.0, first.1, second.1));
        }
        if results[0] != results[1] {
            ledger.diff(&format!("prune {prune}"), &format!("{:?}", results[0]), &format!("{:?}", results[1]));
        }
    }
}

// ------------------------------------------------------------------
// Setup
// ------------------------------------------------------------------

fn legacy_ref(root: &Path) -> String {
    for reference in ["python-legacy", "origin/python-legacy"] {
        let ok = Command::new("git")
            .args(["rev-parse", "--verify", reference])
            .current_dir(root)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map(|status| status.success())
            .unwrap_or(false);
        if ok {
            return reference.to_string();
        }
    }
    eprintln!("python-legacy is not available");
    std::process::exit(1);
}

fn extract_legacy(root: &Path, dest: &Path) {
    let archive = dest.with_extension("tar");
    let reference = legacy_ref(root);
    let status = Command::new("git")
        .args(["archive", "--format=tar", "-o"])
        .arg(&archive)
        .arg(&reference)
        .current_dir(root)
        .status()
        .expect("git archive");
    assert!(status.success(), "git archive failed");
    std::fs::create_dir_all(dest).unwrap();
    let status = Command::new("tar")
        .arg("-xf")
        .arg(&archive)
        .arg("-C")
        .arg(dest)
        .status()
        .expect("tar");
    assert!(status.success(), "tar failed");
}

fn python_minor(python: &str, legacy: &Path) -> u32 {
    let out = Command::new(python)
        .args(["-c", "import sys, coordinationhub; assert coordinationhub.__version__ == '0.8.0'; print(sys.version_info[1])"])
        .env("PYTHONPATH", legacy)
        .output()
        .unwrap_or_else(|err| {
            eprintln!("cannot run {python}: {err}");
            std::process::exit(1)
        });
    if !out.status.success() {
        eprintln!("{python} cannot import coordinationhub 0.8.0: {}", String::from_utf8_lossy(&out.stderr));
        std::process::exit(1);
    }
    String::from_utf8_lossy(&out.stdout).trim().parse().unwrap_or(0)
}

fn main() {
    let mut args = std::env::args().skip(1);
    let mut binary: Option<PathBuf> = None;
    let mut python = std::env::var("PARITY_PYTHON").unwrap_or_else(|_| {
        if cfg!(windows) { "python".into() } else { "python3".into() }
    });
    while let Some(arg) = args.next() {
        if arg == "--python" {
            python = args.next().expect("--python needs a value");
        } else {
            binary = Some(PathBuf::from(arg));
        }
    }
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let binary = binary.unwrap_or_else(|| {
        root.join("target").join("release").join(format!("coordinationhub{}", std::env::consts::EXE_SUFFIX))
    });
    if !binary.is_file() {
        eprintln!("missing rust binary {}", binary.display());
        std::process::exit(1);
    }
    let tmp = std::env::temp_dir().join(format!(
        "chub-parity-{}-{}",
        std::process::id(),
        std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()
    ));
    std::fs::create_dir_all(&tmp).unwrap();
    let legacy = tmp.join("python");
    extract_legacy(&root, &legacy);
    let py_minor = python_minor(&python, &legacy);
    let sides = Sides {
        python,
        legacy,
        rust: std::fs::canonicalize(&binary).unwrap_or(binary),
        tmp: tmp.clone(),
        py_minor,
    };
    let mut ledger = Ledger::default();
    harness_checks(&sides, &mut ledger);
    mcp_scenarios(&sides, &mut ledger);
    cli_sequence(&sides, &mut ledger);
    argparse_cases(&sides, &mut ledger);
    prune_counts(&sides, &mut ledger);
    let unexpected = ledger.unexpected();
    println!("ledger {} difference(s), {} unexpected", ledger.items.len(), unexpected.len());
    for item in &ledger.items {
        println!("  - {item}");
    }
    let failed = !unexpected.is_empty();
    let _ = std::fs::remove_dir_all(&tmp);
    std::process::exit(if failed { 1 } else { 0 });
}
