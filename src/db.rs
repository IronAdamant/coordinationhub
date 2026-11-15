//! On-disk journal: `coordination.jsonl`, an append-only log with one JSON
//! record per line (`put` a row, `del` a row, `ids` the id counters).
//!
//! Every call takes the exclusive file lock (`coordination.lock`), reads
//! the records other engines and processes appended since it last looked,
//! applies its change in memory and appends the rows that differ. Nothing
//! is rewritten in place, with two exceptions, both under the same lock:
//!
//! - A final record that a dying process left incomplete is cut off and
//!   kept in a dated `.torn` file (`read_records`), so no append ever
//!   lands on a partial line.
//! - A journal that has grown large against the live rows is replaced by a
//!   snapshot, one record per row, through a synced temporary file and a
//!   rename (`Store::compact`). Each compaction makes the lock file one
//!   byte longer; an engine holding an offset into the old file sees that
//!   count move and reloads, whatever the new file's length. The snapshot's
//!   first line also carries a generation id, checked whenever the journal
//!   is opened.
//!
//! Numbers are stored finite (`json::finite`); a journal 1.2.0 wrote
//! `Infinity` or `NaN` into is read, repaired and rewritten.
//!
//! Durability: a commit is one `write` and, by default, no `fsync`; set
//! `COORDINATIONHUB_FSYNC=1` to sync after each commit.

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
    pub hub_events: i64,
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
    pub hub_events: Vec<HubEvent>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct HubEvent {
    pub id: i64,
    pub topic: String,
    pub payload: String,
    pub created_at: f64,
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
            "hub_events" => &mut self.ids.hub_events,
            _ => panic!("unknown id slot"),
        };
        *cell += 1;
        *cell
    }

    pub fn record_event(&mut self, topic: &str, payload: Value) {
        let id = self.next("hub_events");
        self.hub_events.push(HubEvent {
            id,
            topic: topic.to_string(),
            payload: json::dumps(&payload),
            created_at: crate::util::now(),
        });
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
    /// Bytes of the journal this state has applied; always a line boundary.
    offset: u64,
    lock: Option<FileLock>,
    clean: bool,
    /// How many times the journal has been replaced by a snapshot, as
    /// last seen: the length of the lock file, which every compaction
    /// makes one byte longer. An engine holding an offset into an older
    /// file sees the count move and reloads, whatever the new length.
    epoch: u64,
    /// The id on the journal's first line (empty before the first
    /// compaction). Checked whenever the journal is opened, so a file
    /// replaced by other means (a restore, a hand repair) is noticed too.
    generation: String,
    /// Records in the journal up to `offset`.
    lines: u64,
    /// Journal length from which `commit` next prunes and considers
    /// compaction.
    maintain_at: u64,
    /// The journal held a number 1.2.0 wrote as `Infinity` or `NaN`; the
    /// next commit rewrites the file so it is strict JSON again.
    rewrite_due: bool,
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
    hub_events: std::collections::HashMap<String, u64>,
    lineage_rows: std::collections::HashMap<String, u64>,
    descendant_rows: std::collections::HashMap<String, u64>,
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
            FileLock::exclusive(&dir.join("coordination.lock")).map_err(|e| busy(&dir.join("coordination.lock"), e))?;
        let loaded = load_journal(&path)?;
        let epoch = lock.size();
        drop(lock);
        let mut store = Self {
            path,
            state: loaded.state,
            digests: Digests::default(),
            offset: loaded.offset,
            lock: None,
            clean: true,
            epoch,
            generation: loaded.generation,
            lines: loaded.lines,
            maintain_at: compact_min_bytes().unwrap_or(u64::MAX),
            rewrite_due: loaded.repaired,
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
        let lock = FileLock::exclusive(&lock_path).map_err(|e| busy(&lock_path, e))?;
        self.lock = Some(lock);
        // On a failed read the file lock goes at once: holding it until this
        // process's next call would stall every other process on the store.
        let result = self.catch_up_locked();
        if result.is_err() {
            self.lock = None;
        }
        result
    }

    fn catch_up_locked(&mut self) -> Result<(), String> {
        if self.clean {
            if self.catch_up()? {
                self.remember();
            }
            return Ok(());
        }
        let dirty = !self.changed_lines().is_empty();
        let applied = self.catch_up()?;
        if applied && !dirty {
            self.remember();
        }
        self.clean = !dirty;
        Ok(())
    }

    pub fn end(&mut self) {
        self.lock = None;
    }

    /// A number that differs whenever the journal has changed: its length,
    /// mixed with the generation so a compaction always moves it.
    pub fn event_cursor(&self) -> u64 {
        if self.generation.is_empty() {
            self.offset
        } else {
            self.offset ^ (hash_str(&self.generation) << 44)
        }
    }

    /// Reads what other engines and processes appended since this one last
    /// looked. Runs under the exclusive file lock. True when state changed.
    ///
    /// The journal is opened only when something happened to it. When the
    /// compaction count and the length are what this engine last saw,
    /// nobody wrote, and the call costs two metadata lookups. (Opening a
    /// file that was just written makes a virus scanner read it again:
    /// some 20 ms a call on Windows.)
    fn catch_up(&mut self) -> Result<bool, String> {
        use std::io::{Read, Seek, SeekFrom};
        let epoch = self.lock.as_ref().map(FileLock::size).unwrap_or(self.epoch);
        let len = match std::fs::metadata(&self.path) {
            Ok(meta) => meta.len(),
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => return Ok(false),
            Err(err) => return Err(format!("cannot read {}: {err}", self.path.display())),
        };
        // The journal was replaced by a snapshot when the compaction count
        // moved. Length cannot show that: a compacted file may be longer
        // than the offset an idle engine still holds.
        if epoch != self.epoch || len < self.offset {
            // The count is kept only once the reload worked, so a failed
            // one is tried again on the next call.
            self.reload()?;
            self.epoch = epoch;
            return Ok(true);
        }
        if len == self.offset {
            return Ok(false);
        }
        let mut file = std::fs::File::open(&self.path)
            .map_err(|err| format!("cannot open {}: {err}", self.path.display()))?;
        // The file is open anyway: its first line must still carry the
        // generation this state was read from.
        let mut head = [0_u8; GENERATION_HEAD];
        let mut got = 0;
        while got < head.len() {
            match file.read(&mut head[got..]).map_err(|e| e.to_string())? {
                0 => break,
                n => got += n,
            }
        }
        if generation_of(&head[..got]) != self.generation {
            drop(file);
            return self.reload();
        }
        file.seek(SeekFrom::Start(self.offset))
            .map_err(|e| e.to_string())?;
        let mut buf = Vec::with_capacity((len - self.offset) as usize);
        file.read_to_end(&mut buf).map_err(|e| e.to_string())?;
        drop(file);
        let mut progress = Progress::default();
        let result = read_records(&self.path, &buf, self.offset, self.lines, &mut self.state, &mut progress);
        // Keep what was read before a bad line, so it is not applied twice.
        self.offset += progress.bytes;
        self.lines += progress.lines;
        self.rewrite_due |= progress.repaired;
        result?;
        Ok(progress.bytes > 0)
    }

    /// Drops this engine's state and reads the whole journal again.
    fn reload(&mut self) -> Result<bool, String> {
        let loaded = load_journal(&self.path)?;
        self.state = loaded.state;
        self.offset = loaded.offset;
        self.generation = loaded.generation;
        self.lines = loaded.lines;
        self.rewrite_due |= loaded.repaired;
        Ok(true)
    }

    pub fn commit(&mut self) -> Result<(), String> {
        let held = self.lock.is_some();
        if !held {
            self.begin()?;
        }
        let result = self.commit_locked();
        if !held {
            self.end();
        }
        result
    }

    fn commit_locked(&mut self) -> Result<(), String> {
        // A large journal: drop events past their retention first, so the
        // compaction below (or the plain append) carries the deletions.
        let due = self.offset >= self.maintain_at;
        if due {
            crate::house::prune(&mut self.state, util::now());
        }
        let lines = self.changed_lines();
        if !lines.is_empty() {
            use std::io::Write;
            let mut file = std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(&self.path)
                .map_err(|e| e.to_string())?;
            // One write per commit: fewer system calls, and the call's lines
            // land together. `begin` left the file ending on a line
            // boundary, so the batch never lands on a partial record.
            let mut batch = String::with_capacity(lines.iter().map(|line| line.len() + 1).sum());
            for line in &lines {
                batch.push_str(line);
                batch.push('\n');
            }
            file.write_all(batch.as_bytes()).map_err(|e| e.to_string())?;
            if fsync_enabled() {
                file.sync_data().map_err(|e| e.to_string())?;
            }
            self.offset = file.metadata().map_err(|e| e.to_string())?.len();
            self.lines += lines.len() as u64;
        }
        self.clean = true;
        if self.rewrite_due || (due && self.lines > 2 * (self.rows() + COMPACT_SLACK)) {
            // Best effort: a rename another program blocks leaves the
            // journal as it was, still correct, and the next check retries.
            if let Err(err) = self.compact() {
                eprintln!("coordinationhub: journal not compacted ({err}); it stays as it is and will be retried");
            }
        }
        if due {
            let step = compact_min_bytes().map(|min| (min / 4).max(1)).unwrap_or(u64::MAX);
            self.maintain_at = self.offset.saturating_add(step);
        }
        Ok(())
    }

    /// Live rows across every table: what a snapshot would hold.
    fn rows(&self) -> u64 {
        let s = &self.state;
        (s.agents.len()
            + s.lineage.len()
            + s.descendants.len()
            + s.locks.len()
            + s.conflicts.len()
            + s.notifications.len()
            + s.responsibilities.len()
            + s.ownership.len()
            + s.messages.len()
            + s.tasks.len()
            + s.intents.len()
            + s.handoffs.len()
            + s.handoff_acks.len()
            + s.failures.len()
            + s.leases.len()
            + s.dependencies.len()
            + s.spawns.len()
            + s.broadcasts.len()
            + s.broadcast_acks.len()
            + s.broadcast_targets.len()
            + s.claims.len()
            + s.claim_events.len()
            + s.hub_events.len()) as u64
    }

    /// Journal records and live rows, for tests and diagnostics.
    pub fn journal_shape(&self) -> (u64, u64) {
        (self.lines, self.rows())
    }

    /// Rewrites the journal as one record per live row, under the exclusive
    /// lock the caller holds (`begin` first). The snapshot goes to a
    /// temporary file that is synced and then renamed over the journal, so
    /// a reader sees the old file or the new one, never half of either.
    /// Before the rename the lock file grows by one byte, which is how
    /// every other engine learns the journal was replaced (see `catch_up`);
    /// the snapshot's first line also carries a new generation id.
    pub fn compact(&mut self) -> Result<(), String> {
        use std::io::Write;
        if self.lock.is_none() {
            return Err("compact needs the store lock: call begin first".into());
        }
        let generation = new_generation();
        // With no digests every row counts as changed: the lines are the
        // whole state, and the digests end up matching it again.
        self.digests = Digests::default();
        let lines = self.changed_lines();
        let mut text = String::with_capacity(lines.iter().map(|line| line.len() + 1).sum::<usize>() + 96);
        text.push_str(&generation_line(&generation));
        text.push('\n');
        for line in &lines {
            text.push_str(line);
            text.push('\n');
        }
        let temp = self.path.with_file_name(format!("coordination.jsonl.compact-{}", std::process::id()));
        let lock = self.lock.as_mut();
        let written = (|| -> std::io::Result<()> {
            let mut file = std::fs::File::create(&temp)?;
            file.write_all(text.as_bytes())?;
            file.sync_all()?;
            drop(file);
            // Count first: a count with no rename after it only makes the
            // others reload once; a rename with no count would go unseen.
            if let Some(lock) = lock {
                lock.grow()?;
            }
            replace_file(&temp, &self.path)
        })();
        self.epoch = self.lock.as_ref().map(FileLock::size).unwrap_or(self.epoch);
        if let Err(err) = written {
            let _ = std::fs::remove_file(&temp);
            return Err(format!("cannot replace {}: {err}", self.path.display()));
        }
        sync_dir(&self.path);
        if self.rewrite_due {
            eprintln!(
                "coordinationhub: {} held numbers that are not JSON (Infinity or NaN, which 1.2.0 could write). They were replaced by finite values and the journal was rewritten; no other record changed.",
                self.path.display()
            );
        }
        self.offset = text.len() as u64;
        self.lines = lines.len() as u64 + 1;
        self.generation = generation;
        self.rewrite_due = false;
        self.clean = true;
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
        self.digests.hub_events =
            digest_map(&self.state.hub_events, |row| row.id.to_string(), hash_hub);
        self.digests.lineage_rows = digest_map(
            &self.state.lineage,
            |row| format!("{}\u{1f}{}", row.parent_id, row.child_id),
            hash_lineage,
        );
        self.digests.descendant_rows = digest_map(
            &self.state.descendants,
            |row| format!("{}\u{1f}{}", row.ancestor_id, row.descendant_id),
            hash_desc,
        );
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
        sync_map(
            &mut lines,
            &mut self.digests.lineage_rows,
            &self.state.lineage,
            "lineage",
            |row| format!("{}\u{1f}{}", row.parent_id, row.child_id),
            hash_lineage,
            encode_lineage,
        );
        if self.state.descendants.len() >= self.digests.descendant_rows.len() {
            let start = self.digests.descendant_rows.len();
            for row in &self.state.descendants[start..] {
                let id = format!("{}\u{1f}{}", row.ancestor_id, row.descendant_id);
                lines.push(format!(
                    "{{\"t\":\"put\",\"table\":\"descendants\",\"row\":{}}}",
                    json::dumps(&encode_desc(row))
                ));
                self.digests.descendant_rows.insert(id, hash_desc(row));
            }
        } else {
            sync_map(
                &mut lines,
                &mut self.digests.descendant_rows,
                &self.state.descendants,
                "descendants",
                |row| format!("{}\u{1f}{}", row.ancestor_id, row.descendant_id),
                hash_desc,
                encode_desc,
            );
        }
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
        sync_map(
            &mut lines,
            &mut self.digests.hub_events,
            &self.state.hub_events,
            "hub_events",
            |row| row.id.to_string(),
            hash_hub,
            encode_hub,
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
        ids.hub_events as u64,
    ])
}

/// What a full read of the journal gives.
struct Loaded {
    state: State,
    offset: u64,
    generation: String,
    lines: u64,
    repaired: bool,
}

/// How far `read_records` got: kept up to date line by line, so the
/// caller knows what was applied even when a later line fails.
#[derive(Default)]
struct Progress {
    bytes: u64,
    lines: u64,
    repaired: bool,
}

/// The first line of a compacted journal. It is a `del` on a table no
/// version has, which 1.2.0 and earlier skip, so they still read the file.
const GENERATION_PREFIX: &str = "{\"t\":\"del\",\"table\":\"generation\",\"id\":\"";

/// Bytes of the journal's head that hold the whole generation line.
const GENERATION_HEAD: usize = 128;

/// Compaction is considered only when the journal has this many records
/// more than twice the live rows, so a small store is never rewritten.
const COMPACT_SLACK: u64 = 64;

fn generation_line(id: &str) -> String {
    format!("{GENERATION_PREFIX}{id}\"}}")
}

/// The generation id on the journal's first line, or "" when the journal
/// was never compacted.
fn generation_of(head: &[u8]) -> String {
    let Some(rest) = head.strip_prefix(GENERATION_PREFIX.as_bytes()) else {
        return String::new();
    };
    let end = rest.iter().position(|byte| *byte == b'"').unwrap_or(rest.len());
    String::from_utf8_lossy(&rest[..end]).into_owned()
}

/// A new id: the clock, the process and eight random bytes (a counter when
/// the system has none; uniqueness is all that is needed here).
fn new_generation() -> String {
    static COUNTER: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let tail = crate::rand::hex(8).unwrap_or_else(|_| {
        format!("{:016x}", COUNTER.fetch_add(1, std::sync::atomic::Ordering::SeqCst))
    });
    format!("{nanos:x}-{:x}-{tail}", std::process::id())
}

/// The journal size from which a commit prunes old events and considers
/// compaction: `COORDINATIONHUB_COMPACT_MIN_BYTES`, 1 MiB by default.
/// `None` (the variable set to 0) turns both off.
fn compact_min_bytes() -> Option<u64> {
    let min = std::env::var("COORDINATIONHUB_COMPACT_MIN_BYTES")
        .ok()
        .and_then(|value| value.trim().parse::<u64>().ok())
        .unwrap_or(1 << 20);
    (min > 0).then_some(min)
}

/// `COORDINATIONHUB_FSYNC=1`: `sync_data` after every commit. Off by
/// default; see the README for the trade-off.
fn fsync_enabled() -> bool {
    static FLAG: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
    *FLAG.get_or_init(|| {
        let flag = std::env::var("COORDINATIONHUB_FSYNC").unwrap_or_default();
        matches!(flag.trim().to_ascii_lowercase().as_str(), "1" | "true" | "yes" | "on")
    })
}

/// Makes a rename durable where a directory can be synced (Unix).
fn sync_dir(journal: &Path) {
    #[cfg(unix)]
    if let Some(dir) = journal.parent() {
        if let Ok(handle) = std::fs::File::open(dir) {
            let _ = handle.sync_all();
        }
    }
    #[cfg(not(unix))]
    let _ = journal;
}

/// Renames `from` over `to`. Windows refuses while another program (a
/// virus scanner, an indexer) has `to` open for an instant, so a refusal
/// is retried a few times, about a tenth of a second in all.
fn replace_file(from: &Path, to: &Path) -> std::io::Result<()> {
    let mut wait = std::time::Duration::from_millis(2);
    for _ in 0..6 {
        match std::fs::rename(from, to) {
            Err(err) if err.kind() == std::io::ErrorKind::PermissionDenied => {
                std::thread::sleep(wait);
                wait *= 2;
            }
            other => return other,
        }
    }
    std::fs::rename(from, to)
}

fn load_journal(path: &Path) -> Result<Loaded, String> {
    let mut state = State::default();
    let mut progress = Progress::default();
    if !path.is_file() {
        std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)
            .map_err(|e| e.to_string())?;
        return Ok(Loaded { state, offset: 0, generation: String::new(), lines: 0, repaired: false });
    }
    let buf = std::fs::read(path).map_err(|e| format!("cannot read {}: {e}", path.display()))?;
    let generation = generation_of(&buf[..buf.len().min(GENERATION_HEAD)]);
    read_records(path, &buf, 0, 0, &mut state, &mut progress)?;
    Ok(Loaded {
        state,
        offset: progress.bytes,
        generation,
        lines: progress.lines,
        repaired: progress.repaired,
    })
}

/// Applies the journal records in `buf`, which starts at byte `base` of
/// the journal, after `lines_before` earlier records. The caller holds the
/// exclusive file lock.
///
/// A final record that is cut short or is not JSON is a torn write (a
/// process died mid-append): its bytes are saved beside the journal in a
/// dated `.torn` file, cut off the journal, and reading carries on. A final
/// record that is whole but has no line feed gets one. Either way the file
/// ends on a line boundary afterwards, so no append lands on a partial
/// line. A bad record with records after it is not a torn tail; that is an
/// error naming the line.
fn read_records(
    path: &Path,
    buf: &[u8],
    base: u64,
    lines_before: u64,
    state: &mut State,
    progress: &mut Progress,
) -> Result<(), String> {
    let mut pos = 0;
    while pos < buf.len() {
        let (line, next, terminated) = match buf[pos..].iter().position(|byte| *byte == b'\n') {
            Some(at) => (&buf[pos..pos + at], pos + at + 1, true),
            None => (&buf[pos..], buf.len(), false),
        };
        let number = lines_before + progress.lines + 1;
        if line.iter().all(u8::is_ascii_whitespace) {
            // A blank line (or spaces after the last record) holds nothing.
            progress.lines += u64::from(terminated);
            progress.bytes = next as u64;
            pos = next;
            continue;
        }
        let parsed = std::str::from_utf8(line)
            .map_err(|_| "it is not UTF-8".to_string())
            .and_then(|text| json::parse_journal(text.trim()));
        match parsed {
            Ok((value, repaired)) => {
                apply_line(state, &value).map_err(|err| bad_line(path, number, &err))?;
                progress.repaired |= repaired;
                progress.lines += 1;
                if !terminated {
                    append_line_feed(path)?;
                    progress.bytes = next as u64 + 1;
                    return Ok(());
                }
                progress.bytes = next as u64;
                pos = next;
            }
            Err(err) => {
                let last = buf[next..].iter().all(u8::is_ascii_whitespace);
                if !last {
                    return Err(bad_line(path, number, &err));
                }
                cut_torn_tail(path, base + pos as u64, &buf[pos..])?;
                progress.bytes = pos as u64;
                return Ok(());
            }
        }
    }
    Ok(())
}

fn bad_line(path: &Path, number: u64, err: &str) -> String {
    format!(
        "journal {} line {number} cannot be read: {err}. It is not an incomplete final record (those are cut off and kept in a .torn file), so nothing was changed. To recover: stop every coordinationhub process on this store, move line {number} out of the file (or correct it), then run 'coordinationhub doctor'",
        path.display()
    )
}

fn append_line_feed(path: &Path) -> Result<(), String> {
    use std::io::Write;
    std::fs::OpenOptions::new()
        .append(true)
        .open(path)
        .and_then(|mut file| file.write_all(b"\n"))
        .map_err(|err| format!("cannot end the last record of {} with a line feed: {err}", path.display()))
}

/// Saves `torn` (the journal's bytes from `keep` on) in a dated `.torn`
/// file, then shortens the journal to `keep` bytes. The copy is written
/// and synced first: if it cannot be, the journal is left alone.
fn cut_torn_tail(path: &Path, keep: u64, torn: &[u8]) -> Result<(), String> {
    use std::io::Write;
    let stamp = utc_stamp(util::now());
    let mut saved = path.with_file_name(format!("coordination.jsonl.{stamp}.torn"));
    let mut serial = 1;
    while saved.exists() {
        saved = path.with_file_name(format!("coordination.jsonl.{stamp}-{serial}.torn"));
        serial += 1;
    }
    let fail = |what: &str, err: std::io::Error| {
        format!(
            "the last record of {} is incomplete and {what} failed: {err}. Nothing was changed; make the storage directory writable and retry",
            path.display()
        )
    };
    std::fs::File::create(&saved)
        .and_then(|mut file| {
            file.write_all(torn)?;
            file.sync_all()
        })
        .map_err(|err| fail("saving a copy of it", err))?;
    std::fs::OpenOptions::new()
        .write(true)
        .open(path)
        .and_then(|file| {
            file.set_len(keep)?;
            file.sync_all()
        })
        .map_err(|err| fail("cutting it off", err))?;
    eprintln!(
        "coordinationhub: the last record of {} was incomplete ({} bytes, a write that did not finish). It was cut off and kept in {}; the hub carries on with every record before it.",
        path.display(),
        torn.len(),
        saved.display()
    );
    Ok(())
}

/// The `.torn` files beside the journal in `dir`, oldest first.
pub fn torn_files(dir: &Path) -> Vec<PathBuf> {
    let mut found: Vec<PathBuf> = std::fs::read_dir(dir)
        .map(|entries| {
            entries
                .filter_map(|entry| entry.ok())
                .map(|entry| entry.path())
                .filter(|path| {
                    path.file_name()
                        .and_then(|name| name.to_str())
                        .map(|name| name.starts_with("coordination.jsonl.") && name.ends_with(".torn"))
                        .unwrap_or(false)
                })
                .collect()
        })
        .unwrap_or_default();
    found.sort();
    found
}

/// `doctor`'s line about torn records: none, or where each was kept.
pub fn torn_report(dir: &Path) -> String {
    let found = torn_files(dir);
    if found.is_empty() {
        return "no torn records were cut off the journal".into();
    }
    let names: Vec<String> = found
        .iter()
        .filter_map(|path| path.file_name().map(|name| name.to_string_lossy().into_owned()))
        .collect();
    format!(
        "{} incomplete final record(s) were cut off the journal and kept in {}: {}. The hub carried on; delete a file once you have looked at it",
        found.len(),
        dir.display(),
        names.join(", ")
    )
}

/// `20261115T093000Z` for an epoch time: a file-name-safe UTC stamp.
fn utc_stamp(epoch: f64) -> String {
    let secs = epoch.max(0.0) as i64;
    let (days, rest) = (secs.div_euclid(86_400), secs.rem_euclid(86_400));
    // Civil date from a day count (Howard Hinnant's algorithm).
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    format!(
        "{year:04}{month:02}{day:02}T{:02}{:02}{:02}Z",
        rest / 3600,
        rest % 3600 / 60,
        rest % 60
    )
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
                hub_events: num_i(ids, "hub_events"),
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
            "lineage" => {
                let row = decode_lineage(row)?;
                let key = format!("{}\u{1f}{}", row.parent_id, row.child_id);
                upsert(
                    &mut state.lineage,
                    row,
                    |_| key.clone(),
                    |item| format!("{}\u{1f}{}", item.parent_id, item.child_id),
                );
            }
            "descendants" => {
                let row = decode_desc(row)?;
                upsert(
                    &mut state.descendants,
                    row,
                    |item| format!("{}\u{1f}{}", item.ancestor_id, item.descendant_id),
                    |item| format!("{}\u{1f}{}", item.ancestor_id, item.descendant_id),
                );
            }
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
            "hub_events" => upsert(
                &mut state.hub_events,
                decode_hub(row)?,
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
            "lineage" => {
                if id == "*" {
                    state.lineage.clear();
                } else {
                    state.lineage.retain(|row| {
                        format!("{}\u{1f}{}", row.parent_id, row.child_id) != id
                    });
                }
            }
            "descendants" => {
                if id == "*" {
                    state.descendants.clear();
                } else {
                    state.descendants.retain(|row| {
                        format!("{}\u{1f}{}", row.ancestor_id, row.descendant_id) != id
                    });
                }
            }
            "handoff_acks" => state.handoff_acks.clear(),
            "broadcast_acks" => state.broadcast_acks.clear(),
            "broadcast_targets" => state.broadcast_targets.clear(),
            "claim_events" => state.claim_events.retain(|row| row.id.to_string() != id),
            "hub_events" => state.hub_events.retain(|row| row.id.to_string() != id),
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
        ("hub_events", json::i(ids.hub_events)),
    ])
}

fn encode_agent(row: &Agent) -> Value {
    json::obj(vec![
        ("agent_id", json::s(&row.agent_id)),
        ("parent_id", json::opt_string(&row.parent_id)),
        ("worktree_root", json::s(&row.worktree_root)),
        ("pid", json::i(row.pid)),
        ("started_at", num(row.started_at)),
        ("last_heartbeat", num(row.last_heartbeat)),
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
        ("spawned_at", num(row.spawned_at)),
    ])
}

fn encode_desc(row: &Descendant) -> Value {
    json::obj(vec![
        ("ancestor_id", json::s(&row.ancestor_id)),
        ("descendant_id", json::s(&row.descendant_id)),
        ("depth", json::i(row.depth)),
        ("registered_at", num(row.registered_at)),
    ])
}

fn encode_lock(row: &Lock) -> Value {
    json::obj(vec![
        ("id", json::i(row.id)),
        ("document_path", json::s(&row.document_path)),
        ("locked_by", json::s(&row.locked_by)),
        ("locked_at", num(row.locked_at)),
        ("lock_ttl", num(row.lock_ttl)),
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
        ("created_at", num(row.created_at)),
    ])
}

fn encode_note(row: &Notification) -> Value {
    json::obj(vec![
        ("id", json::i(row.id)),
        ("document_path", json::s(&row.document_path)),
        ("change_type", json::s(&row.change_type)),
        ("agent_id", json::s(&row.agent_id)),
        ("worktree_root", json::opt_string(&row.worktree_root)),
        ("created_at", num(row.created_at)),
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
        ("updated_at", num(row.updated_at)),
    ])
}

fn encode_own(row: &Ownership) -> Value {
    json::obj(vec![
        ("document_path", json::s(&row.document_path)),
        ("assigned_agent_id", json::s(&row.assigned_agent_id)),
        ("assigned_at", num(row.assigned_at)),
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
        ("created_at", num(row.created_at)),
        (
            "read_at",
            match row.read_at {
                Some(n) => num(n),
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
        ("created_at", num(row.created_at)),
        ("updated_at", num(row.updated_at)),
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
        ("declared_at", num(row.declared_at)),
        ("ttl", num(row.ttl)),
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
        ("created_at", num(row.created_at)),
        ("acknowledged_at", opt_f(row.acknowledged_at)),
        ("completed_at", opt_f(row.completed_at)),
    ])
}

fn encode_hack(row: &HandoffAck) -> Value {
    json::obj(vec![
        ("handoff_id", json::i(row.handoff_id)),
        ("agent_id", json::s(&row.agent_id)),
        ("acknowledged_at", num(row.acknowledged_at)),
    ])
}

fn encode_fail(row: &Failure) -> Value {
    json::obj(vec![
        ("id", json::i(row.id)),
        ("task_id", json::s(&row.task_id)),
        ("error", json::opt_string(&row.error)),
        ("attempt", json::i(row.attempt)),
        ("max_retries", json::i(row.max_retries)),
        ("first_attempt_at", num(row.first_attempt_at)),
        ("last_attempt_at", num(row.last_attempt_at)),
        ("dead_letter_at", opt_f(row.dead_letter_at)),
        ("status", json::s(&row.status)),
    ])
}

fn encode_lease(row: &Lease) -> Value {
    json::obj(vec![
        ("lease_name", json::s(&row.lease_name)),
        ("holder_id", json::s(&row.holder_id)),
        ("acquired_at", num(row.acquired_at)),
        ("ttl", num(row.ttl)),
        ("expires_at", num(row.expires_at)),
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
        ("created_at", num(row.created_at)),
    ])
}

fn encode_spawn(row: &Spawn) -> Value {
    json::obj(vec![
        ("id", json::s(&row.id)),
        ("parent_agent_id", json::s(&row.parent_agent_id)),
        ("subagent_type", json::opt_string(&row.subagent_type)),
        ("description", json::opt_string(&row.description)),
        ("prompt", json::opt_string(&row.prompt)),
        ("created_at", num(row.created_at)),
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
        ("created_at", num(row.created_at)),
        ("ttl", num(row.ttl)),
        ("expires_at", num(row.expires_at)),
        ("expected_count", json::i(row.expected_count)),
    ])
}

fn encode_back(row: &BroadcastAck) -> Value {
    json::obj(vec![
        ("broadcast_id", json::i(row.broadcast_id)),
        ("agent_id", json::s(&row.agent_id)),
        ("acknowledged_at", num(row.acknowledged_at)),
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
        ("claimed_at", num(row.claimed_at)),
        ("ttl", num(row.ttl)),
        ("expires_at", num(row.expires_at)),
        ("note", json::opt_string(&row.note)),
    ])
}

fn hash_hub(row: &HubEvent) -> u64 {
    mix(&[
        row.id as u64,
        hash_str(&row.topic),
        hash_str(&row.payload),
        hash_f(row.created_at),
    ])
}

fn encode_hub(row: &HubEvent) -> Value {
    json::obj(vec![
        ("id", json::i(row.id)),
        ("topic", json::s(&row.topic)),
        ("payload", json::s(&row.payload)),
        ("created_at", num(row.created_at)),
    ])
}

fn encode_ce(row: &ClaimEvent) -> Value {
    json::obj(vec![
        ("id", json::i(row.id)),
        ("area", json::s(&row.area)),
        ("from_agent_id", json::opt_string(&row.from_agent_id)),
        ("to_agent_id", json::opt_string(&row.to_agent_id)),
        ("action", json::s(&row.action)),
        ("at", num(row.at)),
    ])
}

/// A stored number: always finite, so the journal stays strict JSON.
fn num(value: f64) -> Value {
    json::f(json::finite(value))
}

fn opt_f(value: Option<f64>) -> Value {
    match value {
        Some(n) => num(n),
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
/// A stored number, read back finite: a row 1.2.0 wrote with `Infinity`
/// (a lock TTL of `1e400`, say) becomes the largest number 1.3.0 accepts.
fn num_f(value: &Value, key: &str) -> f64 {
    value.get(key).and_then(Value::as_f64).map(json::finite).unwrap_or(0.0)
}
fn num_i(value: &Value, key: &str) -> i64 {
    value.get(key).and_then(Value::as_i64).unwrap_or(0)
}
fn opt_num(value: &Value, key: &str) -> Option<f64> {
    value.get(key).and_then(Value::as_f64).map(json::finite)
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
fn decode_hub(v: &Value) -> Result<HubEvent, String> {
    Ok(HubEvent {
        id: num_i(v, "id"),
        topic: req_str(v, "topic")?,
        payload: req_str(v, "payload")?,
        created_at: num_f(v, "created_at"),
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

/// The error for a store lock that could not be taken: who to blame and
/// what to do.
fn busy(lock_path: &Path, err: std::io::Error) -> String {
    if err.kind() == std::io::ErrorKind::TimedOut {
        format!(
            "store busy: another process has held {} for {} s; retry the call. If it keeps failing, a stuck coordinationhub process holds the lock: stop it, or point this server at another --storage-dir",
            lock_path.display(),
            util::lock_wait_s()
        )
    } else {
        format!("cannot lock {}: {err}", lock_path.display())
    }
}

pub fn home_storage() -> PathBuf {
    util::home_dir().join(".coordinationhub")
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn utc_stamps_are_calendar_dates() {
        assert_eq!(utc_stamp(0.0), "19700101T000000Z");
        assert_eq!(utc_stamp(951_782_400.0), "20000229T000000Z");
        assert_eq!(utc_stamp(1_794_731_900.55), "20261115T083820Z");
        assert_eq!(utc_stamp(4_102_444_799.0), "20991231T235959Z");
        assert_eq!(utc_stamp(-5.0), "19700101T000000Z");
    }

    #[test]
    fn the_generation_is_read_from_the_first_line_only() {
        let line = generation_line("1a-2b-3c");
        assert_eq!(generation_of(line.as_bytes()), "1a-2b-3c");
        assert_eq!(generation_of(format!("{line}\n{{\"t\":\"ids\"}}").as_bytes()), "1a-2b-3c");
        assert_eq!(generation_of(b"{\"t\":\"put\",\"table\":\"agents\"}"), "");
        assert_eq!(generation_of(b""), "");
        // The line is a record every version reads: a `del` on a table
        // none of them has.
        let record = json::parse(&line).unwrap();
        assert_eq!(record.get("t").and_then(Value::as_str), Some("del"));
        let mut state = State::default();
        apply_line(&mut state, &record).unwrap();
        assert_eq!(state, State::default());
        // Two ids never match, and a whole line fits the head that is read.
        assert_ne!(new_generation(), new_generation());
        assert!(generation_line(&new_generation()).len() < GENERATION_HEAD);
    }

    #[test]
    fn stored_numbers_are_finite() {
        let lock = Lock {
            id: 1,
            document_path: "x".into(),
            locked_by: "a".into(),
            locked_at: f64::NAN,
            lock_ttl: f64::INFINITY,
            lock_type: "exclusive".into(),
            region_start: None,
            region_end: None,
            worktree_root: None,
        };
        let text = json::dumps(&encode_lock(&lock));
        assert!(!text.contains("Infinity") && !text.contains("NaN"), "{text}");
        let back = decode_lock(&json::parse(&text).unwrap()).unwrap();
        assert_eq!(back.lock_ttl, json::MAX_NUMBER);
        assert_eq!(back.locked_at, 0.0);
        // And what 1.2.0 wrote reads back finite.
        let (old, repaired) = json::parse_journal(&text.replace("1000000000000000.0", "Infinity")).unwrap();
        assert!(repaired);
        assert_eq!(decode_lock(&old).unwrap().lock_ttl, json::MAX_NUMBER);
    }
}
