//! Process-safe JSON snapshot under `<storage>/.coordinationhub/state.json`.
//!
//! Rust std has no SQLite. The logical tables match the Python schema.
//! The file layout itself is a deliberate change, described in MIGRATION.md.

use crate::json::{self, Json};
use crate::model::*;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

pub struct Store {
    pub dir: PathBuf,
}

pub struct FileGuard {
    path: PathBuf,
}

impl Drop for FileGuard {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.path);
    }
}

impl Store {
    pub fn open(dir: &Path) -> std::io::Result<Store> {
        fs::create_dir_all(dir)?;
        Ok(Store {
            dir: dir.to_path_buf(),
        })
    }

    pub fn state_path(&self) -> PathBuf {
        self.dir.join("state.json")
    }

    fn lock_path(&self) -> PathBuf {
        self.dir.join("state.lock")
    }

    pub fn lock(&self) -> FileGuard {
        let path = self.lock_path();
        loop {
            match OpenOptions::new().write(true).create_new(true).open(&path) {
                Ok(mut f) => {
                    let _ = writeln!(f, "{}", std::process::id());
                    return FileGuard { path };
                }
                Err(_) => {
                    let stale = fs::metadata(&path)
                        .and_then(|m| m.modified())
                        .ok()
                        .and_then(|t| t.duration_since(SystemTime::UNIX_EPOCH).ok())
                        .map(|d| {
                            let now = SystemTime::now()
                                .duration_since(UNIX_EPOCH)
                                .unwrap_or_default();
                            now.saturating_sub(d) > Duration::from_secs(30)
                        })
                        .unwrap_or(true);
                    if stale {
                        let _ = fs::remove_file(&path);
                    } else {
                        thread::sleep(Duration::from_millis(2));
                    }
                }
            }
        }
    }

    pub fn load(&self) -> State {
        let path = self.state_path();
        let mut file = match File::open(&path) {
            Ok(f) => f,
            Err(_) => return State::default(),
        };
        let mut text = String::new();
        if file.read_to_string(&mut text).is_err() {
            return State::default();
        }
        match json::parse(&text) {
            Ok(v) => state_from_json(&v),
            Err(_) => State::default(),
        }
    }

    pub fn save(&self, state: &State) -> std::io::Result<()> {
        let path = self.state_path();
        let tmp = self.dir.join("state.json.tmp");
        let body = state_to_json(state).dump();
        {
            let mut f = File::create(&tmp)?;
            f.write_all(body.as_bytes())?;
            f.write_all(b"\n")?;
        }
        fs::rename(&tmp, &path)?;
        Ok(())
    }
}

fn s(v: &Json, k: &str) -> Option<String> {
    v.get(k).and_then(|x| x.as_str()).map(|x| x.to_string())
}
fn sf(v: &Json, k: &str) -> f64 {
    v.get(k).and_then(|x| x.as_f64()).unwrap_or(0.0)
}
fn si(v: &Json, k: &str) -> i64 {
    v.get(k).and_then(|x| x.as_i64()).unwrap_or(0)
}
fn sb(v: &Json, k: &str) -> bool {
    v.get(k).and_then(|x| x.as_bool()).unwrap_or(false)
}
fn so(v: &Json, k: &str) -> Option<String> {
    match v.get(k) {
        Some(Json::String(s)) => Some(s.clone()),
        _ => None,
    }
}
fn sof(v: &Json, k: &str) -> Option<f64> {
    v.get(k).and_then(|x| x.as_f64())
}
fn arr<'a>(v: &'a Json, k: &str) -> Vec<&'a Json> {
    v.get(k)
        .and_then(|x| x.as_array())
        .map(|a| a.iter().collect())
        .unwrap_or_default()
}
fn strs(v: &Json, k: &str) -> Vec<String> {
    arr(v, k)
        .into_iter()
        .filter_map(|x| x.as_str().map(|s| s.to_string()))
        .collect()
}

fn state_from_json(v: &Json) -> State {
    let mut st = State {
        next_lock: si(v, "next_lock"),
        next_conflict: si(v, "next_conflict"),
        next_notif: si(v, "next_notif"),
        next_message: si(v, "next_message"),
        next_broadcast: si(v, "next_broadcast"),
        next_handoff: si(v, "next_handoff"),
        next_dep: si(v, "next_dep"),
        next_event: si(v, "next_event"),
        graph_loaded: sb(v, "graph_loaded"),
        spec_path: so(v, "spec_path"),
        ..State::default()
    };
    for a in arr(v, "agents") {
        st.agents.push(Agent {
            agent_id: s(a, "agent_id").unwrap_or_default(),
            parent_id: so(a, "parent_id"),
            worktree_root: s(a, "worktree_root").unwrap_or_default(),
            pid: si(a, "pid") as u32,
            started_at: sf(a, "started_at"),
            last_heartbeat: sf(a, "last_heartbeat"),
            status: s(a, "status").unwrap_or_else(|| "active".into()),
            raw_ide_id: so(a, "raw_ide_id"),
            ide_vendor: so(a, "ide_vendor"),
            stop_requested_at: sof(a, "stop_requested_at"),
        });
    }
    for x in arr(v, "lineage") {
        st.lineage.push(Lineage {
            parent_id: s(x, "parent_id").unwrap_or_default(),
            child_id: s(x, "child_id").unwrap_or_default(),
            spawned_at: sf(x, "spawned_at"),
        });
    }
    for x in arr(v, "descendants") {
        st.descendants.push(Descendant {
            ancestor_id: s(x, "ancestor_id").unwrap_or_default(),
            descendant_id: s(x, "descendant_id").unwrap_or_default(),
            depth: si(x, "depth"),
            registered_at: sf(x, "registered_at"),
        });
    }
    for x in arr(v, "locks") {
        st.locks.push(Lock {
            id: si(x, "id"),
            document_path: s(x, "document_path").unwrap_or_default(),
            locked_by: s(x, "locked_by").unwrap_or_default(),
            locked_at: sf(x, "locked_at"),
            lock_ttl: sf(x, "lock_ttl"),
            lock_type: s(x, "lock_type").unwrap_or_else(|| "exclusive".into()),
            region_start: x.get("region_start").and_then(|n| n.as_i64()),
            region_end: x.get("region_end").and_then(|n| n.as_i64()),
            worktree_root: so(x, "worktree_root"),
        });
    }
    for x in arr(v, "conflicts") {
        st.conflicts.push(Conflict {
            id: si(x, "id"),
            document_path: s(x, "document_path").unwrap_or_default(),
            agent_a: s(x, "agent_a").unwrap_or_default(),
            agent_b: s(x, "agent_b").unwrap_or_default(),
            conflict_type: s(x, "conflict_type").unwrap_or_default(),
            resolution: s(x, "resolution").unwrap_or_else(|| "rejected".into()),
            details_json: so(x, "details_json"),
            created_at: sf(x, "created_at"),
        });
    }
    for x in arr(v, "notifications") {
        st.notifications.push(Notification {
            id: si(x, "id"),
            document_path: s(x, "document_path").unwrap_or_default(),
            change_type: s(x, "change_type").unwrap_or_default(),
            agent_id: s(x, "agent_id").unwrap_or_default(),
            worktree_root: so(x, "worktree_root"),
            created_at: sf(x, "created_at"),
        });
    }
    for x in arr(v, "responsibilities") {
        st.responsibilities.push(Responsibility {
            agent_id: s(x, "agent_id").unwrap_or_default(),
            graph_agent_id: so(x, "graph_agent_id"),
            role: so(x, "role"),
            model: so(x, "model"),
            responsibilities: s(x, "responsibilities").unwrap_or_else(|| "[]".into()),
            current_task: so(x, "current_task"),
            scope: so(x, "scope"),
            updated_at: sf(x, "updated_at"),
        });
    }
    for x in arr(v, "ownership") {
        st.ownership.push(Ownership {
            document_path: s(x, "document_path").unwrap_or_default(),
            assigned_agent_id: s(x, "assigned_agent_id").unwrap_or_default(),
            assigned_at: sf(x, "assigned_at"),
            task_description: so(x, "task_description"),
        });
    }
    for x in arr(v, "messages") {
        st.messages.push(Message {
            id: si(x, "id"),
            from_agent_id: s(x, "from_agent_id").unwrap_or_default(),
            to_agent_id: s(x, "to_agent_id").unwrap_or_default(),
            message_type: s(x, "message_type").unwrap_or_default(),
            payload_json: so(x, "payload_json"),
            created_at: sf(x, "created_at"),
            read_at: sof(x, "read_at"),
        });
    }
    for x in arr(v, "tasks") {
        st.tasks.push(Task {
            id: s(x, "id").unwrap_or_default(),
            parent_agent_id: s(x, "parent_agent_id").unwrap_or_default(),
            parent_task_id: so(x, "parent_task_id"),
            assigned_agent_id: so(x, "assigned_agent_id"),
            description: s(x, "description").unwrap_or_default(),
            status: s(x, "status").unwrap_or_else(|| "pending".into()),
            created_at: sf(x, "created_at"),
            updated_at: sf(x, "updated_at"),
            depends_on: strs(x, "depends_on"),
            blocked_by: so(x, "blocked_by"),
            summary: so(x, "summary"),
            priority: si(x, "priority"),
            error: so(x, "error"),
        });
    }
    for x in arr(v, "failures") {
        st.failures.push(Failure {
            id: si(x, "id"),
            task_id: s(x, "task_id").unwrap_or_default(),
            error: so(x, "error"),
            attempt: si(x, "attempt"),
            max_retries: si(x, "max_retries"),
            first_attempt_at: sf(x, "first_attempt_at"),
            last_attempt_at: sf(x, "last_attempt_at"),
            dead_letter_at: sof(x, "dead_letter_at"),
            status: s(x, "status").unwrap_or_else(|| "failed".into()),
        });
    }
    for x in arr(v, "intents") {
        st.intents.push(Intent {
            agent_id: s(x, "agent_id").unwrap_or_default(),
            document_path: s(x, "document_path").unwrap_or_default(),
            intent: s(x, "intent").unwrap_or_default(),
            declared_at: sf(x, "declared_at"),
            ttl: sf(x, "ttl"),
        });
    }
    for x in arr(v, "handoffs") {
        st.handoffs.push(Handoff {
            id: si(x, "id"),
            from_agent_id: s(x, "from_agent_id").unwrap_or_default(),
            to_agents: strs(x, "to_agents"),
            document_path: so(x, "document_path"),
            handoff_type: s(x, "handoff_type").unwrap_or_else(|| "scope_transfer".into()),
            status: s(x, "status").unwrap_or_else(|| "pending".into()),
            created_at: sf(x, "created_at"),
            acknowledged_at: sof(x, "acknowledged_at"),
            completed_at: sof(x, "completed_at"),
            acked_by: strs(x, "acked_by"),
        });
    }
    for x in arr(v, "deps") {
        st.deps.push(Dep {
            id: si(x, "id"),
            dependent_agent_id: s(x, "dependent_agent_id").unwrap_or_default(),
            depends_on_agent_id: s(x, "depends_on_agent_id").unwrap_or_default(),
            depends_on_task_id: so(x, "depends_on_task_id"),
            condition: s(x, "condition").unwrap_or_else(|| "task_completed".into()),
            satisfied: sb(x, "satisfied"),
            satisfied_at: sof(x, "satisfied_at"),
            created_at: sf(x, "created_at"),
        });
    }
    if let Some(x) = v.get("lease") {
        if !x.is_null() {
            st.lease = Some(Lease {
                lease_name: s(x, "lease_name").unwrap_or_default(),
                holder_id: s(x, "holder_id").unwrap_or_default(),
                acquired_at: sf(x, "acquired_at"),
                ttl: sf(x, "ttl"),
                expires_at: sf(x, "expires_at"),
            });
        }
    }
    for x in arr(v, "spawns") {
        st.spawns.push(Spawn {
            task_id: s(x, "task_id").unwrap_or_default(),
            scope_id: s(x, "scope_id").unwrap_or_default(),
            subagent_type: so(x, "subagent_type"),
            description: so(x, "description"),
            prompt: so(x, "prompt"),
            created_at: sf(x, "created_at"),
            status: s(x, "status").unwrap_or_else(|| "pending".into()),
            source: s(x, "source").unwrap_or_else(|| "external".into()),
            child_agent_id: so(x, "child_agent_id"),
            consumed_at: sof(x, "consumed_at"),
        });
    }
    for x in arr(v, "broadcasts") {
        st.broadcasts.push(Broadcast {
            id: si(x, "id"),
            from_agent_id: s(x, "from_agent_id").unwrap_or_default(),
            document_path: so(x, "document_path"),
            message: so(x, "message"),
            created_at: sf(x, "created_at"),
            ttl: sf(x, "ttl"),
            expires_at: sf(x, "expires_at"),
            expected_count: si(x, "expected_count"),
            targets: strs(x, "targets"),
            acks: strs(x, "acks"),
        });
    }
    for x in arr(v, "claims") {
        st.claims.push(Claim {
            area: s(x, "area").unwrap_or_default(),
            agent_id: s(x, "agent_id").unwrap_or_default(),
            claimed_at: sf(x, "claimed_at"),
            ttl: sf(x, "ttl"),
            note: so(x, "note"),
        });
    }
    st
}

fn jstr(s: &str) -> Json {
    Json::String(s.to_string())
}
fn jopt(s: &Option<String>) -> Json {
    opt_string(s)
}
fn jstrs(v: &[String]) -> Json {
    Json::Array(v.iter().cloned().map(Json::String).collect())
}

fn state_to_json(st: &State) -> Json {
    let agents = Json::Array(
        st.agents
            .iter()
            .map(|a| {
                Json::object(vec![
                    ("agent_id", jstr(&a.agent_id)),
                    ("parent_id", jopt(&a.parent_id)),
                    ("worktree_root", jstr(&a.worktree_root)),
                    ("pid", Json::Int(a.pid as i64)),
                    ("started_at", Json::Float(a.started_at)),
                    ("last_heartbeat", Json::Float(a.last_heartbeat)),
                    ("status", jstr(&a.status)),
                    ("raw_ide_id", jopt(&a.raw_ide_id)),
                    ("ide_vendor", jopt(&a.ide_vendor)),
                    ("stop_requested_at", opt_f64(&a.stop_requested_at)),
                ])
            })
            .collect(),
    );
    let locks = Json::Array(st.locks.iter().map(|l| {
        Json::object(vec![
            ("id", Json::Int(l.id)),
            ("document_path", jstr(&l.document_path)),
            ("locked_by", jstr(&l.locked_by)),
            ("locked_at", Json::Float(l.locked_at)),
            ("lock_ttl", Json::Float(l.lock_ttl)),
            ("lock_type", jstr(&l.lock_type)),
            ("region_start", opt_i64(&l.region_start)),
            ("region_end", opt_i64(&l.region_end)),
            ("worktree_root", jopt(&l.worktree_root)),
        ])
    }).collect());
    Json::object(vec![
        ("version", Json::Int(1)),
        ("next_lock", Json::Int(st.next_lock)),
        ("next_conflict", Json::Int(st.next_conflict)),
        ("next_notif", Json::Int(st.next_notif)),
        ("next_message", Json::Int(st.next_message)),
        ("next_broadcast", Json::Int(st.next_broadcast)),
        ("next_handoff", Json::Int(st.next_handoff)),
        ("next_dep", Json::Int(st.next_dep)),
        ("next_event", Json::Int(st.next_event)),
        ("graph_loaded", Json::Bool(st.graph_loaded)),
        ("spec_path", jopt(&st.spec_path)),
        ("agents", agents),
        ("lineage", Json::Array(st.lineage.iter().map(|x| Json::object(vec![
            ("parent_id", jstr(&x.parent_id)),
            ("child_id", jstr(&x.child_id)),
            ("spawned_at", Json::Float(x.spawned_at)),
        ])).collect())),
        ("descendants", Json::Array(st.descendants.iter().map(|x| Json::object(vec![
            ("ancestor_id", jstr(&x.ancestor_id)),
            ("descendant_id", jstr(&x.descendant_id)),
            ("depth", Json::Int(x.depth)),
            ("registered_at", Json::Float(x.registered_at)),
        ])).collect())),
        ("locks", locks),
        ("conflicts", Json::Array(st.conflicts.iter().map(|c| c.to_json()).collect())),
        ("notifications", Json::Array(st.notifications.iter().map(|n| n.to_json()).collect())),
        ("responsibilities", Json::Array(st.responsibilities.iter().map(|r| Json::object(vec![
            ("agent_id", jstr(&r.agent_id)),
            ("graph_agent_id", jopt(&r.graph_agent_id)),
            ("role", jopt(&r.role)),
            ("model", jopt(&r.model)),
            ("responsibilities", jstr(&r.responsibilities)),
            ("current_task", jopt(&r.current_task)),
            ("scope", jopt(&r.scope)),
            ("updated_at", Json::Float(r.updated_at)),
        ])).collect())),
        ("ownership", Json::Array(st.ownership.iter().map(|o| Json::object(vec![
            ("document_path", jstr(&o.document_path)),
            ("assigned_agent_id", jstr(&o.assigned_agent_id)),
            ("assigned_at", Json::Float(o.assigned_at)),
            ("task_description", jopt(&o.task_description)),
        ])).collect())),
        ("messages", Json::Array(st.messages.iter().map(|m| Json::object(vec![
            ("id", Json::Int(m.id)),
            ("from_agent_id", jstr(&m.from_agent_id)),
            ("to_agent_id", jstr(&m.to_agent_id)),
            ("message_type", jstr(&m.message_type)),
            ("payload_json", jopt(&m.payload_json)),
            ("created_at", Json::Float(m.created_at)),
            ("read_at", opt_f64(&m.read_at)),
        ])).collect())),
        ("tasks", Json::Array(st.tasks.iter().map(|t| t.to_json()).collect())),
        ("failures", Json::Array(st.failures.iter().map(|f| Json::object(vec![
            ("id", Json::Int(f.id)),
            ("task_id", jstr(&f.task_id)),
            ("error", jopt(&f.error)),
            ("attempt", Json::Int(f.attempt)),
            ("max_retries", Json::Int(f.max_retries)),
            ("first_attempt_at", Json::Float(f.first_attempt_at)),
            ("last_attempt_at", Json::Float(f.last_attempt_at)),
            ("dead_letter_at", opt_f64(&f.dead_letter_at)),
            ("status", jstr(&f.status)),
        ])).collect())),
        ("intents", Json::Array(st.intents.iter().map(|i| i.to_json()).collect())),
        ("handoffs", Json::Array(st.handoffs.iter().map(|h| Json::object(vec![
            ("id", Json::Int(h.id)),
            ("from_agent_id", jstr(&h.from_agent_id)),
            ("to_agents", jstrs(&h.to_agents)),
            ("document_path", jopt(&h.document_path)),
            ("handoff_type", jstr(&h.handoff_type)),
            ("status", jstr(&h.status)),
            ("created_at", Json::Float(h.created_at)),
            ("acknowledged_at", opt_f64(&h.acknowledged_at)),
            ("completed_at", opt_f64(&h.completed_at)),
            ("acked_by", jstrs(&h.acked_by)),
        ])).collect())),
        ("deps", Json::Array(st.deps.iter().map(|d| Json::object(vec![
            ("id", Json::Int(d.id)),
            ("dependent_agent_id", jstr(&d.dependent_agent_id)),
            ("depends_on_agent_id", jstr(&d.depends_on_agent_id)),
            ("depends_on_task_id", jopt(&d.depends_on_task_id)),
            ("condition", jstr(&d.condition)),
            ("satisfied", Json::Bool(d.satisfied)),
            ("satisfied_at", opt_f64(&d.satisfied_at)),
            ("created_at", Json::Float(d.created_at)),
        ])).collect())),
        ("lease", match &st.lease {
            None => Json::Null,
            Some(l) => Json::object(vec![
                ("lease_name", jstr(&l.lease_name)),
                ("holder_id", jstr(&l.holder_id)),
                ("acquired_at", Json::Float(l.acquired_at)),
                ("ttl", Json::Float(l.ttl)),
                ("expires_at", Json::Float(l.expires_at)),
            ]),
        }),
        ("spawns", Json::Array(st.spawns.iter().map(|p| Json::object(vec![
            ("task_id", jstr(&p.task_id)),
            ("scope_id", jstr(&p.scope_id)),
            ("subagent_type", jopt(&p.subagent_type)),
            ("description", jopt(&p.description)),
            ("prompt", jopt(&p.prompt)),
            ("created_at", Json::Float(p.created_at)),
            ("status", jstr(&p.status)),
            ("source", jstr(&p.source)),
            ("child_agent_id", jopt(&p.child_agent_id)),
            ("consumed_at", opt_f64(&p.consumed_at)),
        ])).collect())),
        ("broadcasts", Json::Array(st.broadcasts.iter().map(|b| Json::object(vec![
            ("id", Json::Int(b.id)),
            ("from_agent_id", jstr(&b.from_agent_id)),
            ("document_path", jopt(&b.document_path)),
            ("message", jopt(&b.message)),
            ("created_at", Json::Float(b.created_at)),
            ("ttl", Json::Float(b.ttl)),
            ("expires_at", Json::Float(b.expires_at)),
            ("expected_count", Json::Int(b.expected_count)),
            ("targets", jstrs(&b.targets)),
            ("acks", jstrs(&b.acks)),
        ])).collect())),
        ("claims", Json::Array(st.claims.iter().map(|c| Json::object(vec![
            ("area", jstr(&c.area)),
            ("agent_id", jstr(&c.agent_id)),
            ("claimed_at", Json::Float(c.claimed_at)),
            ("ttl", Json::Float(c.ttl)),
            ("note", jopt(&c.note)),
        ])).collect())),
    ])
}
