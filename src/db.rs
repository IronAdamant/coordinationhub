//! On-disk journal. One JSON snapshot, rewritten under an exclusive file lock.

use crate::json::{self, Value};
use crate::util::{self, FileLock};
use std::path::{Path, PathBuf};

#[derive(Clone, Debug, PartialEq)]
pub struct Agent {
    pub agent_id: String,
    pub parent_id: Option<String>,
    pub worktree_root: String,
    pub pid: i64,
    pub started_at: f64,
    pub last_heartbeat: f64,
    pub status: String,
    pub raw_ide_id: Option<String>,
    pub ide_vendor: Option<String>,
    pub stop_requested_at: Option<f64>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Lineage {
    pub parent_id: String,
    pub child_id: String,
    pub spawned_at: f64,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Descendant {
    pub ancestor_id: String,
    pub descendant_id: String,
    pub depth: i64,
    pub registered_at: f64,
}

#[derive(Clone, Debug, PartialEq)]
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

#[derive(Clone, Debug, PartialEq)]
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

#[derive(Clone, Debug, PartialEq)]
pub struct Notification {
    pub id: i64,
    pub document_path: String,
    pub change_type: String,
    pub agent_id: String,
    pub worktree_root: Option<String>,
    pub created_at: f64,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Responsibility {
    pub agent_id: String,
    pub graph_agent_id: Option<String>,
    pub role: Option<String>,
    pub model: Option<String>,
    pub responsibilities: Option<String>,
    pub current_task: Option<String>,
    pub scope: Option<String>,
    pub updated_at: f64,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Ownership {
    pub document_path: String,
    pub assigned_agent_id: String,
    pub assigned_at: f64,
    pub last_claimed_by: Option<String>,
    pub task_description: Option<String>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Message {
    pub id: i64,
    pub from_agent_id: String,
    pub to_agent_id: String,
    pub message_type: String,
    pub payload_json: Option<String>,
    pub created_at: f64,
    pub read_at: Option<f64>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Task {
    pub id: String,
    pub parent_agent_id: String,
    pub parent_task_id: Option<String>,
    pub assigned_agent_id: Option<String>,
    pub description: String,
    pub status: String,
    pub created_at: f64,
    pub updated_at: f64,
    pub depends_on: String,
    pub blocked_by: Option<String>,
    pub summary: Option<String>,
    pub priority: i64,
    pub error: Option<String>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Intent {
    pub agent_id: String,
    pub document_path: String,
    pub intent: String,
    pub declared_at: f64,
    pub ttl: f64,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Handoff {
    pub id: i64,
    pub from_agent_id: String,
    pub to_agents: String,
    pub document_path: Option<String>,
    pub handoff_type: String,
    pub status: String,
    pub created_at: f64,
    pub acknowledged_at: Option<f64>,
    pub completed_at: Option<f64>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct HandoffAck {
    pub handoff_id: i64,
    pub agent_id: String,
    pub acknowledged_at: f64,
}

#[derive(Clone, Debug, PartialEq)]
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

#[derive(Clone, Debug, PartialEq)]
pub struct Lease {
    pub lease_name: String,
    pub holder_id: String,
    pub acquired_at: f64,
    pub ttl: f64,
    pub expires_at: f64,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Dependency {
    pub id: i64,
    pub dependent_agent_id: String,
    pub depends_on_agent_id: String,
    pub depends_on_task_id: Option<String>,
    pub condition: String,
    pub satisfied: i64,
    pub satisfied_at: Option<f64>,
    pub created_at: f64,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Spawn {
    pub id: String,
    pub parent_agent_id: String,
    pub subagent_type: Option<String>,
    pub description: Option<String>,
    pub prompt: Option<String>,
    pub created_at: f64,
    pub consumed_at: Option<f64>,
    pub status: String,
    pub source: String,
    pub child_agent_id: Option<String>,
    pub stop_requested: bool,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Broadcast {
    pub id: i64,
    pub from_agent_id: String,
    pub document_path: Option<String>,
    pub message: Option<String>,
    pub created_at: f64,
    pub ttl: f64,
    pub expires_at: f64,
    pub expected_count: i64,
}

#[derive(Clone, Debug, PartialEq)]
pub struct BroadcastAck {
    pub broadcast_id: i64,
    pub agent_id: String,
    pub acknowledged_at: f64,
}

#[derive(Clone, Debug, PartialEq)]
pub struct BroadcastTarget {
    pub broadcast_id: i64,
    pub agent_id: String,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Claim {
    pub id: i64,
    pub agent_id: String,
    pub area: String,
    pub claimed_at: f64,
    pub ttl: f64,
    pub expires_at: f64,
    pub note: Option<String>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct ClaimEvent {
    pub id: i64,
    pub area: String,
    pub from_agent_id: Option<String>,
    pub to_agent_id: Option<String>,
    pub action: String,
    pub at: f64,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Ids {
    pub locks: i64,
    pub conflicts: i64,
    pub notifications: i64,
    pub messages: i64,
    pub handoffs: i64,
    pub failures: i64,
    pub dependencies: i64,
    pub broadcasts: i64,
    pub claims: i64,
    pub claim_events: i64,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct State {
    pub ids: Ids,
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
    pub intents: Vec<Intent>,
    pub handoffs: Vec<Handoff>,
    pub handoff_acks: Vec<HandoffAck>,
    pub failures: Vec<Failure>,
    pub leases: Vec<Lease>,
    pub dependencies: Vec<Dependency>,
    pub spawns: Vec<Spawn>,
    pub broadcasts: Vec<Broadcast>,
    pub broadcast_acks: Vec<BroadcastAck>,
    pub broadcast_targets: Vec<BroadcastTarget>,
    pub claims: Vec<Claim>,
    pub claim_events: Vec<ClaimEvent>,
}

impl State {
    pub fn next(&mut self, slot: &str) -> i64 {
        let cell = match slot {
            "locks" => &mut self.ids.locks,
            "conflicts" => &mut self.ids.conflicts,
            "notifications" => &mut self.ids.notifications,
            "messages" => &mut self.ids.messages,
            "handoffs" => &mut self.ids.handoffs,
            "failures" => &mut self.ids.failures,
            "dependencies" => &mut self.ids.dependencies,
            "broadcasts" => &mut self.ids.broadcasts,
            "claims" => &mut self.ids.claims,
            "claim_events" => &mut self.ids.claim_events,
            _ => panic!("unknown id slot"),
        };
        *cell += 1;
        *cell
    }

    pub fn agent_mut(&mut self, id: &str) -> Option<&mut Agent> {
        self.agents.iter_mut().find(|a| a.agent_id == id)
    }

    pub fn agent(&self, id: &str) -> Option<&Agent> {
        self.agents.iter().find(|a| a.agent_id == id)
    }
}

pub struct Store {
    pub path: PathBuf,
    pub state: State,
    digests: Digests,
    offset: u64,
    lock: Option<FileLock>,
}

#[derive(Default)]
struct Digests {
    agents: std::collections::HashMap<String, u64>,
    locks: std::collections::HashMap<String, u64>,
    conflicts: std::collections::HashMap<String, u64>,
    notifications: std::collections::HashMap<String, u64>,
    responsibilities: std::collections::HashMap<String, u64>,
    ownership: std::collections::HashMap<String, u64>,
    messages: std::collections::HashMap<String, u64>,
    tasks: std::collections::HashMap<String, u64>,
    intents: std::collections::HashMap<String, u64>,
    handoffs: std::collections::HashMap<String, u64>,
    failures: std::collections::HashMap<String, u64>,
    leases: std::collections::HashMap<String, u64>,
    dependencies: std::collections::HashMap<String, u64>,
    spawns: std::collections::HashMap<String, u64>,
    broadcasts: std::collections::HashMap<String, u64>,
    claims: std::collections::HashMap<String, u64>,
    claim_events: std::collections::HashMap<String, u64>,
    lineage: u64,
    descendants: u64,
    handoff_acks: u64,
    broadcast_acks: u64,
    broadcast_targets: u64,
    ids: u64,
}

impl Store {
    pub fn open(dir: &Path) -> Result<Self, String> {
        std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
        let path = dir.join("coordination.jsonl");
        let lock =
            FileLock::exclusive(&dir.join("coordination.lock")).map_err(|e| e.to_string())?;
        let (state, offset) = load_journal(&path)?;
        drop(lock);
        let mut store = Self {
            path,
            state,
            digests: Digests::default(),
            offset,
            lock: None,
        };
        store.remember();
        Ok(store)
    }

    pub fn begin(&mut self) -> Result<(), String> {
        if self.lock.is_some() {
            return Ok(());
        }
        let lock_path = self
            .path
            .parent()
            .unwrap_or_else(|| Path::new("."))
            .join("coordination.lock");
        let lock = FileLock::exclusive(&lock_path).map_err(|e| e.to_string())?;
        self.lock = Some(lock);
        let dirty = !self.changed_lines().is_empty();
        let applied = self.catch_up()?;
        if applied && !dirty {
            self.remember();
        }
        Ok(())
    }

    pub fn end(&mut self) {
        self.lock = None;
    }

    pub fn event_cursor(&self) -> u64 {
        self.offset
    }

    fn catch_up(&mut self) -> Result<bool, String> {
        if !self.path.is_file() {
            return Ok(false);
        }
        let len = std::fs::metadata(&self.path)
            .map_err(|e| e.to_string())?
            .len();
        if len < self.offset {
            let (state, offset) = load_journal(&self.path)?;
            self.state = state;
            self.offset = offset;
            return Ok(true);
        }
        if len == self.offset {
            return Ok(false);
        }
        use std::io::{Read, Seek, SeekFrom};
        let mut file = std::fs::File::open(&self.path).map_err(|e| e.to_string())?;
        file.seek(SeekFrom::Start(self.offset))
            .map_err(|e| e.to_string())?;
        let mut buf = String::new();
        file.read_to_string(&mut buf).map_err(|e| e.to_string())?;
        let mut consumed = 0usize;
        for line in buf.split_inclusive('\n') {
            if !line.ends_with('\n') {
                break;
            }
            consumed += line.len();
            let trimmed = line.trim();
            if trimmed.is_empty() {
                continue;
            }
            let value = json::parse(trimmed)?;
            apply_line(&mut self.state, &value)?;
        }
        self.offset += consumed as u64;
        Ok(consumed > 0)
    }

    pub fn commit(&mut self) -> Result<(), String> {
        let held = self.lock.is_some();
        if !held {
            self.begin()?;
        }
        let lines = self.changed_lines();
        if !lines.is_empty() {
            use std::io::Write;
            let mut file = std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(&self.path)
                .map_err(|e| e.to_string())?;
            for line in &lines {
                file.write_all(line.as_bytes()).map_err(|e| e.to_string())?;
                file.write_all(b"\n").map_err(|e| e.to_string())?;
            }
            file.flush().map_err(|e| e.to_string())?;
            self.offset = std::fs::metadata(&self.path)
                .map_err(|e| e.to_string())?
                .len();
        }
        if !held {
            self.end();
        }
        Ok(())
    }

    fn remember(&mut self) {
        self.digests.agents =
            digest_map(&self.state.agents, |row| row.agent_id.clone(), hash_agent);
        self.digests.locks = digest_map(&self.state.locks, |row| row.id.to_string(), hash_lock);
        self.digests.conflicts = digest_map(
            &self.state.conflicts,
            |row| row.id.to_string(),
            hash_conflict,
        );
        self.digests.notifications = digest_map(
            &self.state.notifications,
            |row| row.id.to_string(),
            hash_note,
        );
        self.digests.responsibilities = digest_map(
            &self.state.responsibilities,
            |row| row.agent_id.clone(),
            hash_resp,
        );
        self.digests.ownership = digest_map(
            &self.state.ownership,
            |row| row.document_path.clone(),
            hash_own,
        );
        self.digests.messages =
            digest_map(&self.state.messages, |row| row.id.to_string(), hash_msg);
        self.digests.tasks = digest_map(&self.state.tasks, |row| row.id.clone(), hash_task);
        self.digests.intents = digest_map(
            &self.state.intents,
            |row| format!("{}|{}", row.agent_id, row.document_path),
            hash_intent,
        );
        self.digests.handoffs =
            digest_map(&self.state.handoffs, |row| row.id.to_string(), hash_handoff);
        self.digests.failures =
            digest_map(&self.state.failures, |row| row.id.to_string(), hash_fail);
        self.digests.leases =
            digest_map(&self.state.leases, |row| row.lease_name.clone(), hash_lease);
        self.digests.dependencies =
            digest_map(&self.state.dependencies, |row| row.id.to_string(), hash_dep);
        self.digests.spawns = digest_map(&self.state.spawns, |row| row.id.clone(), hash_spawn);
        self.digests.broadcasts =
            digest_map(&self.state.broadcasts, |row| row.id.to_string(), hash_bc);
        self.digests.claims = digest_map(&self.state.claims, |row| row.id.to_string(), hash_claim);
        self.digests.claim_events =
            digest_map(&self.state.claim_events, |row| row.id.to_string(), hash_ce);
        self.digests.lineage = hash_slice(&self.state.lineage, hash_lineage);
        self.digests.descendants = hash_slice(&self.state.descendants, hash_desc);
        self.digests.handoff_acks = hash_slice(&self.state.handoff_acks, hash_hack);
        self.digests.broadcast_acks = hash_slice(&self.state.broadcast_acks, hash_back);
        self.digests.broadcast_targets = hash_slice(&self.state.broadcast_targets, hash_bt);
        self.digests.ids = hash_ids(&self.state.ids);
    }

    fn changed_lines(&mut self) -> Vec<String> {
        let mut lines = Vec::new();
        sync_map(
            &mut lines,
            &mut self.digests.agents,
            &self.state.agents,
            "agents",
            |row| row.agent_id.clone(),
            hash_agent,
            encode_agent,
        );
        sync_slice(
            &mut lines,
            &mut self.digests.lineage,
            &self.state.lineage,
            "lineage",
            hash_lineage,
            encode_lineage,
        );
        sync_slice(
            &mut lines,
            &mut self.digests.descendants,
            &self.state.descendants,
            "descendants",
            hash_desc,
            encode_desc,
        );
        sync_map(
            &mut lines,
            &mut self.digests.locks,
            &self.state.locks,
            "locks",
            |row| row.id.to_string(),
            hash_lock,
            encode_lock,
        );
        sync_map(
            &mut lines,
            &mut self.digests.conflicts,
            &self.state.conflicts,
            "conflicts",
            |row| row.id.to_string(),
            hash_conflict,
            encode_conflict,
        );
        sync_map(
            &mut lines,
            &mut self.digests.notifications,
            &self.state.notifications,
            "notifications",
            |row| row.id.to_string(),
            hash_note,
            encode_note,
        );
        sync_map(
            &mut lines,
            &mut self.digests.responsibilities,
            &self.state.responsibilities,
            "responsibilities",
            |row| row.agent_id.clone(),
            hash_resp,
            encode_resp,
        );
        sync_map(
            &mut lines,
            &mut self.digests.ownership,
            &self.state.ownership,
            "ownership",
            |row| row.document_path.clone(),
            hash_own,
            encode_own,
        );
        sync_map(
            &mut lines,
            &mut self.digests.messages,
            &self.state.messages,
            "messages",
            |row| row.id.to_string(),
            hash_msg,
            encode_msg,
        );
        sync_map(
            &mut lines,
            &mut self.digests.tasks,
            &self.state.tasks,
            "tasks",
            |row| row.id.clone(),
            hash_task,
            encode_task,
        );
        sync_map(
            &mut lines,
            &mut self.digests.intents,
            &self.state.intents,
            "intents",
            |row| format!("{}|{}", row.agent_id, row.document_path),
            hash_intent,
            encode_intent,
        );
        sync_map(
            &mut lines,
            &mut self.digests.handoffs,
            &self.state.handoffs,
            "handoffs",
            |row| row.id.to_string(),
            hash_handoff,
            encode_handoff,
        );
        sync_slice(
            &mut lines,
            &mut self.digests.handoff_acks,
            &self.state.handoff_acks,
            "handoff_acks",
            hash_hack,
            encode_hack,
        );
        sync_map(
            &mut lines,
            &mut self.digests.failures,
            &self.state.failures,
            "failures",
            |row| row.id.to_string(),
            hash_fail,
            encode_fail,
        );
        sync_map(
            &mut lines,
            &mut self.digests.leases,
            &self.state.leases,
            "leases",
            |row| row.lease_name.clone(),
            hash_lease,
            encode_lease,
        );
        sync_map(
            &mut lines,
            &mut self.digests.dependencies,
            &self.state.dependencies,
            "dependencies",
            |row| row.id.to_string(),
            hash_dep,
            encode_dep,
        );
        sync_map(
            &mut lines,
            &mut self.digests.spawns,
            &self.state.spawns,
            "spawns",
            |row| row.id.clone(),
            hash_spawn,
            encode_spawn,
        );
        sync_map(
            &mut lines,
            &mut self.digests.broadcasts,
            &self.state.broadcasts,
            "broadcasts",
            |row| row.id.to_string(),
            hash_bc,
            encode_bc,
        );
        sync_slice(
            &mut lines,
            &mut self.digests.broadcast_acks,
            &self.state.broadcast_acks,
            "broadcast_acks",
            hash_back,
            encode_back,
        );
        sync_slice(
            &mut lines,
            &mut self.digests.broadcast_targets,
            &self.state.broadcast_targets,
            "broadcast_targets",
            hash_bt,
            encode_bt,
        );
        sync_map(
            &mut lines,
            &mut self.digests.claims,
            &self.state.claims,
            "claims",
            |row| row.id.to_string(),
            hash_claim,
            encode_claim,
        );
        sync_map(
            &mut lines,
            &mut self.digests.claim_events,
            &self.state.claim_events,
            "claim_events",
            |row| row.id.to_string(),
            hash_ce,
            encode_ce,
        );
        let ids = hash_ids(&self.state.ids);
        if ids != self.digests.ids {
            lines.push(format!(
                "{{\"t\":\"ids\",\"u\":{}}}",
                json::dumps(&encode_ids(&self.state.ids))
            ));
            self.digests.ids = ids;
        }
        lines
    }
}

fn digest_map<T>(
    rows: &[T],
    key: impl Fn(&T) -> String,
    hash: impl Fn(&T) -> u64,
) -> std::collections::HashMap<String, u64> {
    rows.iter().map(|row| (key(row), hash(row))).collect()
}

fn hash_slice<T>(rows: &[T], hash: impl Fn(&T) -> u64) -> u64 {
    let mut out = rows.len() as u64;
    for row in rows {
        out = out.wrapping_mul(131).wrapping_add(hash(row));
    }
    out
}

fn sync_map<T>(
    lines: &mut Vec<String>,
    digests: &mut std::collections::HashMap<String, u64>,
    rows: &[T],
    table: &str,
    key: impl Fn(&T) -> String,
    hash: impl Fn(&T) -> u64,
    encode: impl Fn(&T) -> Value,
) {
    let mut live = std::collections::HashSet::new();
    for row in rows {
        let id = key(row);
        let digest = hash(row);
        live.insert(id.clone());
        if digests.get(&id) != Some(&digest) {
            lines.push(format!(
                "{{\"t\":\"put\",\"table\":\"{table}\",\"row\":{}}}",
                json::dumps(&encode(row))
            ));
            digests.insert(id, digest);
        }
    }
    let stale: Vec<String> = digests
        .keys()
        .filter(|id| !live.contains(*id))
        .cloned()
        .collect();
    for id in stale {
        lines.push(format!(
            "{{\"t\":\"del\",\"table\":\"{table}\",\"id\":{}}}",
            json::dumps(&json::s(&id))
        ));
        digests.remove(&id);
    }
}

fn sync_slice<T: PartialEq>(
    lines: &mut Vec<String>,
    digest: &mut u64,
    rows: &[T],
    table: &str,
    hash: impl Fn(&T) -> u64,
    encode: impl Fn(&T) -> Value,
) {
    let next = hash_slice(rows, hash);
    if next == *digest {
        return;
    }
    lines.push(format!(
        "{{\"t\":\"del\",\"table\":\"{table}\",\"id\":\"*\"}}"
    ));
    for row in rows {
        lines.push(format!(
            "{{\"t\":\"put\",\"table\":\"{table}\",\"row\":{}}}",
            json::dumps(&encode(row))
        ));
    }
    *digest = next;
}

fn mix(parts: &[u64]) -> u64 {
    let mut out = 0xcbf29ce484222325_u64;
    for part in parts {
        out ^= part;
        out = out.wrapping_mul(0x100000001b3);
    }
    out
}

fn hash_str(text: &str) -> u64 {
    let mut out = 0xcbf29ce484222325_u64;
    for byte in text.as_bytes() {
        out ^= *byte as u64;
        out = out.wrapping_mul(0x100000001b3);
    }
    out
}

fn hash_opt(text: &Option<String>) -> u64 {
    text.as_deref().map(hash_str).unwrap_or(0)
}

fn hash_f(value: f64) -> u64 {
    value.to_bits()
}

fn hash_agent(row: &Agent) -> u64 {
    mix(&[
        hash_str(&row.agent_id),
        hash_opt(&row.parent_id),
        hash_str(&row.worktree_root),
        row.pid as u64,
        hash_f(row.started_at),
        hash_f(row.last_heartbeat),
        hash_str(&row.status),
        hash_opt(&row.raw_ide_id),
        hash_opt(&row.ide_vendor),
        row.stop_requested_at.map(hash_f).unwrap_or(0),
    ])
}
fn hash_lock(row: &Lock) -> u64 {
    mix(&[
        row.id as u64,
        hash_str(&row.document_path),
        hash_str(&row.locked_by),
        hash_f(row.locked_at),
        hash_f(row.lock_ttl),
        hash_str(&row.lock_type),
        row.region_start.unwrap_or(0) as u64,
        row.region_end.unwrap_or(0) as u64,
        hash_opt(&row.worktree_root),
    ])
}
fn hash_conflict(row: &Conflict) -> u64 {
    mix(&[
        row.id as u64,
        hash_str(&row.document_path),
        hash_str(&row.agent_a),
        hash_str(&row.agent_b),
        hash_str(&row.conflict_type),
        hash_str(&row.resolution),
        hash_opt(&row.details_json),
        hash_f(row.created_at),
    ])
}
fn hash_note(row: &Notification) -> u64 {
    mix(&[
        row.id as u64,
        hash_str(&row.document_path),
        hash_str(&row.change_type),
        hash_str(&row.agent_id),
        hash_opt(&row.worktree_root),
        hash_f(row.created_at),
    ])
}
fn hash_resp(row: &Responsibility) -> u64 {
    mix(&[
        hash_str(&row.agent_id),
        hash_opt(&row.graph_agent_id),
        hash_opt(&row.role),
        hash_opt(&row.model),
        hash_opt(&row.responsibilities),
        hash_opt(&row.current_task),
        hash_opt(&row.scope),
        hash_f(row.updated_at),
    ])
}
fn hash_own(row: &Ownership) -> u64 {
    mix(&[
        hash_str(&row.document_path),
        hash_str(&row.assigned_agent_id),
        hash_f(row.assigned_at),
        hash_opt(&row.last_claimed_by),
        hash_opt(&row.task_description),
    ])
}
fn hash_msg(row: &Message) -> u64 {
    mix(&[
        row.id as u64,
        hash_str(&row.from_agent_id),
        hash_str(&row.to_agent_id),
        hash_str(&row.message_type),
        hash_opt(&row.payload_json),
        hash_f(row.created_at),
        row.read_at.map(hash_f).unwrap_or(0),
    ])
}
fn hash_task(row: &Task) -> u64 {
    mix(&[
        hash_str(&row.id),
        hash_str(&row.parent_agent_id),
        hash_opt(&row.parent_task_id),
        hash_opt(&row.assigned_agent_id),
        hash_str(&row.description),
        hash_str(&row.status),
        hash_f(row.created_at),
        hash_f(row.updated_at),
        hash_str(&row.depends_on),
        hash_opt(&row.blocked_by),
        hash_opt(&row.summary),
        row.priority as u64,
        hash_opt(&row.error),
    ])
}
fn hash_intent(row: &Intent) -> u64 {
    mix(&[
        hash_str(&row.agent_id),
        hash_str(&row.document_path),
        hash_str(&row.intent),
        hash_f(row.declared_at),
        hash_f(row.ttl),
    ])
}
fn hash_handoff(row: &Handoff) -> u64 {
    mix(&[
        row.id as u64,
        hash_str(&row.from_agent_id),
        hash_str(&row.to_agents),
        hash_opt(&row.document_path),
        hash_str(&row.handoff_type),
        hash_str(&row.status),
        hash_f(row.created_at),
        row.acknowledged_at.map(hash_f).unwrap_or(0),
        row.completed_at.map(hash_f).unwrap_or(0),
    ])
}
fn hash_fail(row: &Failure) -> u64 {
    mix(&[
        row.id as u64,
        hash_str(&row.task_id),
        hash_opt(&row.error),
        row.attempt as u64,
        row.max_retries as u64,
        hash_f(row.first_attempt_at),
        hash_f(row.last_attempt_at),
        row.dead_letter_at.map(hash_f).unwrap_or(0),
        hash_str(&row.status),
    ])
}
fn hash_lease(row: &Lease) -> u64 {
    mix(&[
        hash_str(&row.lease_name),
        hash_str(&row.holder_id),
        hash_f(row.acquired_at),
        hash_f(row.ttl),
        hash_f(row.expires_at),
    ])
}
fn hash_dep(row: &Dependency) -> u64 {
    mix(&[
        row.id as u64,
        hash_str(&row.dependent_agent_id),
        hash_str(&row.depends_on_agent_id),
        hash_opt(&row.depends_on_task_id),
        hash_str(&row.condition),
        row.satisfied as u64,
        row.satisfied_at.map(hash_f).unwrap_or(0),
        hash_f(row.created_at),
    ])
}
fn hash_spawn(row: &Spawn) -> u64 {
    mix(&[
        hash_str(&row.id),
        hash_str(&row.parent_agent_id),
        hash_opt(&row.subagent_type),
        hash_opt(&row.description),
        hash_opt(&row.prompt),
        hash_f(row.created_at),
        row.consumed_at.map(hash_f).unwrap_or(0),
        hash_str(&row.status),
        hash_str(&row.source),
        hash_opt(&row.child_agent_id),
        row.stop_requested as u64,
    ])
}
fn hash_bc(row: &Broadcast) -> u64 {
    mix(&[
        row.id as u64,
        hash_str(&row.from_agent_id),
        hash_opt(&row.document_path),
        hash_opt(&row.message),
        hash_f(row.created_at),
        hash_f(row.ttl),
        hash_f(row.expires_at),
        row.expected_count as u64,
    ])
}
fn hash_claim(row: &Claim) -> u64 {
    mix(&[
        row.id as u64,
        hash_str(&row.agent_id),
        hash_str(&row.area),
        hash_f(row.claimed_at),
        hash_f(row.ttl),
        hash_f(row.expires_at),
        hash_opt(&row.note),
    ])
}
fn hash_ce(row: &ClaimEvent) -> u64 {
    mix(&[
        row.id as u64,
        hash_str(&row.area),
        hash_opt(&row.from_agent_id),
        hash_opt(&row.to_agent_id),
        hash_str(&row.action),
        hash_f(row.at),
    ])
}
fn hash_lineage(row: &Lineage) -> u64 {
    mix(&[
        hash_str(&row.parent_id),
        hash_str(&row.child_id),
        hash_f(row.spawned_at),
    ])
}
fn hash_desc(row: &Descendant) -> u64 {
    mix(&[
        hash_str(&row.ancestor_id),
        hash_str(&row.descendant_id),
        row.depth as u64,
        hash_f(row.registered_at),
    ])
}
fn hash_hack(row: &HandoffAck) -> u64 {
    mix(&[
        row.handoff_id as u64,
        hash_str(&row.agent_id),
        hash_f(row.acknowledged_at),
    ])
}
fn hash_back(row: &BroadcastAck) -> u64 {
    mix(&[
        row.broadcast_id as u64,
        hash_str(&row.agent_id),
        hash_f(row.acknowledged_at),
    ])
}
fn hash_bt(row: &BroadcastTarget) -> u64 {
    mix(&[row.broadcast_id as u64, hash_str(&row.agent_id)])
}
fn hash_ids(ids: &Ids) -> u64 {
    mix(&[
        ids.locks as u64,
        ids.conflicts as u64,
        ids.notifications as u64,
        ids.messages as u64,
        ids.handoffs as u64,
        ids.failures as u64,
        ids.dependencies as u64,
        ids.broadcasts as u64,
        ids.claims as u64,
        ids.claim_events as u64,
    ])
}

fn load_journal(path: &Path) -> Result<(State, u64), String> {
    if !path.is_file() {
        std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)
            .map_err(|e| e.to_string())?;
        return Ok((State::default(), 0));
    }
    let state = replay(path)?;
    let offset = std::fs::metadata(path).map_err(|e| e.to_string())?.len();
    Ok((state, offset))
}

fn replay(path: &Path) -> Result<State, String> {
    let text = std::fs::read_to_string(path).map_err(|e| e.to_string())?;
    let mut state = State::default();
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        apply_line(&mut state, &json::parse(line)?)?;
    }
    Ok(state)
}

fn apply_line(state: &mut State, value: &Value) -> Result<(), String> {
    let table = value.get("table").and_then(Value::as_str).unwrap_or("");
    let kind = value.get("t").and_then(Value::as_str).unwrap_or("");
    if kind == "ids" {
        if let Some(ids) = value.get("u") {
            state.ids = Ids {
                locks: num_i(ids, "locks"),
                conflicts: num_i(ids, "conflicts"),
                notifications: num_i(ids, "notifications"),
                messages: num_i(ids, "messages"),
                handoffs: num_i(ids, "handoffs"),
                failures: num_i(ids, "failures"),
                dependencies: num_i(ids, "dependencies"),
                broadcasts: num_i(ids, "broadcasts"),
                claims: num_i(ids, "claims"),
                claim_events: num_i(ids, "claim_events"),
            };
        }
        return Ok(());
    }
    if kind == "put" {
        let row = value.get("row").ok_or("missing row")?;
        match table {
            "agents" => upsert(
                &mut state.agents,
                decode_agent(row)?,
                |item| item.agent_id.clone(),
                |item| item.agent_id.clone(),
            ),
            "lineage" => state.lineage.push(decode_lineage(row)?),
            "descendants" => state.descendants.push(decode_desc(row)?),
            "locks" => upsert(
                &mut state.locks,
                decode_lock(row)?,
                |item| item.id.to_string(),
                |item| item.id.to_string(),
            ),
            "conflicts" => upsert(
                &mut state.conflicts,
                decode_conflict(row)?,
                |item| item.id.to_string(),
                |item| item.id.to_string(),
            ),
            "notifications" => upsert(
                &mut state.notifications,
                decode_note(row)?,
                |item| item.id.to_string(),
                |item| item.id.to_string(),
            ),
            "responsibilities" => upsert(
                &mut state.responsibilities,
                decode_resp(row)?,
                |item| item.agent_id.clone(),
                |item| item.agent_id.clone(),
            ),
            "ownership" => upsert(
                &mut state.ownership,
                decode_own(row)?,
                |item| item.document_path.clone(),
                |item| item.document_path.clone(),
            ),
            "messages" => upsert(
                &mut state.messages,
                decode_msg(row)?,
                |item| item.id.to_string(),
                |item| item.id.to_string(),
            ),
            "tasks" => upsert(
                &mut state.tasks,
                decode_task(row)?,
                |item| item.id.clone(),
                |item| item.id.clone(),
            ),
            "intents" => upsert(
                &mut state.intents,
                decode_intent(row)?,
                |item| format!("{}|{}", item.agent_id, item.document_path),
                |item| format!("{}|{}", item.agent_id, item.document_path),
            ),
            "handoffs" => upsert(
                &mut state.handoffs,
                decode_handoff(row)?,
                |item| item.id.to_string(),
                |item| item.id.to_string(),
            ),
            "handoff_acks" => state.handoff_acks.push(decode_hack(row)?),
            "failures" => upsert(
                &mut state.failures,
                decode_fail(row)?,
                |item| item.id.to_string(),
                |item| item.id.to_string(),
            ),
            "leases" => upsert(
                &mut state.leases,
                decode_lease(row)?,
                |item| item.lease_name.clone(),
                |item| item.lease_name.clone(),
            ),
            "dependencies" => upsert(
                &mut state.dependencies,
                decode_dep(row)?,
                |item| item.id.to_string(),
                |item| item.id.to_string(),
            ),
            "spawns" => upsert(
                &mut state.spawns,
                decode_spawn(row)?,
                |item| item.id.clone(),
                |item| item.id.clone(),
            ),
            "broadcasts" => upsert(
                &mut state.broadcasts,
                decode_bc(row)?,
                |item| item.id.to_string(),
                |item| item.id.to_string(),
            ),
            "broadcast_acks" => state.broadcast_acks.push(decode_back(row)?),
            "broadcast_targets" => state.broadcast_targets.push(decode_bt(row)?),
            "claims" => upsert(
                &mut state.claims,
                decode_claim(row)?,
                |item| item.id.to_string(),
                |item| item.id.to_string(),
            ),
            "claim_events" => upsert(
                &mut state.claim_events,
                decode_ce(row)?,
                |item| item.id.to_string(),
                |item| item.id.to_string(),
            ),
            other => return Err(format!("unknown table {other}")),
        }
        return Ok(());
    }
    if kind == "del" {
        let id = value
            .get("id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        match table {
            "agents" => state.agents.retain(|row| row.agent_id != id),
            "locks" => state.locks.retain(|row| row.id.to_string() != id),
            "conflicts" => state.conflicts.retain(|row| row.id.to_string() != id),
            "notifications" => state.notifications.retain(|row| row.id.to_string() != id),
            "messages" => state.messages.retain(|row| row.id.to_string() != id),
            "tasks" => state.tasks.retain(|row| row.id != id),
            "handoffs" => state.handoffs.retain(|row| row.id.to_string() != id),
            "failures" => state.failures.retain(|row| row.id.to_string() != id),
            "leases" => state.leases.retain(|row| row.lease_name != id),
            "dependencies" => state.dependencies.retain(|row| row.id.to_string() != id),
            "spawns" => state.spawns.retain(|row| row.id != id),
            "broadcasts" => state.broadcasts.retain(|row| row.id.to_string() != id),
            "claims" => state.claims.retain(|row| row.id.to_string() != id),
            "ownership" => state.ownership.retain(|row| row.document_path != id),
            "responsibilities" => state.responsibilities.retain(|row| row.agent_id != id),
            "intents" => state
                .intents
                .retain(|row| format!("{}|{}", row.agent_id, row.document_path) != id),
            "lineage" => state.lineage.clear(),
            "descendants" => state.descendants.clear(),
            "handoff_acks" => state.handoff_acks.clear(),
            "broadcast_acks" => state.broadcast_acks.clear(),
            "broadcast_targets" => state.broadcast_targets.clear(),
            "claim_events" => state.claim_events.retain(|row| row.id.to_string() != id),
            _ => {}
        }
        return Ok(());
    }
    Err(format!("unknown journal record {kind}"))
}

fn upsert<T>(
    rows: &mut Vec<T>,
    row: T,
    new_key: impl Fn(&T) -> String,
    old_key: impl Fn(&T) -> String,
) {
    let key = new_key(&row);
    if let Some(pos) = rows.iter().position(|item| old_key(item) == key) {
        rows[pos] = row;
    } else {
        rows.push(row);
    }
}

fn encode_ids(ids: &Ids) -> Value {
    json::obj(vec![
        ("locks", json::i(ids.locks)),
        ("conflicts", json::i(ids.conflicts)),
        ("notifications", json::i(ids.notifications)),
        ("messages", json::i(ids.messages)),
        ("handoffs", json::i(ids.handoffs)),
        ("failures", json::i(ids.failures)),
        ("dependencies", json::i(ids.dependencies)),
        ("broadcasts", json::i(ids.broadcasts)),
        ("claims", json::i(ids.claims)),
        ("claim_events", json::i(ids.claim_events)),
    ])
}

fn encode_agent(row: &Agent) -> Value {
    json::obj(vec![
        ("agent_id", json::s(&row.agent_id)),
        ("parent_id", json::opt_string(&row.parent_id)),
        ("worktree_root", json::s(&row.worktree_root)),
        ("pid", json::i(row.pid)),
        ("started_at", json::f(row.started_at)),
        ("last_heartbeat", json::f(row.last_heartbeat)),
        ("status", json::s(&row.status)),
        ("raw_ide_id", json::opt_string(&row.raw_ide_id)),
        ("ide_vendor", json::opt_string(&row.ide_vendor)),
        ("stop_requested_at", opt_f(row.stop_requested_at)),
    ])
}

fn encode_lineage(row: &Lineage) -> Value {
    json::obj(vec![
        ("parent_id", json::s(&row.parent_id)),
        ("child_id", json::s(&row.child_id)),
        ("spawned_at", json::f(row.spawned_at)),
    ])
}

fn encode_desc(row: &Descendant) -> Value {
    json::obj(vec![
        ("ancestor_id", json::s(&row.ancestor_id)),
        ("descendant_id", json::s(&row.descendant_id)),
        ("depth", json::i(row.depth)),
        ("registered_at", json::f(row.registered_at)),
    ])
}

fn encode_lock(row: &Lock) -> Value {
    json::obj(vec![
        ("id", json::i(row.id)),
        ("document_path", json::s(&row.document_path)),
        ("locked_by", json::s(&row.locked_by)),
        ("locked_at", json::f(row.locked_at)),
        ("lock_ttl", json::f(row.lock_ttl)),
        ("lock_type", json::s(&row.lock_type)),
        ("region_start", json::opt_i(row.region_start)),
        ("region_end", json::opt_i(row.region_end)),
        ("worktree_root", json::opt_string(&row.worktree_root)),
    ])
}

fn encode_conflict(row: &Conflict) -> Value {
    json::obj(vec![
        ("id", json::i(row.id)),
        ("document_path", json::s(&row.document_path)),
        ("agent_a", json::s(&row.agent_a)),
        ("agent_b", json::s(&row.agent_b)),
        ("conflict_type", json::s(&row.conflict_type)),
        ("resolution", json::s(&row.resolution)),
        ("details_json", json::opt_string(&row.details_json)),
        ("created_at", json::f(row.created_at)),
    ])
}

fn encode_note(row: &Notification) -> Value {
    json::obj(vec![
        ("id", json::i(row.id)),
        ("document_path", json::s(&row.document_path)),
        ("change_type", json::s(&row.change_type)),
        ("agent_id", json::s(&row.agent_id)),
        ("worktree_root", json::opt_string(&row.worktree_root)),
        ("created_at", json::f(row.created_at)),
    ])
}

fn encode_resp(row: &Responsibility) -> Value {
    json::obj(vec![
        ("agent_id", json::s(&row.agent_id)),
        ("graph_agent_id", json::opt_string(&row.graph_agent_id)),
        ("role", json::opt_string(&row.role)),
        ("model", json::opt_string(&row.model)),
        ("responsibilities", json::opt_string(&row.responsibilities)),
        ("current_task", json::opt_string(&row.current_task)),
        ("scope", json::opt_string(&row.scope)),
        ("updated_at", json::f(row.updated_at)),
    ])
}

fn encode_own(row: &Ownership) -> Value {
    json::obj(vec![
        ("document_path", json::s(&row.document_path)),
        ("assigned_agent_id", json::s(&row.assigned_agent_id)),
        ("assigned_at", json::f(row.assigned_at)),
        ("last_claimed_by", json::opt_string(&row.last_claimed_by)),
        ("task_description", json::opt_string(&row.task_description)),
    ])
}

fn encode_msg(row: &Message) -> Value {
    json::obj(vec![
        ("id", json::i(row.id)),
        ("from_agent_id", json::s(&row.from_agent_id)),
        ("to_agent_id", json::s(&row.to_agent_id)),
        ("message_type", json::s(&row.message_type)),
        ("payload_json", json::opt_string(&row.payload_json)),
        ("created_at", json::f(row.created_at)),
        (
            "read_at",
            match row.read_at {
                Some(n) => json::f(n),
                None => json::null(),
            },
        ),
    ])
}

fn encode_task(row: &Task) -> Value {
    json::obj(vec![
        ("id", json::s(&row.id)),
        ("parent_agent_id", json::s(&row.parent_agent_id)),
        ("parent_task_id", json::opt_string(&row.parent_task_id)),
        (
            "assigned_agent_id",
            json::opt_string(&row.assigned_agent_id),
        ),
        ("description", json::s(&row.description)),
        ("status", json::s(&row.status)),
        ("created_at", json::f(row.created_at)),
        ("updated_at", json::f(row.updated_at)),
        ("depends_on", json::s(&row.depends_on)),
        ("blocked_by", json::opt_string(&row.blocked_by)),
        ("summary", json::opt_string(&row.summary)),
        ("priority", json::i(row.priority)),
        ("error", json::opt_string(&row.error)),
    ])
}

fn encode_intent(row: &Intent) -> Value {
    json::obj(vec![
        ("agent_id", json::s(&row.agent_id)),
        ("document_path", json::s(&row.document_path)),
        ("intent", json::s(&row.intent)),
        ("declared_at", json::f(row.declared_at)),
        ("ttl", json::f(row.ttl)),
    ])
}

fn encode_handoff(row: &Handoff) -> Value {
    json::obj(vec![
        ("id", json::i(row.id)),
        ("from_agent_id", json::s(&row.from_agent_id)),
        ("to_agents", json::s(&row.to_agents)),
        ("document_path", json::opt_string(&row.document_path)),
        ("handoff_type", json::s(&row.handoff_type)),
        ("status", json::s(&row.status)),
        ("created_at", json::f(row.created_at)),
        ("acknowledged_at", opt_f(row.acknowledged_at)),
        ("completed_at", opt_f(row.completed_at)),
    ])
}

fn encode_hack(row: &HandoffAck) -> Value {
    json::obj(vec![
        ("handoff_id", json::i(row.handoff_id)),
        ("agent_id", json::s(&row.agent_id)),
        ("acknowledged_at", json::f(row.acknowledged_at)),
    ])
}

fn encode_fail(row: &Failure) -> Value {
    json::obj(vec![
        ("id", json::i(row.id)),
        ("task_id", json::s(&row.task_id)),
        ("error", json::opt_string(&row.error)),
        ("attempt", json::i(row.attempt)),
        ("max_retries", json::i(row.max_retries)),
        ("first_attempt_at", json::f(row.first_attempt_at)),
        ("last_attempt_at", json::f(row.last_attempt_at)),
        ("dead_letter_at", opt_f(row.dead_letter_at)),
        ("status", json::s(&row.status)),
    ])
}

fn encode_lease(row: &Lease) -> Value {
    json::obj(vec![
        ("lease_name", json::s(&row.lease_name)),
        ("holder_id", json::s(&row.holder_id)),
        ("acquired_at", json::f(row.acquired_at)),
        ("ttl", json::f(row.ttl)),
        ("expires_at", json::f(row.expires_at)),
    ])
}

fn encode_dep(row: &Dependency) -> Value {
    json::obj(vec![
        ("id", json::i(row.id)),
        ("dependent_agent_id", json::s(&row.dependent_agent_id)),
        ("depends_on_agent_id", json::s(&row.depends_on_agent_id)),
        (
            "depends_on_task_id",
            json::opt_string(&row.depends_on_task_id),
        ),
        ("condition", json::s(&row.condition)),
        ("satisfied", json::i(row.satisfied)),
        ("satisfied_at", opt_f(row.satisfied_at)),
        ("created_at", json::f(row.created_at)),
    ])
}

fn encode_spawn(row: &Spawn) -> Value {
    json::obj(vec![
        ("id", json::s(&row.id)),
        ("parent_agent_id", json::s(&row.parent_agent_id)),
        ("subagent_type", json::opt_string(&row.subagent_type)),
        ("description", json::opt_string(&row.description)),
        ("prompt", json::opt_string(&row.prompt)),
        ("created_at", json::f(row.created_at)),
        ("consumed_at", opt_f(row.consumed_at)),
        ("status", json::s(&row.status)),
        ("source", json::s(&row.source)),
        ("child_agent_id", json::opt_string(&row.child_agent_id)),
        ("stop_requested", json::b(row.stop_requested)),
    ])
}

fn encode_bc(row: &Broadcast) -> Value {
    json::obj(vec![
        ("id", json::i(row.id)),
        ("from_agent_id", json::s(&row.from_agent_id)),
        ("document_path", json::opt_string(&row.document_path)),
        ("message", json::opt_string(&row.message)),
        ("created_at", json::f(row.created_at)),
        ("ttl", json::f(row.ttl)),
        ("expires_at", json::f(row.expires_at)),
        ("expected_count", json::i(row.expected_count)),
    ])
}

fn encode_back(row: &BroadcastAck) -> Value {
    json::obj(vec![
        ("broadcast_id", json::i(row.broadcast_id)),
        ("agent_id", json::s(&row.agent_id)),
        ("acknowledged_at", json::f(row.acknowledged_at)),
    ])
}

fn encode_bt(row: &BroadcastTarget) -> Value {
    json::obj(vec![
        ("broadcast_id", json::i(row.broadcast_id)),
        ("agent_id", json::s(&row.agent_id)),
    ])
}

fn encode_claim(row: &Claim) -> Value {
    json::obj(vec![
        ("id", json::i(row.id)),
        ("agent_id", json::s(&row.agent_id)),
        ("area", json::s(&row.area)),
        ("claimed_at", json::f(row.claimed_at)),
        ("ttl", json::f(row.ttl)),
        ("expires_at", json::f(row.expires_at)),
        ("note", json::opt_string(&row.note)),
    ])
}

fn encode_ce(row: &ClaimEvent) -> Value {
    json::obj(vec![
        ("id", json::i(row.id)),
        ("area", json::s(&row.area)),
        ("from_agent_id", json::opt_string(&row.from_agent_id)),
        ("to_agent_id", json::opt_string(&row.to_agent_id)),
        ("action", json::s(&row.action)),
        ("at", json::f(row.at)),
    ])
}

fn opt_f(value: Option<f64>) -> Value {
    match value {
        Some(n) => json::f(n),
        None => json::null(),
    }
}

fn req_str(value: &Value, key: &str) -> Result<String, String> {
    value
        .get(key)
        .and_then(Value::as_str)
        .map(|s| s.to_string())
        .ok_or_else(|| format!("missing {key}"))
}
fn opt_str(value: &Value, key: &str) -> Option<String> {
    value
        .get(key)
        .and_then(Value::as_str)
        .map(|s| s.to_string())
}
fn num_f(value: &Value, key: &str) -> f64 {
    value.get(key).and_then(Value::as_f64).unwrap_or(0.0)
}
fn num_i(value: &Value, key: &str) -> i64 {
    value.get(key).and_then(Value::as_i64).unwrap_or(0)
}
fn opt_num(value: &Value, key: &str) -> Option<f64> {
    value.get(key).and_then(Value::as_f64)
}
fn opt_num_i(value: &Value, key: &str) -> Option<i64> {
    match value.get(key) {
        Some(Value::Null) | None => None,
        Some(other) => other.as_i64(),
    }
}

fn decode_agent(v: &Value) -> Result<Agent, String> {
    Ok(Agent {
        agent_id: req_str(v, "agent_id")?,
        parent_id: opt_str(v, "parent_id"),
        worktree_root: req_str(v, "worktree_root")?,
        pid: num_i(v, "pid"),
        started_at: num_f(v, "started_at"),
        last_heartbeat: num_f(v, "last_heartbeat"),
        status: req_str(v, "status").unwrap_or_else(|_| "active".into()),
        raw_ide_id: opt_str(v, "raw_ide_id"),
        ide_vendor: opt_str(v, "ide_vendor"),
        stop_requested_at: opt_num(v, "stop_requested_at"),
    })
}
fn decode_lineage(v: &Value) -> Result<Lineage, String> {
    Ok(Lineage {
        parent_id: req_str(v, "parent_id")?,
        child_id: req_str(v, "child_id")?,
        spawned_at: num_f(v, "spawned_at"),
    })
}
fn decode_desc(v: &Value) -> Result<Descendant, String> {
    Ok(Descendant {
        ancestor_id: req_str(v, "ancestor_id")?,
        descendant_id: req_str(v, "descendant_id")?,
        depth: num_i(v, "depth"),
        registered_at: num_f(v, "registered_at"),
    })
}
fn decode_lock(v: &Value) -> Result<Lock, String> {
    Ok(Lock {
        id: num_i(v, "id"),
        document_path: req_str(v, "document_path")?,
        locked_by: req_str(v, "locked_by")?,
        locked_at: num_f(v, "locked_at"),
        lock_ttl: num_f(v, "lock_ttl"),
        lock_type: req_str(v, "lock_type").unwrap_or_else(|_| "exclusive".into()),
        region_start: opt_num_i(v, "region_start"),
        region_end: opt_num_i(v, "region_end"),
        worktree_root: opt_str(v, "worktree_root"),
    })
}
fn decode_conflict(v: &Value) -> Result<Conflict, String> {
    Ok(Conflict {
        id: num_i(v, "id"),
        document_path: req_str(v, "document_path")?,
        agent_a: req_str(v, "agent_a")?,
        agent_b: req_str(v, "agent_b")?,
        conflict_type: req_str(v, "conflict_type")?,
        resolution: req_str(v, "resolution").unwrap_or_else(|_| "rejected".into()),
        details_json: opt_str(v, "details_json"),
        created_at: num_f(v, "created_at"),
    })
}
fn decode_note(v: &Value) -> Result<Notification, String> {
    Ok(Notification {
        id: num_i(v, "id"),
        document_path: req_str(v, "document_path")?,
        change_type: req_str(v, "change_type")?,
        agent_id: req_str(v, "agent_id")?,
        worktree_root: opt_str(v, "worktree_root"),
        created_at: num_f(v, "created_at"),
    })
}
fn decode_resp(v: &Value) -> Result<Responsibility, String> {
    Ok(Responsibility {
        agent_id: req_str(v, "agent_id")?,
        graph_agent_id: opt_str(v, "graph_agent_id"),
        role: opt_str(v, "role"),
        model: opt_str(v, "model"),
        responsibilities: opt_str(v, "responsibilities"),
        current_task: opt_str(v, "current_task"),
        scope: opt_str(v, "scope"),
        updated_at: num_f(v, "updated_at"),
    })
}
fn decode_own(v: &Value) -> Result<Ownership, String> {
    Ok(Ownership {
        document_path: req_str(v, "document_path")?,
        assigned_agent_id: req_str(v, "assigned_agent_id")?,
        assigned_at: num_f(v, "assigned_at"),
        last_claimed_by: opt_str(v, "last_claimed_by"),
        task_description: opt_str(v, "task_description"),
    })
}
fn decode_msg(v: &Value) -> Result<Message, String> {
    Ok(Message {
        id: num_i(v, "id"),
        from_agent_id: req_str(v, "from_agent_id")?,
        to_agent_id: req_str(v, "to_agent_id")?,
        message_type: req_str(v, "message_type")?,
        payload_json: opt_str(v, "payload_json"),
        created_at: num_f(v, "created_at"),
        read_at: opt_num(v, "read_at"),
    })
}
fn decode_task(v: &Value) -> Result<Task, String> {
    Ok(Task {
        id: req_str(v, "id")?,
        parent_agent_id: req_str(v, "parent_agent_id")?,
        parent_task_id: opt_str(v, "parent_task_id"),
        assigned_agent_id: opt_str(v, "assigned_agent_id"),
        description: req_str(v, "description")?,
        status: req_str(v, "status").unwrap_or_else(|_| "pending".into()),
        created_at: num_f(v, "created_at"),
        updated_at: num_f(v, "updated_at"),
        depends_on: req_str(v, "depends_on").unwrap_or_else(|_| "[]".into()),
        blocked_by: opt_str(v, "blocked_by"),
        summary: opt_str(v, "summary"),
        priority: num_i(v, "priority"),
        error: opt_str(v, "error"),
    })
}
fn decode_intent(v: &Value) -> Result<Intent, String> {
    Ok(Intent {
        agent_id: req_str(v, "agent_id")?,
        document_path: req_str(v, "document_path")?,
        intent: req_str(v, "intent")?,
        declared_at: num_f(v, "declared_at"),
        ttl: num_f(v, "ttl"),
    })
}
fn decode_handoff(v: &Value) -> Result<Handoff, String> {
    Ok(Handoff {
        id: num_i(v, "id"),
        from_agent_id: req_str(v, "from_agent_id")?,
        to_agents: req_str(v, "to_agents")?,
        document_path: opt_str(v, "document_path"),
        handoff_type: req_str(v, "handoff_type").unwrap_or_else(|_| "scope_transfer".into()),
        status: req_str(v, "status").unwrap_or_else(|_| "pending".into()),
        created_at: num_f(v, "created_at"),
        acknowledged_at: opt_num(v, "acknowledged_at"),
        completed_at: opt_num(v, "completed_at"),
    })
}
fn decode_hack(v: &Value) -> Result<HandoffAck, String> {
    Ok(HandoffAck {
        handoff_id: num_i(v, "handoff_id"),
        agent_id: req_str(v, "agent_id")?,
        acknowledged_at: num_f(v, "acknowledged_at"),
    })
}
fn decode_fail(v: &Value) -> Result<Failure, String> {
    Ok(Failure {
        id: num_i(v, "id"),
        task_id: req_str(v, "task_id")?,
        error: opt_str(v, "error"),
        attempt: num_i(v, "attempt"),
        max_retries: num_i(v, "max_retries"),
        first_attempt_at: num_f(v, "first_attempt_at"),
        last_attempt_at: num_f(v, "last_attempt_at"),
        dead_letter_at: opt_num(v, "dead_letter_at"),
        status: req_str(v, "status").unwrap_or_else(|_| "failed".into()),
    })
}
fn decode_lease(v: &Value) -> Result<Lease, String> {
    Ok(Lease {
        lease_name: req_str(v, "lease_name")?,
        holder_id: req_str(v, "holder_id")?,
        acquired_at: num_f(v, "acquired_at"),
        ttl: num_f(v, "ttl"),
        expires_at: num_f(v, "expires_at"),
    })
}
fn decode_dep(v: &Value) -> Result<Dependency, String> {
    Ok(Dependency {
        id: num_i(v, "id"),
        dependent_agent_id: req_str(v, "dependent_agent_id")?,
        depends_on_agent_id: req_str(v, "depends_on_agent_id")?,
        depends_on_task_id: opt_str(v, "depends_on_task_id"),
        condition: req_str(v, "condition").unwrap_or_else(|_| "task_completed".into()),
        satisfied: num_i(v, "satisfied"),
        satisfied_at: opt_num(v, "satisfied_at"),
        created_at: num_f(v, "created_at"),
    })
}
fn decode_spawn(v: &Value) -> Result<Spawn, String> {
    Ok(Spawn {
        id: req_str(v, "id")?,
        parent_agent_id: req_str(v, "parent_agent_id")?,
        subagent_type: opt_str(v, "subagent_type"),
        description: opt_str(v, "description"),
        prompt: opt_str(v, "prompt"),
        created_at: num_f(v, "created_at"),
        consumed_at: opt_num(v, "consumed_at"),
        status: req_str(v, "status").unwrap_or_else(|_| "pending".into()),
        source: req_str(v, "source").unwrap_or_else(|_| "external".into()),
        child_agent_id: opt_str(v, "child_agent_id"),
        stop_requested: v
            .get("stop_requested")
            .and_then(Value::as_bool)
            .unwrap_or(false),
    })
}
fn decode_bc(v: &Value) -> Result<Broadcast, String> {
    Ok(Broadcast {
        id: num_i(v, "id"),
        from_agent_id: req_str(v, "from_agent_id")?,
        document_path: opt_str(v, "document_path"),
        message: opt_str(v, "message"),
        created_at: num_f(v, "created_at"),
        ttl: num_f(v, "ttl"),
        expires_at: num_f(v, "expires_at"),
        expected_count: num_i(v, "expected_count"),
    })
}
fn decode_back(v: &Value) -> Result<BroadcastAck, String> {
    Ok(BroadcastAck {
        broadcast_id: num_i(v, "broadcast_id"),
        agent_id: req_str(v, "agent_id")?,
        acknowledged_at: num_f(v, "acknowledged_at"),
    })
}
fn decode_bt(v: &Value) -> Result<BroadcastTarget, String> {
    Ok(BroadcastTarget {
        broadcast_id: num_i(v, "broadcast_id"),
        agent_id: req_str(v, "agent_id")?,
    })
}
fn decode_claim(v: &Value) -> Result<Claim, String> {
    Ok(Claim {
        id: num_i(v, "id"),
        agent_id: req_str(v, "agent_id")?,
        area: req_str(v, "area")?,
        claimed_at: num_f(v, "claimed_at"),
        ttl: num_f(v, "ttl"),
        expires_at: num_f(v, "expires_at"),
        note: opt_str(v, "note"),
    })
}
fn decode_ce(v: &Value) -> Result<ClaimEvent, String> {
    Ok(ClaimEvent {
        id: num_i(v, "id"),
        area: req_str(v, "area")?,
        from_agent_id: opt_str(v, "from_agent_id"),
        to_agent_id: opt_str(v, "to_agent_id"),
        action: req_str(v, "action")?,
        at: num_f(v, "at"),
    })
}

pub fn home_storage() -> PathBuf {
    let home = std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .unwrap_or_else(|_| ".".into());
    PathBuf::from(home).join(".coordinationhub")
}

pub fn resolve_storage(storage_dir: Option<&Path>, project_root: Option<&Path>) -> PathBuf {
    if let Some(dir) = storage_dir {
        return util::soft_resolve(dir);
    }
    if let Some(root) = project_root {
        return util::soft_resolve(root).join(".coordinationhub");
    }
    home_storage()
}
