//! IDE hook events. One process per event, sharing the hub journal.

use crate::act_identity;
use crate::act_view;
use crate::db::Spawn;
use crate::engine::Engine;
use crate::json::{self, Value};
use crate::util;

const PREFIX: &str = "cc";

pub fn handle(engine: &mut Engine, event: &Value) -> Option<Value> {
    let name = event.get("hook_event_name").and_then(Value::as_str).unwrap_or("");
    let tool = event.get("tool_name").and_then(Value::as_str).unwrap_or("");
    let session = event.get("session_id").and_then(Value::as_str).unwrap_or("");
    match name {
        "SessionStart" => {
            ensure(engine, &session_agent(session), None, None);
            None
        }
        "UserPromptSubmit" => {
            let prompt = event.get("prompt").and_then(Value::as_str).unwrap_or("");
            on_prompt(engine, session, prompt);
            None
        }
        "PreToolUse" if tool == "Write" || tool == "Edit" => on_pre_write(engine, event, session),
        "PreToolUse" if tool == "Agent" => {
            stash(engine, event, session);
            None
        }
        "PostToolUse" if tool == "Write" || tool == "Edit" => {
            on_post_write(engine, event, session);
            None
        }
        "PostToolUse" if tool == "mcp__stele-context__index" => {
            on_index(engine, event, session);
            None
        }
        "PostToolUse" if tool == "mcp__trammel__claim_step" => {
            on_claim(engine, event, session);
            None
        }
        "SubagentStart" => {
            on_subagent_start(engine, event, session);
            None
        }
        "SubagentStop" => {
            on_subagent_stop(engine, event, session);
            None
        }
        "SessionEnd" => on_session_end(engine, session),
        _ => None,
    }
}

fn session_agent(session_id: &str) -> String {
    format!("hub.{PREFIX}.{}", sanitize(session_id))
}

fn sanitize(session_id: &str) -> String {
    if session_id.is_empty() {
        return "unknown".into();
    }
    if session_id
        .chars()
        .all(|ch| ch.is_ascii_alphanumeric() || ch == '_' || ch == '-')
    {
        return session_id.chars().take(12).collect();
    }
    sha256_hex(session_id).chars().take(12).collect()
}

fn raw_id(event: &Value) -> Option<&str> {
    event
        .get("agent_id")
        .and_then(Value::as_str)
        .or_else(|| event.get("subagent_id").and_then(Value::as_str))
}

fn subagent_type(event: &Value) -> String {
    event
        .get("agent_type")
        .and_then(Value::as_str)
        .or_else(|| {
            event
                .get("tool_input")
                .and_then(|v| v.get("subagent_type"))
                .and_then(Value::as_str)
        })
        .unwrap_or("agent")
        .to_string()
}

fn ensure(engine: &mut Engine, agent_id: &str, parent: Option<&str>, raw: Option<&str>) {
    let _ = engine.store.begin();
    let active = engine
        .store
        .state
        .agent(agent_id)
        .map(|agent| agent.status == "active")
        .unwrap_or(false);
    if active {
        let _ = act_identity::heartbeat(&mut engine.store.state, agent_id);
        let _ = engine.store.commit();
        engine.store.end();
        return;
    }
    let root = engine.project_root.clone();
    let worktree = root
        .as_ref()
        .map(|path| util::path_str(path))
        .unwrap_or_default();
    let url = std::env::var("COORDINATIONHUB_COORDINATION_URL")
        .unwrap_or_else(|_| "http://localhost:9877".into());
    let _ = act_identity::register(
        &mut engine.store.state,
        agent_id,
        parent,
        &worktree,
        raw,
        Some(PREFIX),
        engine.graph.is_some(),
        &url,
    );
    let _ = engine.store.commit();
    engine.store.end();
}

fn resolve(engine: &mut Engine, session: &str, raw: Option<&str>) -> String {
    if let Some(raw) = raw {
        let _ = engine.store.begin();
        let mapped = engine
            .store
            .state
            .agents
            .iter()
            .find(|agent| {
                agent.raw_ide_id.as_deref() == Some(raw)
                    && agent.ide_vendor.as_deref() == Some(PREFIX)
            })
            .map(|agent| agent.agent_id.clone());
        engine.store.end();
        if let Some(mapped) = mapped {
            return mapped;
        }
        return raw.to_string();
    }
    session_agent(session)
}

fn on_prompt(engine: &mut Engine, session: &str, prompt: &str) {
    let prompt = redact(prompt.trim());
    if prompt.is_empty() {
        return;
    }
    let summary = if prompt.chars().count() <= 120 {
        prompt
    } else {
        let cut: String = prompt.chars().take(117).collect();
        format!("{cut}...")
    };
    let summary = summary.split_whitespace().collect::<Vec<_>>().join(" ");
    let agent = session_agent(session);
    ensure(engine, &agent, None, None);
    let _ = engine.call(
        "update_agent_status",
        &json::obj(vec![
            ("agent_id", json::s(&agent)),
            ("current_task", json::s(summary)),
        ]),
    );
}

fn on_pre_write(engine: &mut Engine, event: &Value, session: &str) -> Option<Value> {
    let Some(path) = event
        .get("tool_input")
        .and_then(|v| v.get("file_path"))
        .and_then(Value::as_str)
    else {
        return None;
    };
    let agent = resolve(engine, session, raw_id(event));
    ensure(engine, &agent, None, raw_id(event));
    let _ = engine.call(
        "admin_locks",
        &json::obj(vec![
            ("action", json::s("reap_expired")),
            ("grace_seconds", json::f(120.0)),
        ]),
    );
    let result = engine
        .call(
            "acquire_lock",
            &json::obj(vec![
                ("document_path", json::s(path)),
                ("agent_id", json::s(&agent)),
                ("ttl", json::f(300.0)),
            ]),
        )
        .unwrap_or(json::obj(vec![("acquired", json::b(false))]));
    if result.get("acquired").and_then(Value::as_bool) == Some(true) {
        return Some(json::obj(vec![(
            "hookSpecificOutput",
            json::obj(vec![
                ("hookEventName", json::s("PreToolUse")),
                ("permissionDecision", json::s("allow")),
                (
                    "additionalContext",
                    json::s(format!("[CoordinationHub] Lock acquired: {path}")),
                ),
            ]),
        )]));
    }
    let holder = result
        .get("locked_by")
        .and_then(Value::as_str)
        .unwrap_or("unknown");
    if holder == agent {
        return None;
    }
    Some(json::obj(vec![(
        "hookSpecificOutput",
        json::obj(vec![
            ("hookEventName", json::s("PreToolUse")),
            ("permissionDecision", json::s("deny")),
            (
                "permissionDecisionReason",
                json::s(format!(
                    "[CoordinationHub] File locked by {holder}. Use 'coordinationhub release-lock {path} {holder}' to force."
                )),
            ),
        ]),
    )]))
}

fn on_post_write(engine: &mut Engine, event: &Value, session: &str) {
    let Some(path) = event
        .get("tool_input")
        .and_then(|v| v.get("file_path"))
        .and_then(Value::as_str)
    else {
        return;
    };
    let agent = resolve(engine, session, raw_id(event));
    let _ = engine.call(
        "notify_change",
        &json::obj(vec![
            ("document_path", json::s(path)),
            ("change_type", json::s("modified")),
            ("agent_id", json::s(&agent)),
        ]),
    );
    let _ = engine.store.begin();
    let root = engine.project_root.clone();
    act_view::claim_ownership(&mut engine.store.state, path, &agent, root.as_deref());
    let _ = engine.store.commit();
    engine.store.end();
    let _ = engine.call(
        "release_lock",
        &json::obj(vec![
            ("document_path", json::s(path)),
            ("agent_id", json::s(agent)),
        ]),
    );
}

fn stash(engine: &mut Engine, event: &Value, session: &str) {
    let tool_use = event.get("tool_use_id").and_then(Value::as_str).unwrap_or("");
    let input = event.get("tool_input");
    let kind = input
        .and_then(|v| v.get("subagent_type"))
        .and_then(Value::as_str)
        .unwrap_or("");
    if tool_use.is_empty() || kind.is_empty() {
        return;
    }
    let description = input
        .and_then(|v| v.get("description"))
        .and_then(Value::as_str)
        .unwrap_or("");
    let prompt = input
        .and_then(|v| v.get("prompt"))
        .and_then(Value::as_str)
        .unwrap_or("");
    let _ = engine.store.begin();
    engine.store.state.spawns.push(Spawn {
        id: format!("hook:{tool_use}"),
        parent_agent_id: session.to_string(),
        subagent_type: Some(kind.into()),
        description: Some(description.into()),
        prompt: Some(prompt.into()),
        created_at: util::now(),
        consumed_at: None,
        status: "pending".into(),
        source: "hook_stash".into(),
        child_agent_id: None,
        stop_requested: false,
    });
    let _ = engine.store.commit();
    engine.store.end();
}

fn consume_stash(engine: &mut Engine, session: &str, kind: &str) -> Option<String> {
    let _ = engine.store.begin();
    let found = engine
        .store
        .state
        .spawns
        .iter_mut()
        .find(|row| {
            row.source == "hook_stash"
                && row.status == "pending"
                && row.parent_agent_id == session
                && row.subagent_type.as_deref() == Some(kind)
        })
        .map(|row| {
            row.status = "consumed".into();
            row.consumed_at = Some(util::now());
            row.description.clone().unwrap_or_default()
        });
    let _ = engine.store.commit();
    engine.store.end();
    found
}

fn on_subagent_start(engine: &mut Engine, event: &Value, session: &str) {
    let parent = session_agent(session);
    ensure(engine, &parent, None, None);
    let kind = subagent_type(event);
    let pending = consume_stash(engine, session, &kind);
    let description = pending.or_else(|| {
        event
            .get("tool_input")
            .and_then(|v| v.get("description"))
            .and_then(Value::as_str)
            .map(|s| s.to_string())
    });
    let raw = raw_id(event).map(|s| s.to_string());
    if let Some(raw) = &raw {
        let _ = engine.store.begin();
        let existing = engine
            .store
            .state
            .agents
            .iter()
            .find(|agent| {
                agent.raw_ide_id.as_deref() == Some(raw.as_str())
                    && agent.ide_vendor.as_deref() == Some(PREFIX)
            })
            .map(|agent| agent.agent_id.clone());
        engine.store.end();
        if let Some(existing) = existing {
            let _ = engine.call(
                "heartbeat",
                &json::obj(vec![("agent_id", json::s(&existing))]),
            );
            if let Some(task) = &description {
                if !task.is_empty() {
                    let _ = engine.call(
                        "update_agent_status",
                        &json::obj(vec![
                            ("agent_id", json::s(&existing)),
                            ("current_task", json::s(task)),
                        ]),
                    );
                }
            }
            let _ = engine.call(
                "report_subagent_spawned",
                &json::obj(vec![
                    ("parent_agent_id", json::s(&parent)),
                    ("child_agent_id", json::s(existing)),
                    ("subagent_type", json::s(&kind)),
                    ("source", json::s(PREFIX)),
                    ("caller_agent_id", json::s(&parent)),
                ]),
            );
            return;
        }
    }
    let child = format!("{parent}.{kind}.0");
    ensure(engine, &child, Some(&parent), raw.as_deref());
    if let Some(task) = description {
        if !task.is_empty() {
            let _ = engine.call(
                "update_agent_status",
                &json::obj(vec![
                    ("agent_id", json::s(&child)),
                    ("current_task", json::s(task)),
                ]),
            );
        }
    }
    let _ = engine.call(
        "report_subagent_spawned",
        &json::obj(vec![
            ("parent_agent_id", json::s(parent)),
            ("child_agent_id", json::s(child)),
            ("subagent_type", json::s(kind)),
            ("source", json::s(PREFIX)),
            ("caller_agent_id", json::s(session_agent(session))),
        ]),
    );
}

fn on_subagent_stop(engine: &mut Engine, event: &Value, session: &str) {
    let parent = session_agent(session);
    let mut child = resolve(engine, session, raw_id(event));
    if child == parent {
        child = format!("{parent}.{}.0", subagent_type(event));
    }
    let _ = engine.call(
        "deregister_agent",
        &json::obj(vec![("agent_id", json::s(child))]),
    );
}

fn on_index(engine: &mut Engine, event: &Value, session: &str) {
    let agent = resolve(engine, session, raw_id(event));
    let input = event.get("tool_input");
    let mut paths = Vec::new();
    if let Some(path) = input
        .and_then(|v| v.get("document_path"))
        .and_then(Value::as_str)
        .or_else(|| input.and_then(|v| v.get("path")).and_then(Value::as_str))
    {
        paths.push(path.to_string());
    }
    if let Some(items) = input.and_then(|v| v.get("paths")).and_then(Value::as_array) {
        for item in items {
            if let Some(path) = item.as_str() {
                paths.push(path.to_string());
            }
        }
    }
    for path in paths {
        let _ = engine.call(
            "notify_change",
            &json::obj(vec![
                ("document_path", json::s(path)),
                ("change_type", json::s("indexed")),
                ("agent_id", json::s(&agent)),
            ]),
        );
    }
}

fn on_claim(engine: &mut Engine, event: &Value, session: &str) {
    let agent = resolve(engine, session, raw_id(event));
    ensure(engine, &agent, None, raw_id(event));
    let input = event.get("tool_input");
    let step = input
        .and_then(|v| v.get("step_id"))
        .and_then(Value::as_str)
        .unwrap_or("");
    let plan = input
        .and_then(|v| v.get("plan_id"))
        .and_then(Value::as_str)
        .unwrap_or("");
    let task = if plan.is_empty() {
        step.to_string()
    } else {
        format!("trammel:{plan}/{step}")
    };
    let _ = engine.call(
        "update_agent_status",
        &json::obj(vec![
            ("agent_id", json::s(agent)),
            ("current_task", json::s(task)),
        ]),
    );
}

fn on_session_end(engine: &mut Engine, session: &str) -> Option<Value> {
    let agent = session_agent(session);
    let status = engine
        .call("status", &json::obj(vec![]))
        .unwrap_or(json::obj(vec![]));
    let summary = format!(
        "{} agents tracked, {} locks held, {} conflicts, {} notifications",
        status
            .get("registered_agents")
            .and_then(Value::as_i64)
            .unwrap_or(0),
        status
            .get("active_locks")
            .and_then(Value::as_i64)
            .unwrap_or(0),
        status
            .get("recent_conflicts")
            .and_then(Value::as_i64)
            .unwrap_or(0),
        status
            .get("pending_notifications")
            .and_then(Value::as_i64)
            .unwrap_or(0)
    );
    let _ = engine.call(
        "admin_locks",
        &json::obj(vec![
            ("action", json::s("release_by_agent")),
            ("agent_id", json::s(&agent)),
        ]),
    );
    let _ = engine.call(
        "deregister_agent",
        &json::obj(vec![("agent_id", json::s(agent))]),
    );
    Some(json::obj(vec![(
        "hookSpecificOutput",
        json::obj(vec![
            ("hookEventName", json::s("SessionEnd")),
            (
                "additionalContext",
                json::s(format!("[CoordinationHub] Session summary: {summary}")),
            ),
        ]),
    )]))
}

fn redact(text: &str) -> String {
    let mut out = text.to_string();
    for (pattern, replacement) in [
        ("sk-", "[REDACTED_API_KEY]"),
        ("ghp_", "[REDACTED_GH_PAT]"),
        ("github_pat_", "[REDACTED_GH_PAT]"),
        ("AKIA", "[REDACTED_AWS_KEY_ID]"),
    ] {
        if out.contains(pattern) {
            out = out.replace(pattern, replacement);
        }
    }
    out
}

fn sha256_hex(text: &str) -> String {
    let mut hash = Sha256::new();
    hash.update(text.as_bytes());
    hash.finish()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

struct Sha256 {
    state: [u32; 8],
    buffer: [u8; 64],
    filled: usize,
    bits: u64,
}

impl Sha256 {
    fn new() -> Self {
        Self {
            state: [
                0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab,
                0x5be0cd19,
            ],
            buffer: [0; 64],
            filled: 0,
            bits: 0,
        }
    }

    fn update(&mut self, data: &[u8]) {
        self.bits += (data.len() as u64) * 8;
        let mut rest = data;
        if self.filled > 0 {
            let take = rest.len().min(64 - self.filled);
            self.buffer[self.filled..self.filled + take].copy_from_slice(&rest[..take]);
            self.filled += take;
            rest = &rest[take..];
            if self.filled == 64 {
                let block = self.buffer;
                self.compress(&block);
                self.filled = 0;
            }
        }
        while rest.len() >= 64 {
            self.compress(&rest[..64]);
            rest = &rest[64..];
        }
        self.buffer[..rest.len()].copy_from_slice(rest);
        self.filled = rest.len();
    }

    fn finish(mut self) -> [u8; 32] {
        let mut block = [0_u8; 64];
        block[..self.filled].copy_from_slice(&self.buffer[..self.filled]);
        block[self.filled] = 0x80;
        if self.filled >= 56 {
            self.compress(&block);
            block = [0; 64];
        }
        block[56..].copy_from_slice(&self.bits.to_be_bytes());
        self.compress(&block);
        let mut out = [0_u8; 32];
        for (index, word) in self.state.iter().enumerate() {
            out[index * 4..index * 4 + 4].copy_from_slice(&word.to_be_bytes());
        }
        out
    }

    fn compress(&mut self, block: &[u8]) {
        const K: [u32; 64] = [
            0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4,
            0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe,
            0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f,
            0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
            0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc,
            0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
            0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116,
            0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
            0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
            0xc67178f2,
        ];
        let mut words = [0_u32; 64];
        for index in 0..16 {
            words[index] = u32::from_be_bytes([
                block[index * 4],
                block[index * 4 + 1],
                block[index * 4 + 2],
                block[index * 4 + 3],
            ]);
        }
        for index in 16..64 {
            let s0 = words[index - 15].rotate_right(7)
                ^ words[index - 15].rotate_right(18)
                ^ (words[index - 15] >> 3);
            let s1 = words[index - 2].rotate_right(17)
                ^ words[index - 2].rotate_right(19)
                ^ (words[index - 2] >> 10);
            words[index] = words[index - 16]
                .wrapping_add(s0)
                .wrapping_add(words[index - 7])
                .wrapping_add(s1);
        }
        let mut a = self.state[0];
        let mut b = self.state[1];
        let mut c = self.state[2];
        let mut d = self.state[3];
        let mut e = self.state[4];
        let mut f = self.state[5];
        let mut g = self.state[6];
        let mut h = self.state[7];
        for index in 0..64 {
            let s1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
            let ch = (e & f) ^ (!e & g);
            let temp1 = h
                .wrapping_add(s1)
                .wrapping_add(ch)
                .wrapping_add(K[index])
                .wrapping_add(words[index]);
            let s0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
            let maj = (a & b) ^ (a & c) ^ (b & c);
            let temp2 = s0.wrapping_add(maj);
            h = g;
            g = f;
            f = e;
            e = d.wrapping_add(temp1);
            d = c;
            c = b;
            b = a;
            a = temp1.wrapping_add(temp2);
        }
        self.state[0] = self.state[0].wrapping_add(a);
        self.state[1] = self.state[1].wrapping_add(b);
        self.state[2] = self.state[2].wrapping_add(c);
        self.state[3] = self.state[3].wrapping_add(d);
        self.state[4] = self.state[4].wrapping_add(e);
        self.state[5] = self.state[5].wrapping_add(f);
        self.state[6] = self.state[6].wrapping_add(g);
        self.state[7] = self.state[7].wrapping_add(h);
    }
}
