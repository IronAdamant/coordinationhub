//! Agent registration, heartbeat, lineage, and reap.

use crate::act_lock;
use crate::db::{Agent, Descendant, Lineage, State};
use crate::json::{self, Value};
use crate::util;
use std::collections::HashSet;
use std::path::Path;

pub const MAX_AGENTS: i64 = 10_000;
pub const HEARTBEAT_INTERVAL: i64 = 30;

pub fn register(
    state: &mut State,
    agent_id: &str,
    parent_id: Option<&str>,
    worktree: &str,
    raw_ide_id: Option<&str>,
    ide_vendor: Option<&str>,
    graph_loaded: bool,
    coordination_url: &str,
) -> Value {
    let now = util::now();
    let pid = util::pid();
    if let Some(existing) = state.agent(agent_id) {
        if existing.status == "active" && existing.pid != pid {
            return json::obj(vec![
                ("registered", json::b(false)),
                ("agent_id", json::s(agent_id)),
                ("reason", json::s("collision")),
                ("existing_pid", json::i(existing.pid)),
            ]);
        }
    } else {
        let active = state
            .agents
            .iter()
            .filter(|agent| agent.status == "active")
            .count() as i64;
        if active >= MAX_AGENTS {
            return json::obj(vec![
                ("registered", json::b(false)),
                ("agent_id", json::s(agent_id)),
                ("reason", json::s("max_agents_reached")),
                ("max_agents", json::i(MAX_AGENTS)),
                ("active_count", json::i(active)),
            ]);
        }
    }
    if let Some(agent) = state.agent_mut(agent_id) {
        agent.parent_id = parent_id.map(|s| s.to_string());
        agent.worktree_root = worktree.to_string();
        agent.pid = pid;
        agent.last_heartbeat = now;
        agent.status = "active".into();
        if raw_ide_id.is_some() {
            agent.raw_ide_id = raw_ide_id.map(|s| s.to_string());
        }
        if ide_vendor.is_some() {
            agent.ide_vendor = ide_vendor.map(|s| s.to_string());
        }
    } else {
        state.agents.push(Agent {
            agent_id: agent_id.to_string(),
            parent_id: parent_id.map(|s| s.to_string()),
            worktree_root: worktree.to_string(),
            pid,
            started_at: now,
            last_heartbeat: now,
            status: "active".into(),
            raw_ide_id: raw_ide_id.map(|s| s.to_string()),
            ide_vendor: ide_vendor.map(|s| s.to_string()),
            stop_requested_at: None,
        });
    }
    if let Some(parent) = parent_id {
        record_descendants(state, agent_id, parent, now);
        if !state
            .lineage
            .iter()
            .any(|row| row.parent_id == parent && row.child_id == agent_id)
        {
            state.lineage.push(Lineage {
                parent_id: parent.to_string(),
                child_id: agent_id.to_string(),
                spawned_at: now,
            });
        }
    }
    state.record_event(
        "agent.registered",
        json::obj(vec![
            ("agent_id", json::s(agent_id)),
            (
                "parent_id",
                match parent_id {
                    Some(id) => json::s(id),
                    None => json::null(),
                },
            ),
        ]),
    );
    context_bundle(
        state,
        agent_id,
        parent_id,
        worktree,
        graph_loaded,
        coordination_url,
    )
}

fn record_descendants(state: &mut State, agent_id: &str, parent_id: &str, now: f64) {
    let known = state
        .descendants
        .iter()
        .any(|row| row.descendant_id == agent_id);
    let mut stack = vec![(parent_id.to_string(), 1_i64)];
    let mut seen = HashSet::new();
    while let Some((ancestor, depth)) = stack.pop() {
        if !seen.insert(ancestor.clone()) {
            continue;
        }
        if !known
            || !state
                .descendants
                .iter()
                .any(|row| row.ancestor_id == ancestor && row.descendant_id == agent_id)
        {
            state.descendants.push(Descendant {
                ancestor_id: ancestor.clone(),
                descendant_id: agent_id.to_string(),
                depth,
                registered_at: now,
            });
        }
        if let Some(agent) = state.agents.iter().find(|agent| agent.agent_id == ancestor) {
            if let Some(next) = agent.parent_id.clone() {
                stack.push((next, depth + 1));
            }
        }
    }
}

pub fn context_bundle(
    state: &State,
    agent_id: &str,
    parent_id: Option<&str>,
    worktree: &str,
    graph_loaded: bool,
    coordination_url: &str,
) -> Value {
    let now = util::now();
    let agents = list_rows(state, true, 600.0, false);
    let active_locks: Vec<Value> = state
        .locks
        .iter()
        .filter(|lock| now < lock.locked_at + lock.lock_ttl)
        .map(|lock| {
            json::obj(vec![
                ("document_path", json::s(&lock.document_path)),
                ("locked_by", json::s(&lock.locked_by)),
                ("expires_at", json::f(lock.locked_at + lock.lock_ttl)),
            ])
        })
        .collect();
    let mut notes: Vec<&crate::db::Notification> = state
        .notifications
        .iter()
        .filter(|row| row.created_at > now - 300.0)
        .collect();
    notes.sort_by(|a, b| {
        b.created_at
            .partial_cmp(&a.created_at)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    notes.truncate(20);
    let pending = notes
        .into_iter()
        .map(|row| {
            json::obj(vec![
                ("document_path", json::s(&row.document_path)),
                ("change_type", json::s(&row.change_type)),
                ("agent_id", json::s(&row.agent_id)),
                ("created_at", json::f(row.created_at)),
            ])
        })
        .collect();
    let registered = match agents.get("agents").and_then(Value::as_array) {
        Some(items) => json::arr(
            items
                .iter()
                .map(|row| {
                    json::obj(vec![
                        (
                            "agent_id",
                            json::s(row.get("agent_id").and_then(Value::as_str).unwrap_or("")),
                        ),
                        (
                            "status",
                            json::s(row.get("status").and_then(Value::as_str).unwrap_or("")),
                        ),
                        (
                            "last_heartbeat",
                            row.get("last_heartbeat").cloned().unwrap_or(json::null()),
                        ),
                    ])
                })
                .collect(),
        ),
        None => json::arr(vec![]),
    };
    let mut bundle = vec![
        ("agent_id", json::s(agent_id)),
        ("parent_id", json::opt_str(parent_id)),
        ("worktree_root", json::s(worktree)),
        ("registered_agents", registered),
        ("active_locks", json::arr(active_locks)),
        ("pending_notifications", json::arr(pending)),
        ("coordination_url", json::s(coordination_url)),
        ("graph_loaded", json::b(graph_loaded)),
    ];
    if let Some(resp) = state
        .responsibilities
        .iter()
        .find(|row| row.agent_id == agent_id)
    {
        let responsibilities = resp
            .responsibilities
            .as_deref()
            .and_then(|raw| json::parse(raw).ok())
            .unwrap_or_else(|| json::arr(vec![]));
        bundle.push(("responsibilities", responsibilities));
        bundle.push(("role", json::opt_string(&resp.role)));
        bundle.push(("graph_agent_id", json::opt_string(&resp.graph_agent_id)));
        bundle.push(("current_task", json::opt_string(&resp.current_task)));
    }
    let owned: Vec<Value> = state
        .ownership
        .iter()
        .filter(|row| row.assigned_agent_id == agent_id)
        .map(|row| json::s(&row.document_path))
        .collect();
    if !owned.is_empty() {
        bundle.push(("owned_files", json::arr(owned)));
    }
    bundle.push(("descendants_status", descendants_status(state, agent_id)));
    json::obj(bundle)
}

fn descendants_status(state: &State, ancestor: &str) -> Value {
    let mut rows: Vec<&Descendant> = state
        .descendants
        .iter()
        .filter(|row| row.ancestor_id == ancestor)
        .collect();
    rows.sort_by(|a, b| {
        a.depth
            .cmp(&b.depth)
            .then_with(|| a.descendant_id.cmp(&b.descendant_id))
    });
    json::arr(
        rows.into_iter()
            .map(|row| {
                let agent = state.agent(&row.descendant_id);
                let task = state
                    .responsibilities
                    .iter()
                    .find(|resp| resp.agent_id == row.descendant_id)
                    .and_then(|resp| resp.current_task.clone());
                json::obj(vec![
                    ("depth", json::i(row.depth)),
                    ("agent_id", json::s(&row.descendant_id)),
                    (
                        "status",
                        json::s(agent.map(|a| a.status.as_str()).unwrap_or("unknown")),
                    ),
                    (
                        "last_heartbeat",
                        agent
                            .map(|a| json::f(a.last_heartbeat))
                            .unwrap_or(json::null()),
                    ),
                    ("current_task", json::opt_string(&task)),
                ])
            })
            .collect(),
    )
}

pub fn heartbeat(state: &mut State, agent_id: &str) -> Value {
    let now = util::now();
    let status = state.agent(agent_id).map(|agent| agent.status.clone());
    match status.as_deref() {
        Some("active") => {
            if let Some(agent) = state.agent_mut(agent_id) {
                agent.last_heartbeat = now;
            }
            refresh_claims(state, agent_id, now);
            json::obj(vec![
                ("updated", json::b(true)),
                ("next_heartbeat_in", json::i(HEARTBEAT_INTERVAL)),
            ])
        }
        Some(other) => json::obj(vec![
            ("updated", json::b(false)),
            ("next_heartbeat_in", json::i(HEARTBEAT_INTERVAL)),
            ("reason", json::s(format!("agent_{other}"))),
        ]),
        None => json::obj(vec![
            ("updated", json::b(false)),
            ("next_heartbeat_in", json::i(HEARTBEAT_INTERVAL)),
            ("reason", json::s("not_registered")),
        ]),
    }
}

fn refresh_claims(state: &mut State, agent_id: &str, now: f64) {
    for claim in &mut state.claims {
        if claim.agent_id == agent_id {
            claim.expires_at = now + claim.ttl;
        }
    }
}

pub fn deregister(state: &mut State, agent_id: &str) -> Value {
    let parent_id = state
        .agent(agent_id)
        .and_then(|agent| agent.parent_id.clone());
    let mut effective: Option<String> = None;
    let mut cursor = parent_id.clone();
    let mut seen = HashSet::from([agent_id.to_string()]);
    while let Some(current) = cursor {
        if !seen.insert(current.clone()) {
            break;
        }
        match state.agent(&current) {
            Some(agent) if agent.status == "active" => {
                effective = Some(current);
                break;
            }
            Some(agent) => cursor = agent.parent_id.clone(),
            None => break,
        }
    }
    if let Some(agent) = state.agent_mut(agent_id) {
        agent.status = "stopped".into();
    }
    let children: Vec<String> = state
        .agents
        .iter()
        .filter(|agent| agent.parent_id.as_deref() == Some(agent_id) && agent.status == "active")
        .map(|agent| agent.agent_id.clone())
        .collect();
    let orphaned = children.len() as i64;
    for child in &children {
        if let Some(agent) = state.agent_mut(child) {
            agent.parent_id = effective.clone();
        }
        state
            .lineage
            .retain(|row| !(row.parent_id == agent_id && row.child_id == *child));
    }
    let locks = act_lock::release_agent(state, agent_id);
    let released = locks.get("released").and_then(Value::as_i64).unwrap_or(0);
    if released > 0 {
        state.record_event(
            "lock.released",
            json::obj(vec![
                ("agent_id", json::s(agent_id)),
                ("bulk", json::b(true)),
                ("count", json::i(released)),
            ]),
        );
    }
    let lease_released = release_lease(state, agent_id);
    if lease_released {
        state.record_event(
            "lease.released",
            json::obj(vec![
                ("lease_name", json::s(crate::act_board::COORDINATOR)),
                ("holder_id", json::s(agent_id)),
            ]),
        );
    }
    release_claims(state, agent_id);
    state.record_event(
        "agent.deregistered",
        json::obj(vec![("agent_id", json::s(agent_id))]),
    );
    json::obj(vec![
        ("deregistered", json::b(true)),
        ("children_orphaned", json::i(orphaned)),
        ("locks_released", json::i(released)),
        ("lease_released", json::b(lease_released)),
    ])
}

/// Drops the coordinator lease when this agent holds it, as 0.8.0's
/// deregistration does. The name must be the stored one,
/// `COORDINATOR_LEADER`; matching `"coordinator"` left the lease held.
fn release_lease(state: &mut State, agent_id: &str) -> bool {
    let before = state.leases.len();
    state.leases.retain(|lease| {
        !(lease.lease_name == crate::act_board::COORDINATOR && lease.holder_id == agent_id)
    });
    before != state.leases.len()
}

fn release_claims(state: &mut State, agent_id: &str) {
    let gone: Vec<(String, String)> = state
        .claims
        .iter()
        .filter(|claim| claim.agent_id == agent_id)
        .map(|claim| (claim.area.clone(), claim.agent_id.clone()))
        .collect();
    state.claims.retain(|claim| claim.agent_id != agent_id);
    let now = util::now();
    for (area, from) in gone {
        let id = state.next("claim_events");
        state.claim_events.push(crate::db::ClaimEvent {
            id,
            area,
            from_agent_id: Some(from),
            to_agent_id: None,
            action: "released".into(),
            at: now,
        });
    }
}

pub fn list_rows(
    state: &State,
    active_only: bool,
    stale_timeout: f64,
    include_stale: bool,
) -> Value {
    let now = util::now();
    let cutoff = now - stale_timeout;
    let filter_stale = active_only && !include_stale;
    let mut rows: Vec<&Agent> = state
        .agents
        .iter()
        .filter(|agent| {
            if active_only && filter_stale {
                agent.status == "active" && agent.last_heartbeat >= cutoff
            } else if active_only {
                agent.status == "active"
            } else {
                true
            }
        })
        .collect();
    rows.sort_by(|a, b| {
        b.last_heartbeat
            .partial_cmp(&a.last_heartbeat)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    let agents = rows
        .into_iter()
        .map(|agent| {
            json::obj(vec![
                ("agent_id", json::s(&agent.agent_id)),
                ("parent_id", json::opt_string(&agent.parent_id)),
                ("worktree_root", json::s(&agent.worktree_root)),
                ("pid", json::i(agent.pid)),
                ("started_at", json::f(agent.started_at)),
                ("last_heartbeat", json::f(agent.last_heartbeat)),
                ("status", json::s(&agent.status)),
                ("raw_ide_id", json::opt_string(&agent.raw_ide_id)),
                ("ide_vendor", json::opt_string(&agent.ide_vendor)),
                (
                    "stop_requested_at",
                    match agent.stop_requested_at {
                        Some(n) => json::f(n),
                        None => json::null(),
                    },
                ),
                (
                    "stale",
                    json::b((now - agent.last_heartbeat) > stale_timeout),
                ),
            ])
        })
        .collect();
    json::obj(vec![("agents", json::arr(agents))])
}

pub fn reap_stale(state: &mut State, timeout: f64) -> Value {
    let now = util::now();
    let cutoff = now - timeout;
    let stale: Vec<(String, Option<String>)> = state
        .agents
        .iter()
        .filter(|agent| agent.status == "active" && agent.last_heartbeat < cutoff)
        .map(|agent| (agent.agent_id.clone(), agent.parent_id.clone()))
        .collect();
    let mut reaped = 0_i64;
    let mut orphaned = 0_i64;
    for (agent_id, parent_id) in stale {
        let still = state
            .agent(&agent_id)
            .map(|agent| agent.status == "active" && agent.last_heartbeat < cutoff)
            .unwrap_or(false);
        if !still {
            continue;
        }
        if let Some(agent) = state.agent_mut(&agent_id) {
            agent.status = "stopped".into();
        }
        reaped += 1;
        let children: Vec<String> = state
            .agents
            .iter()
            .filter(|agent| {
                agent.parent_id.as_deref() == Some(agent_id.as_str()) && agent.status == "active"
            })
            .map(|agent| agent.agent_id.clone())
            .collect();
        for child in children {
            if let Some(agent) = state.agent_mut(&child) {
                agent.parent_id = parent_id.clone();
            }
            state
                .lineage
                .retain(|row| !(row.parent_id == agent_id && row.child_id == child));
            orphaned += 1;
        }
        release_claims(state, &agent_id);
    }
    json::obj(vec![
        ("reaped", json::i(reaped)),
        ("orphaned_children", json::i(orphaned)),
    ])
}

pub fn relations(state: &State, agent_id: &str, mode: &str) -> Value {
    if mode == "siblings" {
        let siblings = siblings(state, agent_id);
        return json::obj(vec![
            ("mode", json::s("siblings")),
            ("agent_id", json::s(agent_id)),
            ("siblings", json::arr(siblings)),
        ]);
    }
    lineage(state, agent_id)
}

pub fn siblings(state: &State, agent_id: &str) -> Vec<Value> {
    let Some(parent) = state
        .agent(agent_id)
        .and_then(|agent| agent.parent_id.clone())
    else {
        return Vec::new();
    };
    state
        .agents
        .iter()
        .filter(|agent| {
            agent.parent_id.as_deref() == Some(parent.as_str())
                && agent.status == "active"
                && agent.agent_id != agent_id
        })
        .map(|agent| {
            json::obj(vec![
                ("agent_id", json::s(&agent.agent_id)),
                ("status", json::s(&agent.status)),
                ("last_heartbeat", json::f(agent.last_heartbeat)),
            ])
        })
        .collect()
}

pub fn lineage(state: &State, agent_id: &str) -> Value {
    let mut ancestors = Vec::new();
    let mut current = agent_id.to_string();
    loop {
        let Some(agent) = state.agent(&current) else {
            break;
        };
        let Some(parent) = agent.parent_id.clone() else {
            break;
        };
        let grand = state.agent(&parent).and_then(|row| row.parent_id.clone());
        ancestors.push(json::obj(vec![
            ("agent_id", json::s(&parent)),
            ("parent_id", json::opt_string(&grand)),
        ]));
        current = parent;
    }
    let mut descendants = Vec::new();
    let mut stack = vec![agent_id.to_string()];
    while let Some(current) = stack.pop() {
        let children: Vec<String> = state
            .agents
            .iter()
            .filter(|agent| {
                agent.parent_id.as_deref() == Some(current.as_str()) && agent.status == "active"
            })
            .map(|agent| agent.agent_id.clone())
            .collect();
        for child in children {
            descendants.push(json::obj(vec![
                ("agent_id", json::s(&child)),
                ("parent_id", json::s(&current)),
            ]));
            stack.push(child);
        }
    }
    json::obj(vec![
        ("ancestors", json::arr(ancestors)),
        ("descendants", json::arr(descendants)),
    ])
}

pub fn worktree_of(project_root: Option<&Path>, explicit: Option<&str>) -> String {
    // Python stores `worktree_root or effective_root`: the caller's text as
    // given, and the resolved project root when it is absent or empty.
    if let Some(path) = explicit.filter(|path| !path.is_empty()) {
        return path.to_string();
    }
    if let Some(root) = project_root {
        return util::path_str(root);
    }
    util::path_str(&std::env::current_dir().unwrap_or_else(|_| Path::new(".").to_path_buf()))
}
