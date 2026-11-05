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

    /// Send tools/call requests to `serve-mcp` and return each result object.
    pub fn mcp(&self, calls: &[(&str, Value)]) -> Vec<Value> {
        let mut child = Command::new(env!("CARGO_BIN_EXE_coordinationhub"))
            .args(["serve-mcp", "--storage-dir"])
            .arg(&self.store)
            .arg("--project-root")
            .arg(&self.project)
            .env("COORDINATIONHUB_HOUSEKEEPING", "0")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .unwrap();
        {
            let stdin = child.stdin.as_mut().unwrap();
            writeln!(stdin, r#"{{"jsonrpc":"2.0","id":0,"method":"initialize","params":{{}}}}"#).unwrap();
            for (index, (tool, args)) in calls.iter().enumerate() {
                let request = json::obj(vec![
                    ("jsonrpc", json::s("2.0")),
                    ("id", json::i(index as i64 + 1)),
                    ("method", json::s("tools/call")),
                    (
                        "params",
                        json::obj(vec![("name", json::s(*tool)), ("arguments", args.clone())]),
                    ),
                ]);
                writeln!(stdin, "{}", json::dumps(&request)).unwrap();
            }
        }
        drop(child.stdin.take());
        let out = child.wait_with_output().unwrap();
        String::from_utf8_lossy(&out.stdout)
            .lines()
            .skip(1)
            .map(|line| json::parse(line).unwrap().get("result").cloned().unwrap())
            .collect()
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
