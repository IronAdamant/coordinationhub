//! Calls that wait run on engines of their own.
//!
//! A server keeps one shared engine behind a mutex. A waiting call (a long
//! poll, `wait_for_*`, `await_*`, `acquire_lock` with `retry`) that ran on it
//! would hold that mutex for its whole timeout, and every other call from
//! every agent on the server would queue behind it: a heartbeat could sit
//! there until the client's idle timeout. Waits therefore run on a second
//! engine over the same store. The journal's file lock already keeps
//! several engines (or processes) consistent, so the shared engine sees
//! what a wait writes, and the other way round.

use crate::engine::Engine;
use crate::json::Value;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

/// At most this many waits run at once on one server.
pub const MAX_RUNNING: usize = 64;

/// Idle wait engines kept for reuse.
const SPARE: usize = 4;

pub struct Waits {
    storage: Option<PathBuf>,
    project: Option<PathBuf>,
    namespace: String,
    spare: Mutex<Vec<Engine>>,
    running: AtomicUsize,
}

impl Waits {
    pub fn new(storage: Option<PathBuf>, project: Option<PathBuf>, namespace: &str) -> Waits {
        Waits {
            storage,
            project,
            namespace: namespace.to_string(),
            spare: Mutex::new(Vec::new()),
            running: AtomicUsize::new(0),
        }
    }

    /// Reserves a slot for one wait; false when `MAX_RUNNING` are running.
    /// Every true must be followed by exactly one `run`.
    pub fn admit(&self) -> bool {
        let mut current = self.running.load(Ordering::SeqCst);
        loop {
            if current >= MAX_RUNNING {
                return false;
            }
            match self.running.compare_exchange(current, current + 1, Ordering::SeqCst, Ordering::SeqCst) {
                Ok(_) => return true,
                Err(seen) => current = seen,
            }
        }
    }

    /// The refusal sent when `admit` says no.
    pub fn refusal() -> String {
        format!(
            "{MAX_RUNNING} waiting calls are already running on this server; let one finish or cancel it, then retry"
        )
    }

    /// Runs one admitted wait. `cancel` stops it early when set.
    pub fn run(&self, tool: &str, args: &Value, cancel: Arc<AtomicBool>) -> Result<Value, String> {
        let result = self.run_inner(tool, args, cancel);
        self.running.fetch_sub(1, Ordering::SeqCst);
        result
    }

    fn run_inner(&self, tool: &str, args: &Value, cancel: Arc<AtomicBool>) -> Result<Value, String> {
        let reused = self.spare.lock().ok().and_then(|mut spare| spare.pop());
        let mut engine = match reused {
            Some(engine) => engine,
            None => Engine::open(self.storage.as_deref(), self.project.as_deref(), &self.namespace)?,
        };
        engine.cancel = Some(cancel);
        let result = engine.call(tool, args);
        engine.cancel = None;
        if let Ok(mut spare) = self.spare.lock() {
            if spare.len() < SPARE {
                spare.push(engine);
            }
        }
        result
    }
}
