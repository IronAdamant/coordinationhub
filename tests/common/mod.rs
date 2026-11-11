//! Shared helpers for tests that drive the built binary.
#![allow(dead_code)]

use coordinationhub::json::{self, Value};
use std::io::Write;
use std::path::PathBuf;
use std::process::{Command, Stdio};

pub struct Output {
    pub code: i32,
    pub stdout: String,
    pub stderr: String,
}

/// A fresh storage directory and project root for one test.
pub struct Hub {
    pub store: PathBuf,
    pub project: PathBuf,
}

impl Hub {
    pub fn new(name: &str) -> Hub {
        let base = std::env::temp_dir().join(format!(
            "chub-t-{name}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let store = base.join("store");
        let project = base.join("project");
        std::fs::create_dir_all(&store).unwrap();
        std::fs::create_dir_all(&project).unwrap();
        Hub { store, project }
    }

    fn command(&self, args: &[&str], columns: Option<&str>) -> Command {
        let mut cmd = Command::new(env!("CARGO_BIN_EXE_coordinationhub"));
        cmd.args(args)
            .current_dir(&self.project)
            .env_remove("COLUMNS")
            .env_remove("LINES")
            .env("COORDINATIONHUB_HOUSEKEEPING", "0");
        if let Some(columns) = columns {
            cmd.env("COLUMNS", columns);
        }
        cmd
    }

    /// Run with this hub's --storage-dir and --project-root appended.
    pub fn run(&self, args: &[&str]) -> Output {
        let mut full: Vec<String> = args.iter().map(|s| s.to_string()).collect();
        full.push("--storage-dir".into());
        full.push(self.store.display().to_string());
        full.push("--project-root".into());
        full.push(self.project.display().to_string());
        let refs: Vec<&str> = full.iter().map(String::as_str).collect();
        collect(self.command(&refs, None))
    }

    /// Run exactly these arguments (no storage flags), cwd in the project.
    pub fn run_bare(&self, args: &[&str], columns: Option<&str>) -> Output {
        collect(self.command(args, columns))
    }

    /// Run with --json and parse stdout.
    pub fn json(&self, args: &[&str]) -> Value {
        let mut full = args.to_vec();
        full.push("--json");
        let out = self.run(&full);
        json::parse(&out.stdout).unwrap_or_else(|err| panic!("{err}: {}{}", out.stdout, out.stderr))
    }

    /// Send tools/call requests to `serve-mcp` one at a time, each after the
    /// previous reply (as an agent does), and return each result object.
    /// The server answers waiting calls on their own threads, so a pipelined
    /// batch could see a later call land during an earlier wait.
    pub fn mcp(&self, calls: &[(&str, Value)]) -> Vec<Value> {
        self.mcp_env(calls, &[])
    }

    /// `mcp` with extra environment variables for the server.
    pub fn mcp_env(&self, calls: &[(&str, Value)], env: &[(&str, &str)]) -> Vec<Value> {
        let mut server = Server::start(self, env);
        let results = calls
            .iter()
            .map(|(tool, args)| server.call(tool, args.clone()))
            .collect();
        server.stop();
        results
    }
}

/// A running `serve-mcp` that requests can be sent to without waiting.
pub struct Server {
    child: std::process::Child,
    lines: std::sync::mpsc::Receiver<String>,
    next: i64,
    early: Vec<Value>,
}

impl Server {
    pub fn start(hub: &Hub, env: &[(&str, &str)]) -> Server {
        let mut command = Command::new(env!("CARGO_BIN_EXE_coordinationhub"));
        command
            .args(["serve-mcp", "--storage-dir"])
            .arg(&hub.store)
            .arg("--project-root")
            .arg(&hub.project)
            .env("COORDINATIONHUB_HOUSEKEEPING", "0")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        for (key, value) in env {
            command.env(key, value);
        }
        let mut child = command.spawn().unwrap();
        let stdout = child.stdout.take().unwrap();
        let (sender, lines) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            use std::io::BufRead;
            for line in std::io::BufReader::new(stdout).lines() {
                let Ok(line) = line else { break };
                if sender.send(line).is_err() {
                    break;
                }
            }
        });
        let mut server = Server { child, lines, next: 1, early: Vec::new() };
        server.write(r#"{"jsonrpc":"2.0","id":0,"method":"initialize","params":{}}"#);
        server.wait_for(0, std::time::Duration::from_secs(30));
        server
    }

    fn write(&mut self, line: &str) {
        let stdin = self.child.stdin.as_mut().unwrap();
        writeln!(stdin, "{line}").unwrap();
        stdin.flush().unwrap();
    }

    /// Sends a tools/call and returns its id without waiting for the reply.
    pub fn send(&mut self, tool: &str, args: Value) -> i64 {
        let id = self.next;
        self.next += 1;
        let request = json::obj(vec![
            ("jsonrpc", json::s("2.0")),
            ("id", json::i(id)),
            ("method", json::s("tools/call")),
            ("params", json::obj(vec![("name", json::s(tool)), ("arguments", args)])),
        ]);
        self.write(&json::dumps(&request));
        id
    }

    /// Sends a raw JSON-RPC line (a notification, say).
    pub fn notify(&mut self, line: &str) {
        self.write(line);
    }

    /// The result object of response `id`, or None after `limit`.
    pub fn wait_for(&mut self, id: i64, limit: std::time::Duration) -> Option<Value> {
        if let Some(pos) = self.early.iter().position(|msg| msg.get("id") == Some(&json::i(id))) {
            return self.early.remove(pos).get("result").cloned();
        }
        let deadline = std::time::Instant::now() + limit;
        loop {
            let left = deadline.checked_duration_since(std::time::Instant::now())?;
            let line = self.lines.recv_timeout(left).ok()?;
            let msg = json::parse(&line).unwrap();
            if msg.get("id") == Some(&json::i(id)) {
                return msg.get("result").cloned();
            }
            self.early.push(msg);
        }
    }

    /// Sends a call and waits up to 60 s for its result.
    pub fn call(&mut self, tool: &str, args: Value) -> Value {
        let id = self.send(tool, args);
        self.wait_for(id, std::time::Duration::from_secs(60))
            .unwrap_or_else(|| panic!("no reply to {tool} within 60 s"))
    }

    /// Closes stdin and waits for the server to exit.
    pub fn stop(mut self) {
        drop(self.child.stdin.take());
        let _ = self.child.wait();
    }
}

fn collect(mut cmd: Command) -> Output {
    let out = cmd.output().unwrap();
    Output {
        code: out.status.code().unwrap_or(-1),
        stdout: String::from_utf8_lossy(&out.stdout).into_owned(),
        stderr: String::from_utf8_lossy(&out.stderr).into_owned(),
    }
}

/// Whitespace split, enough for the fixed argument strings in these tests.
pub fn split(text: &str) -> Vec<&str> {
    text.split_whitespace().collect()
}

/// The text of an MCP tool result and its isError flag.
pub fn tool_text(result: &Value) -> (String, bool) {
    let text = result
        .get("content")
        .and_then(Value::as_array)
        .and_then(|items| items.first())
        .and_then(|item| item.get("text"))
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let is_error = result.get("isError").and_then(Value::as_bool).unwrap_or(false);
    (text, is_error)
}
