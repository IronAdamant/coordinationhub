//! Background reap for long-running servers, and retention for the event
//! journal.

use crate::act_identity;
use crate::act_lock;
use crate::act_view;
use crate::db::State;
use crate::engine::Engine;
use crate::json::{self, Value};
use crate::util;
use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

pub fn spawn(engine: Arc<Mutex<Engine>>) {
    let flag = std::env::var("COORDINATIONHUB_HOUSEKEEPING").unwrap_or_else(|_| "1".into());
    if matches!(flag.as_str(), "0" | "false" | "no" | "off" | "") {
        return;
    }
    let interval = util::env_f64("COORDINATIONHUB_HOUSEKEEPING_AGENTS_INTERVAL_S", 30.0);
    let prune_every = util::env_f64("COORDINATIONHUB_HOUSEKEEPING_EVENTS_INTERVAL_S", 600.0);
    thread::spawn(move || {
        let mut pruned_at: Option<Instant> = None;
        loop {
            thread::sleep(Duration::from_secs_f64(json::finite(interval).max(1.0)));
            let Ok(mut engine) = engine.lock() else {
                return;
            };
            if engine.store.begin().is_err() {
                continue;
            }
            let stale = util::env_f64("COORDINATIONHUB_AGENTS_STALE_TIMEOUT_S", 600.0);
            let _ = act_identity::reap_stale(&mut engine.store.state, stale);
            let _ = act_lock::reap_expired(&mut engine.store.state, 0.0);
            let _ = act_view::who(
                &mut engine.store.state,
                "status",
                None,
                None,
                None,
                600.0,
                None,
            );
            // Retention: on the first pass, then every `prune_every` seconds.
            let due = pruned_at
                .map(|at| at.elapsed().as_secs_f64() >= json::finite(prune_every).max(1.0))
                .unwrap_or(true);
            if due {
                prune(&mut engine.store.state, util::now());
                pruned_at = Some(Instant::now());
            }
            let _ = engine.store.commit();
            engine.store.end();
        }
    });
}

/// How long events and notifications are kept, in seconds:
/// `COORDINATIONHUB_EVENTS_MAX_AGE_S` (the 0.8.0 name), seven days by
/// default. `None` when it is 0 or less: nothing is pruned.
pub fn retention_s() -> Option<f64> {
    let age = util::env_f64("COORDINATIONHUB_EVENTS_MAX_AGE_S", 7.0 * 24.0 * 3600.0);
    (age.is_finite() && age > 0.0).then_some(age)
}

/// Drops events and notifications past the retention window. Returns how
/// many rows went.
pub fn prune(state: &mut State, now: f64) -> usize {
    match retention_s() {
        Some(age) => prune_before(state, now - age),
        None => 0,
    }
}

/// Drops hub events, change notifications and claim events created before
/// `cutoff`, except the events live answers are still read from (see
/// `needed_events`).
pub fn prune_before(state: &mut State, cutoff: f64) -> usize {
    let before = state.hub_events.len() + state.notifications.len() + state.claim_events.len();
    if state.hub_events.iter().any(|event| event.created_at < cutoff) {
        let needed = needed_events(state);
        state
            .hub_events
            .retain(|event| event.created_at >= cutoff || needed.contains(&event.id));
    }
    state.notifications.retain(|row| row.created_at >= cutoff);
    state.claim_events.retain(|row| row.at >= cutoff);
    before - (state.hub_events.len() + state.notifications.len() + state.claim_events.len())
}

/// The ids of the events two readers still depend on, however old:
///
/// - `act_identity::last_lifecycle` reads an agent's latest
///   `agent.registered` / `deregistered` / `reaped` / `revived` event to tell
///   a reaped agent (revived by its next heartbeat) from a deregistered
///   one. The latest such event of every agent that still has a row stays.
/// - `act_lock::lapses` reads `lock.expired` events that no later acquire,
///   release, clear or deregistration of the same agent has closed, to tell
///   that agent its lock ran out. Every still-open one stays. The events
///   that closed the others can go: once both are gone the answer is the
///   same.
///
/// So neither answer changes for an agent that still exists.
fn needed_events(state: &State) -> HashSet<i64> {
    let agents: HashSet<&str> = state.agents.iter().map(|agent| agent.agent_id.as_str()).collect();
    let mut needed = HashSet::new();
    let mut lifecycle_seen: HashSet<String> = HashSet::new();
    for event in state.hub_events.iter().rev() {
        if !matches!(
            event.topic.as_str(),
            "agent.registered" | "agent.deregistered" | "agent.reaped" | "agent.revived"
        ) {
            continue;
        }
        let Some(agent) = payload_agent(&event.payload).map(|(agent, _)| agent) else {
            continue;
        };
        if agents.contains(agent.as_str()) && lifecycle_seen.insert(agent) {
            needed.insert(event.id);
        }
    }
    // The same walk as `act_lock::lapses`, for every agent at once.
    type Open = Vec<(String, Option<i64>, Option<i64>, i64)>;
    let mut open: HashMap<String, Open> = HashMap::new();
    for event in &state.hub_events {
        let topic = event.topic.as_str();
        if !matches!(
            topic,
            "lock.expired" | "lock.acquired" | "lock.released" | "lock.expired_cleared" | "agent.deregistered"
        ) {
            continue;
        }
        let Some((agent, payload)) = payload_agent(&event.payload) else {
            continue;
        };
        let bulk = payload.get("bulk").and_then(Value::as_bool) == Some(true);
        if topic == "agent.deregistered" || bulk {
            open.remove(&agent);
            continue;
        }
        let path = payload.get("document_path").and_then(Value::as_str).unwrap_or("");
        let start = payload.get("region_start").and_then(Value::as_i64);
        let end = payload.get("region_end").and_then(Value::as_i64);
        let entry = open.entry(agent).or_default();
        entry.retain(|(held, from, to, _)| !(held == path && *from == start && *to == end));
        if topic == "lock.expired" {
            entry.push((path.to_string(), start, end, event.id));
        }
    }
    for (agent, lapses) in &open {
        if agents.contains(agent.as_str()) {
            needed.extend(lapses.iter().map(|(_, _, _, id)| *id));
        }
    }
    needed
}

/// The `agent_id` of an event payload, with the parsed payload.
fn payload_agent(payload: &str) -> Option<(String, Value)> {
    let value = json::parse(payload).ok()?;
    let agent = value.get("agent_id").and_then(Value::as_str)?.to_string();
    Some((agent, value))
}
