//! IDE hook adapter. One JSON event on stdin, one decision object on stdout.
//! Fails open: a bad event prints nothing and exits 0.

use crate::hash::sha256_hex;
use crate::hub::Hub;
use crate::json::{self, Json};
use crate::util::obj;
use std::io::{self, Read};
use std::path::PathBuf;

pub fn run() -> io::Result<()> {
    let mut raw = String::new();
    io::stdin().read_to_string(&mut raw)?;
    if raw.trim().is_empty() {
        return Ok(());
    }
    let Ok(event) = json::parse(&raw) else {
        return Ok(());
    };
    let cwd = event
        .get("cwd")
        .and_then(|v| v.as_str())
        .map(PathBuf::from)
        .unwrap_or_else(|| std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")));
    let storage = std::env::var("COORDINATIONHUB_STORAGE_DIR").ok().map(PathBuf::from);
    let Ok(hub) = Hub::open(storage.as_deref(), Some(&cwd), "hub") else {
        return Ok(());
    };
    let name = event.get("hook_event_name").and_then(|v| v.as_str()).unwrap_or("");
    let tool = event.get("tool_name").and_then(|v| v.as_str()).unwrap_or("");
    let session = event.get("session_id").and_then(|v| v.as_str()).unwrap_or("");
    let agent = session_agent("cc", session);
    let result = match (name, tool) {
        ("SessionStart", _) => {
            let _ = hub.call(
                "register_agent",
                &obj(vec![("agent_id", Json::String(agent))]),
            );
            None
        }
        ("UserPromptSubmit", _) => {
            let prompt = event.get("prompt").and_then(|v| v.as_str()).unwrap_or("");
            let summary = redact(prompt);
            let _ = hub.call(
                "update_agent_status",
                &obj(vec![
                    ("agent_id", Json::String(agent)),
                    ("current_task", Json::String(summary)),
                ]),
            );
            None
        }
        ("PreToolUse", "Write" | "Edit") => pre_write(&hub, &agent, &event),
        ("PostToolUse", "Write" | "Edit") => {
            if let Some(path) = file_path(&event) {
                let _ = hub.call(
                    "notify_change",
                    &obj(vec![
                        ("document_path", Json::String(path.clone())),
                        ("change_type", Json::String("modified".into())),
                        ("agent_id", Json::String(agent.clone())),
                    ]),
                );
                let _ = hub.call(
                    "release_lock",
                    &obj(vec![
                        ("document_path", Json::String(path)),
                        ("agent_id", Json::String(agent)),
                    ]),
                );
            }
            None
        }
        ("SubagentStart", _) => {
            let child = event
                .get("agent_id")
                .and_then(|v| v.as_str())
                .unwrap_or("child");
            let child_id = format!("{agent}.{child}");
            let _ = hub.call(
                "register_agent",
                &obj(vec![
                    ("agent_id", Json::String(child_id)),
                    ("parent_id", Json::String(agent)),
                ]),
            );
            None
        }
        ("SubagentStop", _) => {
            let child = event
                .get("agent_id")
                .and_then(|v| v.as_str())
                .unwrap_or("child");
            let _ = hub.call(
                "deregister_agent",
                &obj(vec![("agent_id", Json::String(format!("{agent}.{child}")))]),
            );
            None
        }
        ("SessionEnd", _) => {
            let status = hub.call("status", &Json::object(vec![])).ok();
            let _ = hub.call(
                "deregister_agent",
                &obj(vec![("agent_id", Json::String(agent))]),
            );
            let summary = status
                .as_ref()
                .map(|s| {
                    format!(
                        "{} agents tracked, {} locks held, {} conflicts, {} notifications",
                        s.get("registered_agents").and_then(|v| v.as_i64()).unwrap_or(0),
                        s.get("active_locks").and_then(|v| v.as_i64()).unwrap_or(0),
                        s.get("recent_conflicts").and_then(|v| v.as_i64()).unwrap_or(0),
                        s.get("pending_notifications").and_then(|v| v.as_i64()).unwrap_or(0),
                    )
                })
                .unwrap_or_default();
            Some(obj(vec![(
                "hookSpecificOutput",
                obj(vec![
                    ("hookEventName", Json::String("SessionEnd".into())),
                    (
                        "additionalContext",
                        Json::String(format!("[CoordinationHub] Session summary: {summary}")),
                    ),
                ]),
            )]))
        }
        _ => None,
    };
    if let Some(result) = result {
        print!("{}", result.dump());
    }
    Ok(())
}

fn pre_write(hub: &Hub, agent: &str, event: &Json) -> Option<Json> {
    let path = file_path(event)?;
    let _ = hub.call(
        "admin_locks",
        &obj(vec![
            ("action", Json::String("reap_expired".into())),
            ("grace_seconds", Json::Float(120.0)),
        ]),
    );
    let result = hub
        .call(
            "acquire_lock",
            &obj(vec![
                ("document_path", Json::String(path.clone())),
                ("agent_id", Json::String(agent.into())),
                ("ttl", Json::Float(300.0)),
            ]),
        )
        .ok()?;
    if result.get("acquired").and_then(|v| v.as_bool()) == Some(true) {
        return Some(obj(vec![(
            "hookSpecificOutput",
            obj(vec![
                ("hookEventName", Json::String("PreToolUse".into())),
                ("permissionDecision", Json::String("allow".into())),
                (
                    "additionalContext",
                    Json::String(format!("[CoordinationHub] Lock acquired: {path}")),
                ),
            ]),
        )]));
    }
    let holder = result
        .get("locked_by")
        .and_then(|v| v.as_str())
        .unwrap_or("unknown");
    if holder == agent {
        return None;
    }
    Some(obj(vec![(
        "hookSpecificOutput",
        obj(vec![
            ("hookEventName", Json::String("PreToolUse".into())),
            ("permissionDecision", Json::String("deny".into())),
            (
                "permissionDecisionReason",
                Json::String(format!(
                    "[CoordinationHub] File locked by {holder}. Use 'coordinationhub release-lock {path} {holder}' to force."
                )),
            ),
        ]),
    )]))
}

fn file_path(event: &Json) -> Option<String> {
    let input = event.get("tool_input")?;
    input
        .get("file_path")
        .or_else(|| input.get("path"))
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
}

pub fn session_agent(prefix: &str, session: &str) -> String {
    format!("hub.{prefix}.{}", sanitize_session(session))
}

fn sanitize_session(session: &str) -> String {
    if session.is_empty() {
        return "unknown".into();
    }
    if session
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
    {
        return session.chars().take(12).collect();
    }
    sha256_hex(session.as_bytes())[..12].to_string()
}

fn redact(prompt: &str) -> String {
    let mut out = String::new();
    let mut rest = prompt;
    while let Some(idx) = rest.find("sk-") {
        out.push_str(&rest[..idx]);
        out.push_str("[redacted]");
        let after = &rest[idx..];
        let skip = after.find(char::is_whitespace).unwrap_or(after.len());
        rest = &after[skip..];
    }
    out.push_str(rest);
    let flat = out.split_whitespace().collect::<Vec<_>>().join(" ");
    if flat.chars().count() <= 120 {
        flat
    } else {
        let head: String = flat.chars().take(117).collect();
        format!("{head}...")
    }
}
