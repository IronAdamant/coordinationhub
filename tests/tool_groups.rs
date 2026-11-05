//! COORDINATIONHUB_TOOLS / COORDINATIONHUB_DISABLE on the MCP stdio surface.

use coordinationhub::groups::GROUPS;
use coordinationhub::json::{self, Value};
use std::io::Write;
use std::process::{Command, Output, Stdio};

/// Run `serve-mcp` with the given switches; send initialize, tools/list and
/// the given tool calls; return every reply line.
fn session(env: &[(&str, &str)], calls: &[(&str, Value)]) -> (Output, Vec<Value>) {
    let base = std::env::temp_dir().join(format!(
        "chub-groups-{}-{}",
        std::process::id(),
        std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()
    ));
    std::fs::create_dir_all(base.join("project")).unwrap();
    let mut command = Command::new(env!("CARGO_BIN_EXE_coordinationhub"));
    command
        .arg("serve-mcp")
        .arg("--storage-dir")
        .arg(base.join("store"))
        .arg("--project-root")
        .arg(base.join("project"))
        .env_remove("COORDINATIONHUB_TOOLS")
        .env_remove("COORDINATIONHUB_DISABLE")
        .env("COORDINATIONHUB_HOUSEKEEPING", "0")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    for (key, value) in env {
        command.env(key, value);
    }
    let mut child = command.spawn().unwrap();
    {
        let mut stdin = child.stdin.take().unwrap();
        let mut lines = vec![
            r#"{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}"#.to_string(),
            r#"{"jsonrpc":"2.0","id":2,"method":"tools/list"}"#.to_string(),
        ];
        for (index, (tool, args)) in calls.iter().enumerate() {
            lines.push(json::dumps(&json::obj(vec![
                ("jsonrpc", json::s("2.0")),
                ("id", json::i(index as i64 + 3)),
                ("method", json::s("tools/call")),
                ("params", json::obj(vec![("name", json::s(*tool)), ("arguments", args.clone())])),
            ])));
        }
        // A server that exits at startup closes stdin; ignore that error.
        let _ = stdin.write_all((lines.join("\n") + "\n").as_bytes());
    }
    let out = child.wait_with_output().unwrap();
    let replies = String::from_utf8_lossy(&out.stdout)
        .lines()
        .map(|line| json::parse(line).unwrap())
        .collect();
    (out, replies)
}

fn names(replies: &[Value]) -> Vec<String> {
    replies[1]
        .get("result")
        .and_then(|r| r.get("tools"))
        .and_then(Value::as_array)
        .unwrap()
        .iter()
        .map(|tool| tool.get("name").and_then(Value::as_str).unwrap().to_string())
        .collect()
}

fn group(name: &str) -> Vec<String> {
    GROUPS
        .iter()
        .find(|(g, _)| *g == name)
        .unwrap()
        .1
        .iter()
        .map(|tool| tool.to_string())
        .collect()
}

fn sorted(mut items: Vec<String>) -> Vec<String> {
    items.sort();
    items
}

fn tool_text(reply: &Value) -> (String, bool) {
    let result = reply.get("result").unwrap();
    let text = result
        .get("content")
        .and_then(Value::as_array)
        .and_then(|items| items.first())
        .and_then(|item| item.get("text"))
        .and_then(Value::as_str)
        .unwrap()
        .to_string();
    (text, result.get("isError").and_then(Value::as_bool).unwrap_or(false))
}

#[test]
fn groups_cover_every_tool_once() {
    let (_, replies) = session(&[], &[]);
    let listed = sorted(names(&replies));
    let grouped: Vec<String> = GROUPS.iter().flat_map(|(_, tools)| tools.iter().map(|t| t.to_string())).collect();
    assert_eq!(grouped.len(), 51);
    assert_eq!(sorted(grouped), listed);
}

#[test]
fn default_listing_is_unchanged() {
    let (_, plain) = session(&[], &[]);
    let (_, blank) = session(&[("COORDINATIONHUB_TOOLS", "  "), ("COORDINATIONHUB_DISABLE", "")], &[]);
    assert_eq!(names(&plain).len(), 51);
    assert_eq!(names(&plain).last().map(String::as_str), Some("who"));
    assert_eq!(plain[0].get("result").unwrap().get("instructions"), None);
    // Blank switches count as unset: same bytes.
    assert_eq!(json::dumps(&plain[0]), json::dumps(&blank[0]));
    assert_eq!(json::dumps(&plain[1]), json::dumps(&blank[1]));
}

#[test]
fn tools_allowlist_by_group_and_by_name() {
    let (_, replies) = session(&[("COORDINATIONHUB_TOOLS", " Locks , TASKS ")], &[]);
    let mut want = group("core");
    want.extend(group("locks"));
    want.extend(group("tasks"));
    assert_eq!(sorted(names(&replies)), sorted(want));
    let (_, replies) = session(&[("COORDINATIONHUB_TOOLS", "acquire_lock,send_message")], &[]);
    let mut want = group("core");
    want.push("acquire_lock".into());
    want.push("send_message".into());
    assert_eq!(sorted(names(&replies)), sorted(want));
    let (_, replies) = session(&[("COORDINATIONHUB_TOOLS", "all")], &[]);
    assert_eq!(names(&replies).len(), 51);
}

#[test]
fn disable_by_group_and_by_name_after_the_allowlist() {
    let (_, replies) = session(&[("COORDINATIONHUB_DISABLE", "spawn,diagnostics")], &[]);
    let listed = names(&replies);
    assert_eq!(listed.len(), 51 - group("spawn").len() - group("diagnostics").len());
    assert!(!listed.iter().any(|name| group("spawn").contains(name)));
    let (_, replies) = session(&[("COORDINATIONHUB_DISABLE", "who")], &[]);
    assert_eq!(names(&replies).len(), 50);
    assert!(!names(&replies).contains(&"who".to_string()));
    let (_, replies) = session(
        &[("COORDINATIONHUB_TOOLS", "locks,tasks"), ("COORDINATIONHUB_DISABLE", "who,tasks")],
        &[],
    );
    let mut want = group("core");
    want.extend(group("locks").into_iter().filter(|tool| tool != "who"));
    assert_eq!(sorted(names(&replies)), sorted(want));
}

#[test]
fn a_disabled_tool_says_how_to_enable_it() {
    let (_, replies) = session(
        &[("COORDINATIONHUB_DISABLE", "locks")],
        &[("acquire_lock", json::obj(vec![("document_path", json::s("a.md")), ("agent_id", json::s("x"))]))],
    );
    let (text, is_error) = tool_text(&replies[2]);
    assert!(is_error);
    assert_eq!(
        text,
        r#"tool "acquire_lock" is disabled (group "locks") by COORDINATIONHUB_DISABLE; remove "locks" from it to enable"#
    );
    let (_, replies) = session(
        &[("COORDINATIONHUB_TOOLS", "tasks")],
        &[("send_message", json::obj(vec![]))],
    );
    let (text, is_error) = tool_text(&replies[2]);
    assert!(is_error);
    assert_eq!(
        text,
        r#"tool "send_message" is disabled (group "messages") by COORDINATIONHUB_TOOLS; add "messages" to it to enable"#
    );
}

#[test]
fn active_groups_are_reported_once_switched() {
    let (_, replies) = session(&[("COORDINATIONHUB_TOOLS", "locks")], &[("status", json::obj(vec![]))]);
    let instructions = replies[0].get("result").unwrap().get("instructions").and_then(Value::as_str);
    assert_eq!(instructions, Some("CoordinationHub tool groups on: core, locks."));
    let (text, _) = tool_text(&replies[2]);
    let status = json::parse(&text).unwrap();
    assert_eq!(status.get("tool_groups").and_then(Value::as_str), Some("core, locks"));
    assert_eq!(status.get("tools").and_then(Value::as_i64), Some((group("core").len() + group("locks").len()) as i64));
}

#[test]
fn an_unknown_name_exits_2_with_one_line() {
    let (out, replies) = session(&[("COORDINATIONHUB_DISABLE", "lokcs")], &[]);
    assert_eq!(out.status.code(), Some(2));
    assert!(replies.is_empty());
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert_eq!(stderr.lines().count(), 1);
    assert!(stderr.starts_with(
        r#"COORDINATIONHUB_DISABLE: unknown group "lokcs"; groups: core, locks, tasks, messages, spawn, agents, diagnostics; tools: register_agent, "#
    ));
    let (out, _) = session(&[("COORDINATIONHUB_TOOLS", "locks,teh_tasks")], &[]);
    assert_eq!(out.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&out.stderr).starts_with(r#"COORDINATIONHUB_TOOLS: unknown group "teh_tasks";"#));
    let (out, _) = session(&[("COORDINATIONHUB_DISABLE", "all")], &[]);
    assert_eq!(out.status.code(), Some(2));
}

#[test]
fn core_cannot_be_disabled() {
    let (_, replies) = session(
        &[("COORDINATIONHUB_DISABLE", "core,status"), ("COORDINATIONHUB_TOOLS", "messages")],
        &[("status", json::obj(vec![]))],
    );
    let listed = names(&replies);
    for tool in group("core") {
        assert!(listed.contains(&tool), "{tool} missing");
    }
    assert!(!tool_text(&replies[2]).1);
}

#[test]
fn the_cli_is_unaffected() {
    let base = std::env::temp_dir().join(format!("chub-groups-cli-{}", std::process::id()));
    let out = Command::new(env!("CARGO_BIN_EXE_coordinationhub"))
        .args(["acquire-lock", "a.md", "x", "--json", "--storage-dir"])
        .arg(base.join("store"))
        .arg("--project-root")
        .arg(&base)
        .env("COORDINATIONHUB_DISABLE", "locks")
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(0));
    assert!(String::from_utf8_lossy(&out.stdout).contains("\"acquired\": true"));
}
