//! Document locks, conflict log, and contention.

use crate::call_id::{norm, release_locks_of, reap_stale};
use crate::json::Json;
use crate::model::*;
use crate::util::{
    arg_bool, arg_f64, arg_i64, arg_opt_i64, arg_str, arg_string, obj, regions_overlap,
};
use std::path::Path;

fn worktree(root: &Path) -> String {
    root.to_string_lossy().replace('\\', "/")
}

pub fn acquire(st: &mut State, root: &Path, args: &Json) -> Json {
    let path_in = arg_string(args, "document_path").unwrap_or_default();
    let agent_id = arg_string(args, "agent_id").unwrap_or_default();
    let lock_type = arg_str(args, "lock_type").unwrap_or("exclusive").to_string();
    let ttl = arg_f64(args, "ttl", 300.0);
    let force = arg_bool(args, "force", false);
    let region_start = arg_opt_i64(args, "region_start");
    let region_end = arg_opt_i64(args, "region_end");
    let retry = arg_bool(args, "retry", false);
    let max_retries = arg_i64(args, "max_retries", 5);
    let norm_path = norm(root, &path_in);
    let wt = worktree(root);
    let mut attempt = 0i64;
    loop {
        let t = now();
        if let Some(own) = st.locks.iter().position(|l| {
            l.document_path == norm_path
                && l.locked_by == agent_id
                && l.region_start == region_start
                && l.region_end == region_end
                && (l.worktree_root.is_none() || l.worktree_root.as_deref() == Some(wt.as_str()))
        }) {
            let id = st.locks[own].id;
            st.locks[own].locked_at = t;
            st.locks[own].lock_ttl = ttl;
            st.locks[own].lock_type = lock_type.clone();
            let _ = id;
            return obj(vec![
                ("acquired", Json::Bool(true)),
                ("document_path", Json::String(norm_path)),
                ("locked_by", Json::String(agent_id)),
                ("expires_at", Json::Float(t + ttl)),
                ("region_start", opt_i64(&region_start)),
                ("region_end", opt_i64(&region_end)),
                ("attempts", Json::Int(attempt + 1)),
            ]);
        }
        let conflicts: Vec<Lock> = st
            .locks
            .iter()
            .filter(|l| {
                l.document_path == norm_path
                    && l.alive(t)
                    && l.locked_by != agent_id
                    && !(l.lock_type == "shared" && lock_type == "shared")
                    && regions_overlap(l.region_start, l.region_end, region_start, region_end)
                    && (l.worktree_root.is_none() || l.worktree_root.as_deref() == Some(wt.as_str()))
            })
            .cloned()
            .collect();
        if !conflicts.is_empty() && !force {
            for c in &conflicts {
                record_conflict(st, &norm_path, &c.locked_by, &agent_id, "denied", "rejected", None);
            }
            let first = &conflicts[0];
            if retry && attempt < max_retries {
                attempt += 1;
                continue;
            }
            let conflict_json = conflicts
                .iter()
                .map(|c| {
                    obj(vec![
                        ("locked_by", Json::String(c.locked_by.clone())),
                        ("region_start", opt_i64(&c.region_start)),
                        ("region_end", opt_i64(&c.region_end)),
                    ])
                })
                .collect();
            return obj(vec![
                ("acquired", Json::Bool(false)),
                ("locked_by", Json::String(first.locked_by.clone())),
                ("locked_at", Json::Float(first.locked_at)),
                ("expires_at", Json::Float(first.locked_at + first.lock_ttl)),
                ("worktree", opt_string(&first.worktree_root)),
                ("conflicts", Json::Array(conflict_json)),
                ("attempt", Json::Int(attempt + 1)),
            ]);
        }
        if !conflicts.is_empty() && force {
            for c in &conflicts {
                record_conflict(
                    st,
                    &norm_path,
                    &c.locked_by,
                    &agent_id,
                    "lock_stolen",
                    "force_overwritten",
                    None,
                );
                st.locks.retain(|l| l.id != c.id);
            }
        }
        if let Some(violation) = scope_violation(st, root, &norm_path, &agent_id) {
            return obj(vec![
                ("acquired", Json::Bool(false)),
                ("error", Json::String("scope_violation".into())),
                ("scope_violation", violation),
                ("attempts", Json::Int(attempt + 1)),
            ]);
        }
        let id = st.alloc("lock");
        st.locks.push(Lock {
            id,
            document_path: norm_path.clone(),
            locked_by: agent_id.clone(),
            locked_at: t,
            lock_ttl: ttl,
            lock_type: lock_type.clone(),
            region_start,
            region_end,
            worktree_root: Some(wt.clone()),
        });
        notify(st, root, &norm_path, "locked", &agent_id);
        let mut result = vec![
            ("acquired", Json::Bool(true)),
            ("document_path", Json::String(norm_path.clone())),
            ("locked_by", Json::String(agent_id.clone())),
            ("expires_at", Json::Float(t + ttl)),
            ("region_start", opt_i64(&region_start)),
            ("region_end", opt_i64(&region_end)),
            ("attempts", Json::Int(attempt + 1)),
        ];
        if let Some(warn) = ownership_warning(st, root, &norm_path, &agent_id) {
            result.push(("ownership_warning", warn));
        }
        if let Some(prox) = proximity(st, &norm_path, &agent_id) {
            result.push(("proximity_warning", prox));
        }
        return Json::object(result);
    }
}

fn scope_violation(st: &State, root: &Path, norm_path: &str, agent_id: &str) -> Option<Json> {
    let resp = st.responsibilities.iter().find(|r| r.agent_id == agent_id)?;
    let scope = resp.scope.as_ref()?;
    let parsed = crate::json::parse(scope).ok()?;
    let paths = parsed.as_array()?;
    if paths.is_empty() {
        return None;
    }
    let declared: Vec<String> = paths.iter().filter_map(|p| p.as_str().map(|s| s.to_string())).collect();
    if declared.is_empty() {
        return None;
    }
    for prefix in &declared {
        let norm_scope = norm(root, prefix).trim_end_matches('/').to_string();
        if norm_path == norm_scope || norm_path.starts_with(&(norm_scope + "/")) {
            return None;
        }
    }
    Some(obj(vec![
        (
            "declared_scope",
            Json::Array(declared.iter().cloned().map(Json::String).collect()),
        ),
        ("attempted_path", Json::String(norm_path.to_string())),
        (
            "message",
            Json::String(format!(
                "Agent {agent_id} declared scope {declared:?} but attempted to lock {norm_path}"
            )),
        ),
    ]))
}

fn ownership_warning(st: &mut State, root: &Path, norm_path: &str, agent_id: &str) -> Option<Json> {
    let owner = st
        .ownership
        .iter()
        .find(|o| o.document_path == norm_path)
        .map(|o| o.assigned_agent_id.clone())?;
    if owner == agent_id {
        return None;
    }
    let msg = format!("Agent {agent_id} locked file owned by {owner}");
    record_conflict(
        st,
        norm_path,
        &owner,
        agent_id,
        "boundary_crossing",
        "allowed",
        Some(format!("{{\"message\": {}}}", crate::json::Json::String(msg.clone()).dump())),
    );
    notify(st, root, norm_path, "boundary_crossing", agent_id);
    Some(obj(vec![
        ("owned_by", Json::String(owner.clone())),
        (
            "message",
            Json::String(format!("File is assigned to {owner} in file_ownership")),
        ),
    ]))
}

fn proximity(st: &State, norm_path: &str, agent_id: &str) -> Option<Json> {
    let t = now();
    let conflicts: Vec<&Intent> = st
        .intents
        .iter()
        .filter(|i| i.document_path == norm_path && i.agent_id != agent_id && i.alive(t))
        .collect();
    if conflicts.is_empty() {
        return None;
    }
    let ids: Vec<String> = conflicts.iter().map(|c| c.agent_id.clone()).collect();
    let listed = format!("{ids:?}").replace('"', "'");
    Some(obj(vec![
        (
            "conflicting_agents",
            Json::Array(
                conflicts
                    .iter()
                    .map(|c| {
                        obj(vec![
                            ("agent_id", Json::String(c.agent_id.clone())),
                            ("intent", Json::String(c.intent.clone())),
                        ])
                    })
                    .collect(),
            ),
        ),
        (
            "message",
            Json::String(format!(
                "Agents {listed} have declared intent to work on {norm_path}"
            )),
        ),
    ]))
}

pub fn record_conflict(
    st: &mut State,
    path: &str,
    a: &str,
    b: &str,
    kind: &str,
    resolution: &str,
    details: Option<String>,
) {
    let id = st.alloc("conflict");
    st.conflicts.push(Conflict {
        id,
        document_path: path.to_string(),
        agent_a: a.to_string(),
        agent_b: b.to_string(),
        conflict_type: kind.to_string(),
        resolution: resolution.to_string(),
        details_json: details,
        created_at: now(),
    });
}

pub fn notify(st: &mut State, root: &Path, path: &str, change_type: &str, agent_id: &str) -> Json {
    let id = st.alloc("notif");
    let t = now();
    st.notifications.push(Notification {
        id,
        document_path: path.to_string(),
        change_type: change_type.to_string(),
        agent_id: agent_id.to_string(),
        worktree_root: Some(worktree(root)),
        created_at: t,
    });
    obj(vec![
        ("recorded", Json::Bool(true)),
        ("notification_id", Json::Int(id)),
        ("created_at", Json::Float(t)),
    ])
}

pub fn release(st: &mut State, root: &Path, args: &Json) -> Json {
    let path_in = arg_string(args, "document_path").unwrap_or_default();
    let agent_id = arg_string(args, "agent_id").unwrap_or_default();
    let region_start = arg_opt_i64(args, "region_start");
    let region_end = arg_opt_i64(args, "region_end");
    let norm_path = norm(root, &path_in);
    let rows: Vec<&Lock> = st
        .locks
        .iter()
        .filter(|l| {
            l.document_path == norm_path
                && if region_start.is_some() {
                    l.region_start == region_start && l.region_end == region_end
                } else {
                    l.region_start.is_none()
                }
        })
        .collect();
    if rows.is_empty() {
        if region_start.is_none() {
            let region_locks: Vec<Json> = st
                .locks
                .iter()
                .filter(|l| l.document_path == norm_path && l.locked_by == agent_id && l.region_start.is_some())
                .map(|l| {
                    obj(vec![
                        ("region_start", opt_i64(&l.region_start)),
                        ("region_end", opt_i64(&l.region_end)),
                    ])
                })
                .collect();
            if !region_locks.is_empty() {
                return obj(vec![
                    ("released", Json::Bool(false)),
                    ("reason", Json::String("region_required".into())),
                    ("region_locks", Json::Array(region_locks)),
                ]);
            }
        }
        return obj(vec![
            ("released", Json::Bool(false)),
            ("reason", Json::String("not_locked".into())),
        ]);
    }
    let owned: Vec<i64> = rows
        .iter()
        .filter(|l| l.locked_by == agent_id)
        .map(|l| l.id)
        .collect();
    if owned.is_empty() {
        return obj(vec![
            ("released", Json::Bool(false)),
            ("reason", Json::String("not_owner".into())),
        ]);
    }
    let count = owned.len() as i64;
    st.locks.retain(|l| !owned.contains(&l.id));
    notify(st, root, &norm_path, "unlocked", &agent_id);
    obj(vec![
        ("released", Json::Bool(true)),
        ("count", Json::Int(count)),
    ])
}

pub fn refresh(st: &mut State, root: &Path, args: &Json) -> Json {
    let path_in = arg_string(args, "document_path").unwrap_or_default();
    let agent_id = arg_string(args, "agent_id").unwrap_or_default();
    let ttl = arg_opt_f64_present(args);
    let region_start = arg_opt_i64(args, "region_start");
    let region_end = arg_opt_i64(args, "region_end");
    let norm_path = norm(root, &path_in);
    let t = now();
    let idx = st.locks.iter().position(|l| {
        l.document_path == norm_path
            && if region_start.is_some() {
                l.region_start == region_start && l.region_end == region_end
            } else {
                l.region_start.is_none()
            }
    });
    let Some(idx) = idx else {
        return obj(vec![
            ("refreshed", Json::Bool(false)),
            ("reason", Json::String("not_locked".into())),
        ]);
    };
    if st.locks[idx].locked_by != agent_id {
        return obj(vec![
            ("refreshed", Json::Bool(false)),
            ("reason", Json::String("not_owner".into())),
        ]);
    }
    if !st.locks[idx].alive(t) {
        return obj(vec![
            ("refreshed", Json::Bool(false)),
            ("reason", Json::String("expired".into())),
        ]);
    }
    let new_ttl = ttl.unwrap_or(st.locks[idx].lock_ttl);
    st.locks[idx].locked_at = t;
    st.locks[idx].lock_ttl = new_ttl;
    obj(vec![
        ("refreshed", Json::Bool(true)),
        ("expires_at", Json::Float(t + new_ttl)),
    ])
}

fn arg_opt_f64_present(args: &Json) -> Option<f64> {
    match args.get("ttl") {
        None | Some(Json::Null) => None,
        Some(v) => v.as_f64(),
    }
}

pub fn lock_status(st: &State, root: &Path, args: &Json) -> Json {
    let path_in = arg_string(args, "document_path").unwrap_or_default();
    let norm_path = norm(root, &path_in);
    status_for(st, &norm_path)
}

pub fn status_for(st: &State, norm_path: &str) -> Json {
    let t = now();
    let active: Vec<&Lock> = st
        .locks
        .iter()
        .filter(|l| l.document_path == norm_path && l.alive(t))
        .collect();
    if active.is_empty() {
        return obj(vec![("locked", Json::Bool(false))]);
    }
    let one = |e: &Lock| {
        obj(vec![
            ("locked_by", Json::String(e.locked_by.clone())),
            ("locked_at", Json::Float(e.locked_at)),
            ("expires_at", Json::Float(e.locked_at + e.lock_ttl)),
            ("lock_type", Json::String(e.lock_type.clone())),
            ("region_start", opt_i64(&e.region_start)),
            ("region_end", opt_i64(&e.region_end)),
            ("worktree", opt_string(&e.worktree_root)),
        ])
    };
    if active.len() == 1 {
        let mut pairs = vec![("locked", Json::Bool(true))];
        if let Json::Object(rest) = one(active[0]) {
            pairs.extend(rest.into_iter().map(|(k, v)| {
                // leak key into static? can't. rebuild.
                let _ = k;
                let _ = v;
                ("locked", Json::Bool(true))
            }));
        }
        return merge_locked(one(active[0]));
    }
    obj(vec![
        ("locked", Json::Bool(true)),
        (
            "holders",
            Json::Array(active.into_iter().map(one).collect()),
        ),
    ])
}

fn merge_locked(inner: Json) -> Json {
    let mut pairs = vec![("locked".to_string(), Json::Bool(true))];
    if let Json::Object(rest) = inner {
        pairs.extend(rest);
    }
    Json::Object(pairs)
}

pub fn list_locks(st: &State, args: &Json) -> Json {
    let agent = arg_string(args, "agent_id");
    let t = now();
    let locks: Vec<Json> = st
        .locks
        .iter()
        .filter(|l| l.alive(t) && agent.as_ref().map(|a| l.locked_by == *a).unwrap_or(true))
        .map(|e| {
            obj(vec![
                ("document_path", Json::String(e.document_path.clone())),
                ("locked_by", Json::String(e.locked_by.clone())),
                ("locked_at", Json::Float(e.locked_at)),
                ("expires_at", Json::Float(e.locked_at + e.lock_ttl)),
                ("lock_type", Json::String(e.lock_type.clone())),
                ("region_start", opt_i64(&e.region_start)),
                ("region_end", opt_i64(&e.region_end)),
                ("worktree", opt_string(&e.worktree_root)),
            ])
        })
        .collect();
    let count = locks.len() as i64;
    obj(vec![
        ("locks", Json::Array(locks)),
        ("count", Json::Int(count)),
    ])
}

pub fn admin(st: &mut State, args: &Json) -> Json {
    let action = arg_str(args, "action").unwrap_or("");
    match action {
        "release_by_agent" => {
            let Some(agent_id) = arg_string(args, "agent_id") else {
                return obj(vec![(
                    "error",
                    Json::String("agent_id is required for release_by_agent".into()),
                )]);
            };
            let n = release_locks_of(st, &agent_id);
            obj(vec![("released", Json::Int(n))])
        }
        "reap_expired" => {
            let grace = arg_f64(args, "grace_seconds", 0.0);
            reap_expired(st, grace)
        }
        "reap_stale" => {
            let timeout = arg_f64(args, "timeout", 600.0);
            let mut result = reap_stale(st, timeout);
            let stopped: Vec<String> = st
                .agents
                .iter()
                .filter(|a| a.status == "stopped")
                .map(|a| a.agent_id.clone())
                .collect();
            let before = st.locks.len();
            st.locks.retain(|l| !stopped.iter().any(|s| s == &l.locked_by));
            let released = (before - st.locks.len()) as i64;
            if let Json::Object(pairs) = &mut result {
                pairs.push(("locks_released".into(), Json::Int(released)));
            }
            result
        }
        other => obj(vec![(
            "error",
            Json::String(format!("Unknown action: {other}")),
        )]),
    }
}

pub fn reap_expired(st: &mut State, grace: f64) -> Json {
    let t = now();
    if grace > 0.0 {
        let fresh: Vec<String> = st
            .agents
            .iter()
            .filter(|a| a.status == "active" && a.last_heartbeat > t - grace)
            .map(|a| a.agent_id.clone())
            .collect();
        for l in st.locks.iter_mut() {
            if !l.alive(t) && fresh.iter().any(|a| a == &l.locked_by) {
                l.locked_at = t;
                if l.lock_ttl < grace {
                    l.lock_ttl = grace;
                }
            }
        }
    }
    let before = st.locks.len();
    st.locks.retain(|l| l.alive(t));
    obj(vec![("reaped", Json::Int((before - st.locks.len()) as i64))])
}

pub fn conflicts(st: &State, root: &Path, args: &Json) -> Json {
    let path = arg_string(args, "document_path").map(|p| norm(root, &p));
    let agent = arg_string(args, "agent_id");
    let limit = arg_i64(args, "limit", 20) as usize;
    let mut rows: Vec<&Conflict> = st
        .conflicts
        .iter()
        .filter(|c| path.as_ref().map(|p| c.document_path == *p).unwrap_or(true))
        .filter(|c| {
            agent
                .as_ref()
                .map(|a| c.agent_a == *a || c.agent_b == *a)
                .unwrap_or(true)
        })
        .collect();
    rows.sort_by(|a, b| b.created_at.partial_cmp(&a.created_at).unwrap_or(std::cmp::Ordering::Equal));
    rows.truncate(limit);
    obj(vec![(
        "conflicts",
        Json::Array(rows.into_iter().map(|c| c.to_json()).collect()),
    )])
}

pub fn hotspots(st: &State, args: &Json) -> Json {
    let limit = arg_i64(args, "limit", 10) as usize;
    let mut paths: Vec<String> = st.conflicts.iter().map(|c| c.document_path.clone()).collect();
    paths.sort();
    paths.dedup();
    let mut rows: Vec<(String, i64, Vec<String>)> = paths
        .into_iter()
        .map(|p| {
            let matches: Vec<&Conflict> = st.conflicts.iter().filter(|c| c.document_path == p).collect();
            let mut agents: Vec<String> = matches
                .iter()
                .flat_map(|c| [c.agent_a.clone(), c.agent_b.clone()])
                .collect();
            agents.sort();
            agents.dedup();
            (p, matches.len() as i64, agents)
        })
        .collect();
    rows.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));
    rows.truncate(limit);
    let hotspots: Vec<Json> = rows
        .into_iter()
        .map(|(p, n, agents)| {
            obj(vec![
                ("document_path", Json::String(p)),
                ("conflict_count", Json::Int(n)),
                (
                    "agents_involved",
                    Json::Array(agents.into_iter().map(Json::String).collect()),
                ),
            ])
        })
        .collect();
    let total = hotspots.len() as i64;
    obj(vec![
        ("hotspots", Json::Array(hotspots)),
        ("total", Json::Int(total)),
    ])
}

pub fn notify_change(st: &mut State, root: &Path, args: &Json) -> Json {
    let path = arg_string(args, "document_path").unwrap_or_default();
    let change = arg_string(args, "change_type").unwrap_or_default();
    let agent = arg_string(args, "agent_id").unwrap_or_default();
    let norm_path = norm(root, &path);
    notify(st, root, &norm_path, &change, &agent)
}
