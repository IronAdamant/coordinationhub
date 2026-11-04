//! In-memory tables. Field names match the Python SQLite columns the tools return.

use crate::json::Json;
use std::time::{SystemTime, UNIX_EPOCH};

pub fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

pub fn opt_string(v: &Option<String>) -> Json {
    match v {
        Some(s) => Json::String(s.clone()),
        None => Json::Null,
    }
}

pub fn opt_f64(v: &Option<f64>) -> Json {
    match v {
        Some(n) => Json::Float(*n),
        None => Json::Null,
    }
}

pub fn opt_i64(v: &Option<i64>) -> Json {
    match v {
        Some(n) => Json::Int(*n),
        None => Json::Null,
    }
}

#[derive(Clone, Debug)]
pub struct Agent {
    pub agent_id: String,
    pub parent_id: Option<String>,
    pub worktree_root: String,
    pub pid: u32,
    pub started_at: f64,
    pub last_heartbeat: f64,
    pub status: String,
    pub raw_ide_id: Option<String>,
    pub ide_vendor: Option<String>,
    pub stop_requested_at: Option<f64>,
}

impl Agent {
    pub fn to_json(&self, stale: bool) -> Json {
        Json::object(vec![
            ("agent_id", Json::String(self.agent_id.clone())),
            ("parent_id", opt_string(&self.parent_id)),
            ("worktree_root", Json::String(self.worktree_root.clone())),
            ("pid", Json::Int(self.pid as i64)),
            ("started_at", Json::Float(self.started_at)),
            ("last_heartbeat", Json::Float(self.last_heartbeat)),
            ("status", Json::String(self.status.clone())),
            ("raw_ide_id", opt_string(&self.raw_ide_id)),
            ("ide_vendor", opt_string(&self.ide_vendor)),
            ("stop_requested_at", opt_f64(&self.stop_requested_at)),
            ("stale", Json::Bool(stale)),
        ])
    }
}

#[derive(Clone, Debug)]
pub struct Lineage {
    pub parent_id: String,
    pub child_id: String,
    pub spawned_at: f64,
}

#[derive(Clone, Debug)]
pub struct Descendant {
    pub ancestor_id: String,
    pub descendant_id: String,
    pub depth: i64,
    pub registered_at: f64,
}

#[derive(Clone, Debug)]
pub struct Lock {
    pub id: i64,
    pub document_path: String,
    pub locked_by: String,
    pub locked_at: f64,
    pub lock_ttl: f64,
    pub lock_type: String,
    pub region_start: Option<i64>,
    pub region_end: Option<i64>,
    pub worktree_root: Option<String>,
}

impl Lock {
    pub fn alive(&self, t: f64) -> bool {
        self.locked_at + self.lock_ttl > t
    }
}

#[derive(Clone, Debug)]
pub struct Conflict {
    pub id: i64,
    pub document_path: String,
    pub agent_a: String,
    pub agent_b: String,
    pub conflict_type: String,
    pub resolution: String,
    pub details_json: Option<String>,
    pub created_at: f64,
}

impl Conflict {
    pub fn to_json(&self) -> Json {
        Json::object(vec![
            ("id", Json::Int(self.id)),
            ("document_path", Json::String(self.document_path.clone())),
            ("agent_a", Json::String(self.agent_a.clone())),
            ("agent_b", Json::String(self.agent_b.clone())),
            ("conflict_type", Json::String(self.conflict_type.clone())),
            ("resolution", Json::String(self.resolution.clone())),
            ("details_json", opt_string(&self.details_json)),
            ("created_at", Json::Float(self.created_at)),
        ])
    }
}

#[derive(Clone, Debug)]
pub struct Notification {
    pub id: i64,
    pub document_path: String,
    pub change_type: String,
    pub agent_id: String,
    pub worktree_root: Option<String>,
    pub created_at: f64,
}

impl Notification {
    pub fn to_json(&self) -> Json {
        Json::object(vec![
            ("id", Json::Int(self.id)),
            ("document_path", Json::String(self.document_path.clone())),
            ("change_type", Json::String(self.change_type.clone())),
            ("agent_id", Json::String(self.agent_id.clone())),
            ("worktree_root", opt_string(&self.worktree_root)),
            ("created_at", Json::Float(self.created_at)),
        ])
    }
}

#[derive(Clone, Debug)]
pub struct Responsibility {
    pub agent_id: String,
    pub graph_agent_id: Option<String>,
    pub role: Option<String>,
    pub model: Option<String>,
    pub responsibilities: String,
    pub current_task: Option<String>,
    pub scope: Option<String>,
    pub updated_at: f64,
}

#[derive(Clone, Debug)]
pub struct Ownership {
    pub document_path: String,
    pub assigned_agent_id: String,
    pub assigned_at: f64,
    pub task_description: Option<String>,
}

#[derive(Clone, Debug)]
pub struct Message {
    pub id: i64,
    pub from_agent_id: String,
    pub to_agent_id: String,
    pub message_type: String,
    pub payload_json: Option<String>,
    pub created_at: f64,
    pub read_at: Option<f64>,
}

#[derive(Clone, Debug)]
pub struct Task {
    pub id: String,
    pub parent_agent_id: String,
    pub parent_task_id: Option<String>,
    pub assigned_agent_id: Option<String>,
    pub description: String,
    pub status: String,
    pub created_at: f64,
    pub updated_at: f64,
    pub depends_on: Vec<String>,
    pub blocked_by: Option<String>,
    pub summary: Option<String>,
    pub priority: i64,
    pub error: Option<String>,
}

impl Task {
    pub fn to_json(&self) -> Json {
        let deps = Json::Array(
            self.depends_on
                .iter()
                .cloned()
                .map(Json::String)
                .collect(),
        );
        Json::object(vec![
            ("id", Json::String(self.id.clone())),
            ("parent_agent_id", Json::String(self.parent_agent_id.clone())),
            ("parent_task_id", opt_string(&self.parent_task_id)),
            ("assigned_agent_id", opt_string(&self.assigned_agent_id)),
            ("description", Json::String(self.description.clone())),
            ("status", Json::String(self.status.clone())),
            ("created_at", Json::Float(self.created_at)),
            ("updated_at", Json::Float(self.updated_at)),
            ("depends_on", deps),
            ("blocked_by", opt_string(&self.blocked_by)),
            ("summary", opt_string(&self.summary)),
            ("priority", Json::Int(self.priority)),
            ("error", opt_string(&self.error)),
        ])
    }
}

#[derive(Clone, Debug)]
pub struct Failure {
    pub id: i64,
    pub task_id: String,
    pub error: Option<String>,
    pub attempt: i64,
    pub max_retries: i64,
    pub first_attempt_at: f64,
    pub last_attempt_at: f64,
    pub dead_letter_at: Option<f64>,
    pub status: String,
}

#[derive(Clone, Debug)]
pub struct Intent {
    pub agent_id: String,
    pub document_path: String,
    pub intent: String,
    pub declared_at: f64,
    pub ttl: f64,
}

impl Intent {
    pub fn alive(&self, t: f64) -> bool {
        self.declared_at + self.ttl > t
    }
    pub fn to_json(&self) -> Json {
        Json::object(vec![
            ("agent_id", Json::String(self.agent_id.clone())),
            ("document_path", Json::String(self.document_path.clone())),
            ("intent", Json::String(self.intent.clone())),
            ("declared_at", Json::Float(self.declared_at)),
            ("ttl", Json::Float(self.ttl)),
        ])
    }
}

#[derive(Clone, Debug)]
pub struct Handoff {
    pub id: i64,
    pub from_agent_id: String,
    pub to_agents: Vec<String>,
    pub document_path: Option<String>,
    pub handoff_type: String,
    pub status: String,
    pub created_at: f64,
    pub acknowledged_at: Option<f64>,
    pub completed_at: Option<f64>,
    pub acked_by: Vec<String>,
}

#[derive(Clone, Debug)]
pub struct Dep {
    pub id: i64,
    pub dependent_agent_id: String,
    pub depends_on_agent_id: String,
    pub depends_on_task_id: Option<String>,
    pub condition: String,
    pub satisfied: bool,
    pub satisfied_at: Option<f64>,
    pub created_at: f64,
}

#[derive(Clone, Debug)]
pub struct Lease {
    pub lease_name: String,
    pub holder_id: String,
    pub acquired_at: f64,
    pub ttl: f64,
    pub expires_at: f64,
}

#[derive(Clone, Debug)]
pub struct Spawn {
    pub task_id: String,
    pub scope_id: String,
    pub subagent_type: Option<String>,
    pub description: Option<String>,
    pub prompt: Option<String>,
    pub created_at: f64,
    pub status: String,
    pub source: String,
    pub child_agent_id: Option<String>,
    pub consumed_at: Option<f64>,
}

#[derive(Clone, Debug)]
pub struct Broadcast {
    pub id: i64,
    pub from_agent_id: String,
    pub document_path: Option<String>,
    pub message: Option<String>,
    pub created_at: f64,
    pub ttl: f64,
    pub expires_at: f64,
    pub expected_count: i64,
    pub targets: Vec<String>,
    pub acks: Vec<String>,
}

#[derive(Clone, Debug)]
pub struct Claim {
    pub area: String,
    pub agent_id: String,
    pub claimed_at: f64,
    pub ttl: f64,
    pub note: Option<String>,
}

#[derive(Clone, Debug, Default)]
pub struct State {
    pub next_lock: i64,
    pub next_conflict: i64,
    pub next_notif: i64,
    pub next_message: i64,
    pub next_broadcast: i64,
    pub next_handoff: i64,
    pub next_dep: i64,
    pub next_event: i64,
    pub agents: Vec<Agent>,
    pub lineage: Vec<Lineage>,
    pub descendants: Vec<Descendant>,
    pub locks: Vec<Lock>,
    pub conflicts: Vec<Conflict>,
    pub notifications: Vec<Notification>,
    pub responsibilities: Vec<Responsibility>,
    pub ownership: Vec<Ownership>,
    pub messages: Vec<Message>,
    pub tasks: Vec<Task>,
    pub failures: Vec<Failure>,
    pub intents: Vec<Intent>,
    pub handoffs: Vec<Handoff>,
    pub deps: Vec<Dep>,
    pub lease: Option<Lease>,
    pub spawns: Vec<Spawn>,
    pub broadcasts: Vec<Broadcast>,
    pub claims: Vec<Claim>,
    pub graph_loaded: bool,
    pub spec_path: Option<String>,
}

impl State {
    pub fn alloc(&mut self, slot: &str) -> i64 {
        let n = match slot {
            "lock" => &mut self.next_lock,
            "conflict" => &mut self.next_conflict,
            "notif" => &mut self.next_notif,
            "message" => &mut self.next_message,
            "broadcast" => &mut self.next_broadcast,
            "handoff" => &mut self.next_handoff,
            "dep" => &mut self.next_dep,
            _ => &mut self.next_event,
        };
        *n += 1;
        *n
    }

    pub fn agent_mut(&mut self, id: &str) -> Option<&mut Agent> {
        self.agents.iter_mut().find(|a| a.agent_id == id)
    }

    pub fn agent(&self, id: &str) -> Option<&Agent> {
        self.agents.iter().find(|a| a.agent_id == id)
    }
}
