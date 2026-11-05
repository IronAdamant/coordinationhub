//! Document locks, conflicts, and change notifications.

use crate::db::{Conflict, Lock, Notification, State};
use crate::json::{self, Value};
use crate::util::{self, normalize_path};
use std::path::Path;

const DEFAULT_TTL: f64 = 300.0;

pub fn regions_overlap(a0: Option<i64>, a1: Option<i64>, b0: Option<i64>, b1: Option<i64>) -> bool {
    if a0.is_none() || b0.is_none() {
        return true;
    }
    a0.unwrap_or(0) < b1.unwrap_or(0) && b0.unwrap_or(0) < a1.unwrap_or(0)
}

pub fn record_notification(
    state: &mut State,
    path: &str,
    change_type: &str,
    agent_id: &str,
    worktree: Option<String>,
) -> Value {
    let now = util::now();
    let id = state.next("notifications");
    state.notifications.push(Notification {
        id,
        document_path: path.to_string(),
        change_type: change_type.to_string(),
        agent_id: agent_id.to_string(),
        worktree_root: worktree,
        created_at: now,
    });
    json::obj(vec![
        ("recorded", json::b(true)),
        ("notification_id", json::i(id)),
        ("created_at", json::f(now)),
    ])
}

pub fn record_conflict(
    state: &mut State,
    path: &str,
    agent_a: &str,
    agent_b: &str,
    conflict_type: &str,
    resolution: &str,
    details: Option<String>,
) -> i64 {
    let id = state.next("conflicts");
    state.conflicts.push(Conflict {
        id,
        document_path: path.to_string(),
        agent_a: agent_a.to_string(),
        agent_b: agent_b.to_string(),
        conflict_type: conflict_type.to_string(),
        resolution: resolution.to_string(),
        details_json: details,
        created_at: util::now(),
    });
    id
}

fn lock_alive(lock: &Lock, now: f64) -> bool {
    lock.locked_at + lock.lock_ttl > now
}

pub fn acquire(
    state: &mut State,
    document_path: &str,
    agent_id: &str,
    lock_type: &str,
    ttl: f64,
    force: bool,
    region_start: Option<i64>,
    region_end: Option<i64>,
    project_root: Option<&Path>,
    attempt: i64,
) -> Value {
    let norm = normalize_path(document_path, project_root);
    let worktree = project_root.map(|p| util::path_str(p));
    let now = util::now();
    if let Some(pos) = state.locks.iter().position(|lock| {
        lock.document_path == norm
            && lock.locked_by == agent_id
            && lock.region_start == region_start
            && lock.region_end == region_end
            && worktree_ok(&lock.worktree_root, worktree.as_deref())
    }) {
        let lock = &mut state.locks[pos];
        lock.locked_at = now;
        lock.lock_ttl = ttl;
        lock.lock_type = lock_type.to_string();
        return json::obj(vec![
            ("acquired", json::b(true)),
            ("document_path", json::s(&norm)),
            ("locked_by", json::s(agent_id)),
            ("expires_at", json::f(now + ttl)),
            ("region_start", json::opt_i(region_start)),
            ("region_end", json::opt_i(region_end)),
            ("attempts", json::i(attempt)),
        ]);
    }
    let conflicts: Vec<Lock> = state
        .locks
        .iter()
        .filter(|lock| {
            lock.document_path == norm
                && lock_alive(lock, now)
                && lock.locked_by != agent_id
                && worktree_ok(&lock.worktree_root, worktree.as_deref())
                && !(lock.lock_type == "shared" && lock_type == "shared")
                && regions_overlap(lock.region_start, lock.region_end, region_start, region_end)
        })
        .cloned()
        .collect();
    if !conflicts.is_empty() && !force {
        for conflict in &conflicts {
            record_conflict(
                state,
                &norm,
                &conflict.locked_by,
                agent_id,
                "denied",
                "rejected",
                None,
            );
        }
        let first = &conflicts[0];
        let listed = conflicts
            .iter()
            .map(|c| {
                json::obj(vec![
                    ("locked_by", json::s(&c.locked_by)),
                    ("region_start", json::opt_i(c.region_start)),
                    ("region_end", json::opt_i(c.region_end)),
                ])
            })
            .collect();
        return json::obj(vec![
            ("acquired", json::b(false)),
            ("locked_by", json::s(&first.locked_by)),
            ("locked_at", json::f(first.locked_at)),
            ("expires_at", json::f(first.locked_at + first.lock_ttl)),
            ("worktree", json::opt_string(&first.worktree_root)),
            ("conflicts", json::arr(listed)),
            ("attempt", json::i(attempt)),
        ]);
    }
    if !conflicts.is_empty() && force {
        let ids: Vec<i64> = conflicts.iter().map(|c| c.id).collect();
        for conflict in &conflicts {
            record_conflict(
                state,
                &norm,
                &conflict.locked_by,
                agent_id,
                "lock_stolen",
                "force_overwritten",
                None,
            );
        }
        state.locks.retain(|lock| !ids.contains(&lock.id));
    }
    if let Some(scope_hit) = scope_violation(state, &norm, agent_id, project_root) {
        return json::obj(vec![
            ("acquired", json::b(false)),
            ("error", json::s("scope_violation")),
            ("scope_violation", scope_hit),
            ("attempts", json::i(attempt)),
        ]);
    }
    let id = state.next("locks");
    state.locks.push(Lock {
        id,
        document_path: norm.clone(),
        locked_by: agent_id.to_string(),
        locked_at: now,
        lock_ttl: ttl,
        lock_type: lock_type.to_string(),
        region_start,
        region_end,
        worktree_root: worktree.clone(),
    });
    record_notification(state, &norm, "locked", agent_id, worktree);
    let mut result = vec![
        ("acquired", json::b(true)),
        ("document_path", json::s(&norm)),
        ("locked_by", json::s(agent_id)),
        ("expires_at", json::f(now + ttl)),
        ("region_start", json::opt_i(region_start)),
        ("region_end", json::opt_i(region_end)),
        ("attempts", json::i(attempt)),
    ];
    if let Some(warning) = ownership_warning(state, &norm, agent_id, project_root) {
        result.push(("ownership_warning", warning));
    }
    if let Some(warning) = proximity_warning(state, &norm, agent_id) {
        result.push(("proximity_warning", warning));
    }
    state.record_event(
        "lock.acquired",
        json::obj(vec![
            ("document_path", json::s(&norm)),
            ("agent_id", json::s(agent_id)),
            ("lock_type", json::s(lock_type)),
            ("region_start", json::opt_i(region_start)),
            ("region_end", json::opt_i(region_end)),
        ]),
    );
    json::obj(result)
}

fn worktree_ok(stored: &Option<String>, wanted: Option<&str>) -> bool {
    match wanted {
        None => true,
        Some(wanted) => stored.as_deref().map(|s| s == wanted).unwrap_or(true),
    }
}

fn scope_violation(
    state: &State,
    norm: &str,
    agent_id: &str,
    project_root: Option<&Path>,
) -> Option<Value> {
    let resp = state
        .responsibilities
        .iter()
        .find(|row| row.agent_id == agent_id)?;
    let raw = resp.scope.as_deref()?;
    if raw.is_empty() {
        return None;
    }
    let Ok(parsed) = json::parse(raw) else {
        return None;
    };
    let Some(items) = parsed.as_array() else {
        return None;
    };
    if items.is_empty() {
        return None;
    }
    let prefixes: Vec<String> = items
        .iter()
        .filter_map(Value::as_str)
        .map(|s| s.to_string())
        .collect();
    if prefixes.is_empty() {
        return None;
    }
    for prefix in &prefixes {
        let norm_scope = normalize_path(prefix, project_root)
            .trim_end_matches('/')
            .to_string();
        if norm == norm_scope || norm.starts_with(&(norm_scope.clone() + "/")) {
            return None;
        }
    }
    let shown = json::arr(prefixes.iter().cloned().map(json::s).collect());
    Some(json::obj(vec![
        ("declared_scope", shown.clone()),
        ("attempted_path", json::s(norm)),
        (
            "message",
            json::s(format!(
                "Agent {agent_id} declared scope {} but attempted to lock {norm}",
                json::py_repr(&shown)
            )),
        ),
    ]))
}

fn ownership_warning(
    state: &mut State,
    norm: &str,
    agent_id: &str,
    project_root: Option<&Path>,
) -> Option<Value> {
    let owner = state
        .ownership
        .iter()
        .find(|row| row.document_path == norm)
        .map(|row| row.assigned_agent_id.clone())?;
    if owner == agent_id {
        return None;
    }
    let message = format!("Agent {agent_id} locked file owned by {owner}");
    let details = json::dumps(&json::obj(vec![("message", json::s(&message))]));
    record_conflict(
        state,
        norm,
        &owner,
        agent_id,
        "boundary_crossing",
        "allowed",
        Some(details),
    );
    let worktree = project_root.map(|p| util::path_str(p));
    record_notification(state, norm, "boundary_crossing", agent_id, worktree);
    Some(json::obj(vec![
        ("owned_by", json::s(&owner)),
        (
            "message",
            json::s(format!("File is assigned to {owner} in file_ownership")),
        ),
    ]))
}

fn proximity_warning(state: &State, norm: &str, agent_id: &str) -> Option<Value> {
    let now = util::now();
    let conflicts: Vec<&crate::db::Intent> = state
        .intents
        .iter()
        .filter(|row| {
            row.document_path == norm && row.agent_id != agent_id && row.declared_at + row.ttl > now
        })
        .collect();
    if conflicts.is_empty() {
        return None;
    }
    let ids: Vec<String> = conflicts.iter().map(|row| row.agent_id.clone()).collect();
    let listed = conflicts
        .iter()
        .map(|row| {
            json::obj(vec![
                ("agent_id", json::s(&row.agent_id)),
                ("intent", json::s(&row.intent)),
            ])
        })
        .collect();
    let id_text = json::py_repr(&json::string_list(&ids));
    Some(json::obj(vec![
        ("conflicting_agents", json::arr(listed)),
        (
            "message",
            json::s(format!(
                "Agents {id_text} have declared intent to work on {norm}"
            )),
        ),
    ]))
}

pub fn release(
    state: &mut State,
    document_path: &str,
    agent_id: &str,
    region_start: Option<i64>,
    region_end: Option<i64>,
    project_root: Option<&Path>,
) -> Value {
    let norm = normalize_path(document_path, project_root);
    let rows: Vec<&Lock> = state
        .locks
        .iter()
        .filter(|lock| {
            lock.document_path == norm
                && if region_start.is_some() {
                    lock.region_start == region_start && lock.region_end == region_end
                } else {
                    lock.region_start.is_none()
                }
        })
        .collect();
    if rows.is_empty() {
        if region_start.is_none() {
            let regions: Vec<Value> = state
                .locks
                .iter()
                .filter(|lock| {
                    lock.document_path == norm
                        && lock.locked_by == agent_id
                        && lock.region_start.is_some()
                })
                .map(|lock| {
                    json::obj(vec![
                        ("region_start", json::opt_i(lock.region_start)),
                        ("region_end", json::opt_i(lock.region_end)),
                    ])
                })
                .collect();
            if !regions.is_empty() {
                return json::obj(vec![
                    ("released", json::b(false)),
                    ("reason", json::s("region_required")),
                    ("region_locks", json::arr(regions)),
                ]);
            }
        }
        return json::obj(vec![
            ("released", json::b(false)),
            ("reason", json::s("not_locked")),
        ]);
    }
    let owned: Vec<i64> = rows
        .iter()
        .filter(|lock| lock.locked_by == agent_id)
        .map(|lock| lock.id)
        .collect();
    if owned.is_empty() {
        return json::obj(vec![
            ("released", json::b(false)),
            ("reason", json::s("not_owner")),
        ]);
    }
    let count = owned.len() as i64;
    state.locks.retain(|lock| !owned.contains(&lock.id));
    let worktree = project_root.map(|p| util::path_str(p));
    record_notification(state, &norm, "unlocked", agent_id, worktree);
    json::obj(vec![("released", json::b(true)), ("count", json::i(count))])
}

pub fn refresh(
    state: &mut State,
    document_path: &str,
    agent_id: &str,
    ttl: f64,
    region_start: Option<i64>,
    region_end: Option<i64>,
    project_root: Option<&Path>,
) -> Value {
    let norm = normalize_path(document_path, project_root);
    let now = util::now();
    if let Some(lock) = state.locks.iter_mut().find(|lock| {
        lock.document_path == norm
            && lock.locked_by == agent_id
            && lock.region_start == region_start
            && lock.region_end == region_end
    }) {
        if lock.locked_at + lock.lock_ttl <= now {
            return json::obj(vec![
                ("refreshed", json::b(false)),
                ("reason", json::s("not_locked")),
            ]);
        }
        lock.locked_at = now;
        lock.lock_ttl = ttl;
        return json::obj(vec![
            ("refreshed", json::b(true)),
            ("expires_at", json::f(now + ttl)),
        ]);
    }
    json::obj(vec![
        ("refreshed", json::b(false)),
        ("reason", json::s("not_locked")),
    ])
}

pub fn status(state: &State, document_path: &str, project_root: Option<&Path>) -> Value {
    let norm = normalize_path(document_path, project_root);
    let now = util::now();
    let active: Vec<&Lock> = state
        .locks
        .iter()
        .filter(|lock| lock.document_path == norm && lock_alive(lock, now))
        .collect();
    if active.is_empty() {
        return json::obj(vec![("locked", json::b(false))]);
    }
    let one = |lock: &Lock| {
        json::obj(vec![
            ("locked_by", json::s(&lock.locked_by)),
            ("locked_at", json::f(lock.locked_at)),
            ("expires_at", json::f(lock.locked_at + lock.lock_ttl)),
            ("lock_type", json::s(&lock.lock_type)),
            ("region_start", json::opt_i(lock.region_start)),
            ("region_end", json::opt_i(lock.region_end)),
            ("worktree", json::opt_string(&lock.worktree_root)),
        ])
    };
    if active.len() == 1 {
        let lock = active[0];
        return Value::Object(vec![
            ("locked".into(), json::b(true)),
            ("locked_by".into(), json::s(&lock.locked_by)),
            ("locked_at".into(), json::f(lock.locked_at)),
            ("expires_at".into(), json::f(lock.locked_at + lock.lock_ttl)),
            ("lock_type".into(), json::s(&lock.lock_type)),
            ("region_start".into(), json::opt_i(lock.region_start)),
            ("region_end".into(), json::opt_i(lock.region_end)),
            ("worktree".into(), json::opt_string(&lock.worktree_root)),
        ]);
    }
    json::obj(vec![
        ("locked", json::b(true)),
        ("holders", json::arr(active.into_iter().map(one).collect())),
    ])
}

pub fn list_locks(state: &State, agent_id: Option<&str>) -> Value {
    let now = util::now();
    let mut seen_paths: Vec<String> = Vec::new();
    for lock in &state.locks {
        if lock_alive(lock, now) && !seen_paths.iter().any(|p| p == &lock.document_path) {
            seen_paths.push(lock.document_path.clone());
        }
    }
    let mut locks = Vec::new();
    for path in seen_paths {
        for lock in state
            .locks
            .iter()
            .filter(|lock| lock.document_path == path && lock_alive(lock, now))
        {
            if agent_id.map(|id| lock.locked_by == id).unwrap_or(true) {
                locks.push(json::obj(vec![
                    ("document_path", json::s(&lock.document_path)),
                    ("locked_by", json::s(&lock.locked_by)),
                    ("locked_at", json::f(lock.locked_at)),
                    ("expires_at", json::f(lock.locked_at + lock.lock_ttl)),
                    ("lock_type", json::s(&lock.lock_type)),
                    ("region_start", json::opt_i(lock.region_start)),
                    ("region_end", json::opt_i(lock.region_end)),
                    ("worktree", json::opt_string(&lock.worktree_root)),
                ]));
            }
        }
    }
    let count = locks.len() as i64;
    json::obj(vec![("locks", json::arr(locks)), ("count", json::i(count))])
}

pub fn release_agent(state: &mut State, agent_id: &str) -> Value {
    let before = state.locks.len();
    state.locks.retain(|lock| lock.locked_by != agent_id);
    json::obj(vec![(
        "released",
        json::i((before - state.locks.len()) as i64),
    )])
}

pub fn reap_expired(state: &mut State, grace: f64) -> Value {
    let now = util::now();
    if grace > 0.0 {
        let fresh: Vec<String> = state
            .agents
            .iter()
            .filter(|agent| agent.status == "active" && agent.last_heartbeat > now - grace)
            .map(|agent| agent.agent_id.clone())
            .collect();
        for lock in &mut state.locks {
            if lock.locked_at + lock.lock_ttl < now && fresh.iter().any(|id| id == &lock.locked_by)
            {
                lock.locked_at = now;
                if lock.lock_ttl < grace {
                    lock.lock_ttl = grace;
                }
            }
        }
    }
    let before = state.locks.len();
    state
        .locks
        .retain(|lock| lock.locked_at + lock.lock_ttl >= now);
    json::obj(vec![(
        "reaped",
        json::i((before - state.locks.len()) as i64),
    )])
}

pub fn notify(
    state: &mut State,
    document_path: &str,
    change_type: &str,
    agent_id: &str,
    project_root: Option<&Path>,
) -> Value {
    let norm = normalize_path(document_path, project_root);
    let worktree = project_root.map(|p| util::path_str(p));
    let result = record_notification(state, &norm, change_type, agent_id, worktree);
    let created_at = result.get("created_at").and_then(Value::as_f64).unwrap_or(util::now());
    let notification_id = result.get("notification_id").and_then(Value::as_i64).unwrap_or(0);
    state.record_event(
        "notification.created",
        json::obj(vec![
            ("document_path", json::s(&norm)),
            ("change_type", json::s(change_type)),
            ("agent_id", json::s(agent_id)),
            ("created_at", json::f(created_at)),
            ("notification_id", json::i(notification_id)),
        ]),
    );
    result
}

pub fn prune_notifications(state: &mut State, max_age: Option<f64>, max_entries: Option<i64>) -> i64 {
    let now = util::now();
    let before = state.notifications.len() + state.hub_events.len();
    if let Some(age) = max_age {
        let cutoff = now - age;
        state.notifications.retain(|row| row.created_at >= cutoff);
        state.hub_events.retain(|row| row.created_at >= cutoff);
    }
    if let Some(max_entries) = max_entries {
        trim_oldest(&mut state.notifications, max_entries, |row| (row.created_at, row.id));
        trim_oldest(&mut state.hub_events, max_entries, |row| (row.created_at, row.id));
    }
    (before - state.notifications.len() - state.hub_events.len()) as i64
}

fn trim_oldest<T>(rows: &mut Vec<T>, max_entries: i64, key: impl Fn(&T) -> (f64, i64)) {
    if max_entries < 0 || rows.len() as i64 <= max_entries {
        return;
    }
    let mut order: Vec<usize> = (0..rows.len()).collect();
    order.sort_by(|&a, &b| {
        let (at, aid) = key(&rows[a]);
        let (bt, bid) = key(&rows[b]);
        at.partial_cmp(&bt)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then(aid.cmp(&bid))
    });
    let excess = rows.len() - max_entries as usize;
    let drop: Vec<usize> = order.into_iter().take(excess).collect();
    let mut keep = vec![true; rows.len()];
    for index in drop {
        keep[index] = false;
    }
    let mut index = 0;
    rows.retain(|_| {
        let ok = keep[index];
        index += 1;
        ok
    });
}

pub fn notifications(
    state: &State,
    since: Option<f64>,
    exclude: Option<&str>,
    limit: i64,
) -> Value {
    let mut rows: Vec<&Notification> = state
        .notifications
        .iter()
        .filter(|row| since.map(|cut| row.created_at > cut).unwrap_or(true))
        .filter(|row| exclude.map(|id| row.agent_id != id).unwrap_or(true))
        .collect();
    rows.sort_by(|a, b| b.id.cmp(&a.id));
    if limit >= 0 {
        rows.truncate(limit as usize);
    }
    let items = rows
        .into_iter()
        .map(|row| {
            json::obj(vec![
                ("id", json::i(row.id)),
                ("document_path", json::s(&row.document_path)),
                ("change_type", json::s(&row.change_type)),
                ("agent_id", json::s(&row.agent_id)),
                ("worktree_root", json::opt_string(&row.worktree_root)),
                ("created_at", json::f(row.created_at)),
            ])
        })
        .collect::<Vec<_>>();
    json::obj(vec![("notifications", json::arr(items))])
}

pub fn conflicts(
    state: &State,
    document_path: Option<&str>,
    agent_id: Option<&str>,
    limit: i64,
    project_root: Option<&Path>,
) -> Value {
    let norm = document_path.map(|path| normalize_path(path, project_root));
    let mut rows: Vec<&Conflict> = state
        .conflicts
        .iter()
        .filter(|row| {
            norm.as_ref()
                .map(|path| row.document_path == *path)
                .unwrap_or(true)
        })
        .filter(|row| {
            agent_id
                .map(|id| row.agent_a == id || row.agent_b == id)
                .unwrap_or(true)
        })
        .collect();
    rows.sort_by(|a, b| {
        b.created_at
            .partial_cmp(&a.created_at)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    rows.truncate(limit.max(0) as usize);
    let items = rows
        .into_iter()
        .map(|row| {
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
        })
        .collect::<Vec<_>>();
    json::obj(vec![("conflicts", json::arr(items))])
}

pub fn hotspots(state: &State, limit: i64) -> Value {
    let mut paths: Vec<String> = Vec::new();
    for row in &state.conflicts {
        if !paths.iter().any(|path| path == &row.document_path) {
            paths.push(row.document_path.clone());
        }
    }
    let mut ranked: Vec<(String, i64, Vec<String>, i64)> = paths
        .into_iter()
        .map(|path| {
            let rows: Vec<&Conflict> = state
                .conflicts
                .iter()
                .filter(|row| row.document_path == path)
                .collect();
            let mut agents: Vec<String> = Vec::new();
            for row in &rows {
                if !agents.iter().any(|id| id == &row.agent_a) {
                    agents.push(row.agent_a.clone());
                }
                if !agents.iter().any(|id| id == &row.agent_b) {
                    agents.push(row.agent_b.clone());
                }
            }
            agents.sort();
            let first_id = rows.iter().map(|row| row.id).min().unwrap_or(0);
            let count = rows.len() as i64;
            (path, count, agents, first_id)
        })
        .collect();
    ranked.sort_by(|a, b| b.1.cmp(&a.1).then(a.3.cmp(&b.3)));
    ranked.truncate(limit.max(0) as usize);
    let items = ranked
        .into_iter()
        .map(|(path, count, agents, _)| {
            json::obj(vec![
                ("document_path", json::s(path)),
                ("conflict_count", json::i(count)),
                ("agents_involved", json::string_list(&agents)),
            ])
        })
        .collect::<Vec<_>>();
    let total = items.len() as i64;
    json::obj(vec![
        ("hotspots", json::arr(items)),
        ("total", json::i(total)),
    ])
}

pub fn default_ttl() -> f64 {
    DEFAULT_TTL
}
