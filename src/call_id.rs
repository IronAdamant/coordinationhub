//! Agent identity, lineage, and the registration context bundle.

use crate::json::Json;
use crate::model::*;
use crate::paths::normalize_path;
use crate::util::{arg_bool, arg_f64, arg_str, arg_string, arr_json, obj, truncate};
use crate::util::env_i64;
use std::path::Path;

pub fn coordination_url() -> String {
    std::env::var("COORDINATIONHUB_COORDINATION_URL")
        .unwrap_or_else(|_| "http://localhost:9877".into())
}

pub fn register(st: &mut State, root: &Path, args: &Json) -> Json {
    let agent_id = arg_string(args, "agent_id").unwrap_or_default();
    let parent_id = arg_string(args, "parent_id");
    let graph_agent_id = arg_string(args, "graph_agent_id");
    let worktree = arg_string(args, "worktree_root")
        .unwrap_or_else(|| root.to_string_lossy().replace('\\', "/"));
    let raw_ide = arg_string(args, "raw_ide_id");
    let ide_vendor = arg_string(args, "ide_vendor");
    let pid = std::process::id();
    let t = now();
    if let Some(existing) = st.agent(&agent_id).cloned() {
        if existing.status == "active" && existing.pid != pid {
            return obj(vec![
                ("registered", Json::Bool(false)),
                ("agent_id", Json::String(agent_id)),
                ("reason", Json::String("collision".into())),
                ("existing_pid", Json::Int(existing.pid as i64)),
            ]);
        }
    } else {
        let active = st.agents.iter().filter(|a| a.status == "active").count();
        if active >= 10_000 {
            return obj(vec![
                ("registered", Json::Bool(false)),
                ("agent_id", Json::String(agent_id)),
                ("reason", Json::String("max_agents_reached".into())),
                ("max_agents", Json::Int(10_000)),
                ("active_count", Json::Int(active as i64)),
            ]);
        }
    }
    if let Some(agent) = st.agent_mut(&agent_id) {
        agent.parent_id = parent_id.clone();
        agent.worktree_root = worktree.clone();
        agent.pid = pid;
        agent.last_heartbeat = t;
        agent.status = "active".into();
        if raw_ide.is_some() {
            agent.raw_ide_id = raw_ide;
        }
        if ide_vendor.is_some() {
            agent.ide_vendor = ide_vendor;
        }
    } else {
        st.agents.push(Agent {
            agent_id: agent_id.clone(),
            parent_id: parent_id.clone(),
            worktree_root: worktree.clone(),
            pid,
            started_at: t,
            last_heartbeat: t,
            status: "active".into(),
            raw_ide_id: raw_ide,
            ide_vendor,
            stop_requested_at: None,
        });
    }
    if let Some(parent) = &parent_id {
        record_descendants(st, &agent_id, parent, t);
        if !st
            .lineage
            .iter()
            .any(|l| l.parent_id == *parent && l.child_id == agent_id)
        {
            st.lineage.push(Lineage {
                parent_id: parent.clone(),
                child_id: agent_id.clone(),
                spawned_at: t,
            });
        }
    }
    if let Some(gid) = graph_agent_id {
        if let Some(resp) = st.responsibilities.iter_mut().find(|r| r.agent_id == agent_id) {
            resp.graph_agent_id = Some(gid);
            resp.updated_at = t;
        }
    }
    context_bundle(st, root, &agent_id, parent_id.as_deref())
}

fn record_descendants(st: &mut State, agent_id: &str, parent_id: &str, t: f64) {
    let mut stack = vec![(parent_id.to_string(), 1i64)];
    let mut seen = Vec::new();
    while let Some((ancestor, depth)) = stack.pop() {
        if seen.iter().any(|s: &String| s == &ancestor) {
            continue;
        }
        seen.push(ancestor.clone());
        if !st.descendants.iter().any(|d| {
            d.ancestor_id == ancestor && d.descendant_id == agent_id
        }) {
            st.descendants.push(Descendant {
                ancestor_id: ancestor.clone(),
                descendant_id: agent_id.to_string(),
                depth,
                registered_at: t,
            });
        }
        if let Some(a) = st.agent(&ancestor) {
            if let Some(p) = a.parent_id.clone() {
                stack.push((p, depth + 1));
            }
        }
    }
}

pub fn context_bundle(st: &State, root: &Path, agent_id: &str, parent_id: Option<&str>) -> Json {
    let t = now();
    let agents = list_agent_rows(st, true, 600.0, false);
    let registered = agents
        .iter()
        .filter_map(|a| {
            let id = a.get("agent_id")?.as_str()?.to_string();
            let status = a.get("status")?.as_str()?.to_string();
            let hb = a.get("last_heartbeat")?.as_f64()?;
            Some(obj(vec![
                ("agent_id", Json::String(id)),
                ("status", Json::String(status)),
                ("last_heartbeat", Json::Float(hb)),
            ]))
        })
        .collect();
    let locks: Vec<Json> = st
        .locks
        .iter()
        .filter(|l| l.alive(t))
        .map(|l| {
            obj(vec![
                ("document_path", Json::String(l.document_path.clone())),
                ("locked_by", Json::String(l.locked_by.clone())),
                ("expires_at", Json::Float(l.locked_at + l.lock_ttl)),
            ])
        })
        .collect();
    let notifs: Vec<Json> = st
        .notifications
        .iter()
        .filter(|n| n.created_at > t - 300.0)
        .rev()
        .take(20)
        .map(|n| {
            obj(vec![
                ("document_path", Json::String(n.document_path.clone())),
                ("change_type", Json::String(n.change_type.clone())),
                ("agent_id", Json::String(n.agent_id.clone())),
                ("created_at", Json::Float(n.created_at)),
            ])
        })
        .collect();
    let resp = st.responsibilities.iter().find(|r| r.agent_id == agent_id);
    let mut bundle = vec![
        ("agent_id", Json::String(agent_id.to_string())),
        (
            "parent_id",
            match parent_id {
                Some(p) => Json::String(p.to_string()),
                None => Json::Null,
            },
        ),
        (
            "worktree_root",
            Json::String(root.to_string_lossy().replace('\\', "/")),
        ),
        ("registered_agents", Json::Array(registered)),
        ("active_locks", Json::Array(locks)),
        ("pending_notifications", Json::Array(notifs)),
        ("coordination_url", Json::String(coordination_url())),
        ("graph_loaded", Json::Bool(st.graph_loaded)),
    ];
    if let Some(r) = resp {
        let responsibilities = crate::json::parse(&r.responsibilities).unwrap_or(Json::Array(vec![]));
        bundle.push(("responsibilities", responsibilities));
        bundle.push(("role", opt_string(&r.role)));
        bundle.push(("graph_agent_id", opt_string(&r.graph_agent_id)));
        bundle.push(("current_task", opt_string(&r.current_task)));
    }
    let owned: Vec<Json> = st
        .ownership
        .iter()
        .filter(|o| o.assigned_agent_id == agent_id)
        .map(|o| Json::String(o.document_path.clone()))
        .collect();
    if !owned.is_empty() {
        bundle.push(("owned_files", Json::Array(owned)));
    }
    bundle.push(("descendants_status", Json::Array(descendants_status(st, agent_id))));
    Json::object(bundle)
}

pub fn descendants_status(st: &State, ancestor: &str) -> Vec<Json> {
    let mut rows: Vec<&Descendant> = st
        .descendants
        .iter()
        .filter(|d| d.ancestor_id == ancestor)
        .collect();
    rows.sort_by(|a, b| a.depth.cmp(&b.depth).then(a.descendant_id.cmp(&b.descendant_id)));
    rows.into_iter()
        .filter_map(|d| {
            let agent = st.agent(&d.descendant_id)?;
            let task = st
                .responsibilities
                .iter()
                .find(|r| r.agent_id == d.descendant_id)
                .and_then(|r| r.current_task.clone());
            Some(obj(vec![
                ("depth", Json::Int(d.depth)),
                ("agent_id", Json::String(d.descendant_id.clone())),
                ("status", Json::String(agent.status.clone())),
                ("last_heartbeat", Json::Float(agent.last_heartbeat)),
                ("current_task", opt_string(&task)),
            ]))
        })
        .collect()
}

pub fn heartbeat(st: &mut State, args: &Json) -> Json {
    let agent_id = arg_string(args, "agent_id").unwrap_or_default();
    let t = now();
    let reason = if let Some(a) = st.agent_mut(&agent_id) {
        if a.status == "active" {
            a.last_heartbeat = t;
            None
        } else {
            Some(format!("agent_{}", a.status))
        }
    } else {
        Some("not_registered".into())
    };
    refresh_claims_for(st, &agent_id, t);
    let mut out = vec![
        ("updated", Json::Bool(reason.is_none())),
        ("next_heartbeat_in", Json::Int(30)),
    ];
    if let Some(r) = reason {
        out.push(("reason", Json::String(r)));
    }
    Json::object(out)
}

fn refresh_claims_for(st: &mut State, agent_id: &str, t: f64) {
    for c in st.claims.iter_mut() {
        if c.agent_id == agent_id {
            c.claimed_at = t;
        }
    }
}

pub fn deregister(st: &mut State, args: &Json) -> Json {
    let agent_id = arg_string(args, "agent_id").unwrap_or_default();
    let parent = st.agent(&agent_id).and_then(|a| a.parent_id.clone());
    let effective = live_ancestor(st, parent.as_deref(), &agent_id);
    if let Some(a) = st.agent_mut(&agent_id) {
        a.status = "stopped".into();
    }
    let children: Vec<String> = st
        .agents
        .iter()
        .filter(|a| a.parent_id.as_deref() == Some(agent_id.as_str()) && a.status == "active")
        .map(|a| a.agent_id.clone())
        .collect();
    let orphaned = children.len() as i64;
    for child in &children {
        if let Some(a) = st.agent_mut(child) {
            a.parent_id = effective.clone();
        }
        st.lineage
            .retain(|l| !(l.parent_id == agent_id && l.child_id == *child));
    }
    let released = release_locks_of(st, &agent_id);
    let lease_released = release_lease_if_holder(st, &agent_id);
    st.claims.retain(|c| c.agent_id != agent_id);
    obj(vec![
        ("deregistered", Json::Bool(true)),
        ("children_orphaned", Json::Int(orphaned)),
        ("locks_released", Json::Int(released)),
        ("lease_released", Json::Bool(lease_released)),
    ])
}

fn live_ancestor(st: &State, start: Option<&str>, self_id: &str) -> Option<String> {
    let mut cursor = start.map(|s| s.to_string());
    let mut seen = vec![self_id.to_string()];
    while let Some(id) = cursor.clone() {
        if seen.iter().any(|s| s == &id) {
            break;
        }
        seen.push(id.clone());
        match st.agent(&id) {
            Some(a) if a.status == "active" => return Some(id),
            Some(a) => cursor = a.parent_id.clone(),
            None => break,
        }
    }
    None
}

pub fn release_locks_of(st: &mut State, agent_id: &str) -> i64 {
    let before = st.locks.len();
    st.locks.retain(|l| l.locked_by != agent_id);
    (before - st.locks.len()) as i64
}

fn release_lease_if_holder(st: &mut State, agent_id: &str) -> bool {
    if st.lease.as_ref().map(|l| l.holder_id == agent_id).unwrap_or(false) {
        st.lease = None;
        true
    } else {
        false
    }
}

pub fn list_agents(st: &State, args: &Json) -> Json {
    let active_only = arg_bool(args, "active_only", true);
    let stale_timeout = arg_f64(args, "stale_timeout", 600.0);
    let include_stale = arg_bool(args, "include_stale", false);
    let rows = list_agent_rows(st, active_only, stale_timeout, include_stale);
    obj(vec![("agents", Json::Array(rows))])
}

pub fn list_agent_rows(st: &State, active_only: bool, stale_timeout: f64, include_stale: bool) -> Vec<Json> {
    let t = now();
    let filter_stale = active_only && !include_stale;
    let mut rows: Vec<&Agent> = st
        .agents
        .iter()
        .filter(|a| {
            if active_only && a.status != "active" {
                return false;
            }
            if filter_stale && a.last_heartbeat < t - stale_timeout {
                return false;
            }
            true
        })
        .collect();
    rows.sort_by(|a, b| {
        b.last_heartbeat
            .partial_cmp(&a.last_heartbeat)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    rows.into_iter()
        .map(|a| a.to_json((t - a.last_heartbeat) > stale_timeout))
        .collect()
}

pub fn relations(st: &State, args: &Json) -> Json {
    let agent_id = arg_string(args, "agent_id").unwrap_or_default();
    let mode = arg_str(args, "mode").unwrap_or("lineage");
    if mode == "siblings" {
        let siblings = siblings_of(st, &agent_id);
        return obj(vec![
            ("mode", Json::String("siblings".into())),
            ("agent_id", Json::String(agent_id)),
            ("siblings", Json::Array(siblings)),
        ]);
    }
    lineage(st, &agent_id)
}

pub fn siblings_of(st: &State, agent_id: &str) -> Vec<Json> {
    let parent = st.agent(agent_id).and_then(|a| a.parent_id.clone());
    let Some(parent) = parent else {
        return Vec::new();
    };
    st.agents
        .iter()
        .filter(|a| a.parent_id.as_deref() == Some(parent.as_str()) && a.status == "active" && a.agent_id != agent_id)
        .map(|a| {
            obj(vec![
                ("agent_id", Json::String(a.agent_id.clone())),
                ("status", Json::String(a.status.clone())),
                ("last_heartbeat", Json::Float(a.last_heartbeat)),
            ])
        })
        .collect()
}

pub fn lineage(st: &State, agent_id: &str) -> Json {
    let mut ancestors = Vec::new();
    let mut current = Some(agent_id.to_string());
    while let Some(id) = current {
        let parent = st.agent(&id).and_then(|a| a.parent_id.clone());
        let Some(parent) = parent else { break };
        let gp = st.agent(&parent).and_then(|a| a.parent_id.clone());
        ancestors.push(obj(vec![
            ("agent_id", Json::String(parent.clone())),
            ("parent_id", opt_string(&gp)),
        ]));
        current = Some(parent);
    }
    let mut descendants = Vec::new();
    let mut stack = vec![agent_id.to_string()];
    while let Some(cur) = stack.pop() {
        let children: Vec<String> = st
            .agents
            .iter()
            .filter(|a| a.parent_id.as_deref() == Some(cur.as_str()) && a.status == "active")
            .map(|a| a.agent_id.clone())
            .collect();
        for child in children {
            descendants.push(obj(vec![
                ("agent_id", Json::String(child.clone())),
                ("parent_id", Json::String(cur.clone())),
            ]));
            stack.push(child);
        }
    }
    obj(vec![
        ("ancestors", Json::Array(ancestors)),
        ("descendants", Json::Array(descendants)),
    ])
}

pub fn update_status(st: &mut State, args: &Json) -> Json {
    let agent_id = arg_string(args, "agent_id").unwrap_or_default();
    if st.agent(&agent_id).is_none() {
        return obj(vec![(
            "error",
            Json::String(format!("Agent not found: {agent_id}")),
        )]);
    }
    let t = now();
    let cap = env_i64("COORDINATIONHUB_MAX_CURRENT_TASK", 5_000) as usize;
    let task = arg_str(args, "current_task").map(|s| truncate(s, cap));
    let scope = args.get("scope").cloned();
    let resp = st.responsibilities.iter_mut().find(|r| r.agent_id == agent_id);
    if let Some(r) = resp {
        if let Some(task) = task {
            r.current_task = Some(task);
        }
        if let Some(scope) = scope {
            if !scope.is_null() {
                r.scope = Some(scope.dump());
            }
        }
        r.updated_at = t;
    } else {
        st.responsibilities.push(Responsibility {
            agent_id: agent_id.clone(),
            graph_agent_id: None,
            role: None,
            model: None,
            responsibilities: "[]".into(),
            current_task: task,
            scope: scope.and_then(|s| if s.is_null() { None } else { Some(s.dump()) }),
            updated_at: t,
        });
    }
    obj(vec![
        ("updated", Json::Bool(true)),
        ("agent_id", Json::String(agent_id)),
    ])
}

pub fn reap_stale(st: &mut State, timeout: f64) -> Json {
    let t = now();
    let cutoff = t - timeout;
    let stale: Vec<(String, Option<String>)> = st
        .agents
        .iter()
        .filter(|a| a.status == "active" && a.last_heartbeat < cutoff)
        .map(|a| (a.agent_id.clone(), a.parent_id.clone()))
        .collect();
    let mut reaped = 0i64;
    let mut orphaned = 0i64;
    for (agent_id, parent) in stale {
        let still = st
            .agent(&agent_id)
            .map(|a| a.status == "active" && a.last_heartbeat < cutoff)
            .unwrap_or(false);
        if !still {
            continue;
        }
        if let Some(a) = st.agent_mut(&agent_id) {
            a.status = "stopped".into();
        }
        reaped += 1;
        let children: Vec<String> = st
            .agents
            .iter()
            .filter(|a| a.parent_id.as_deref() == Some(agent_id.as_str()) && a.status == "active")
            .map(|a| a.agent_id.clone())
            .collect();
        for child in children {
            if let Some(a) = st.agent_mut(&child) {
                a.parent_id = parent.clone();
            }
            st.lineage
                .retain(|l| !(l.parent_id == agent_id && l.child_id == child));
            orphaned += 1;
        }
        st.claims.retain(|c| c.agent_id != agent_id);
    }
    obj(vec![
        ("reaped", Json::Int(reaped)),
        ("orphaned_children", Json::Int(orphaned)),
    ])
}

pub fn find_by_raw(st: &State, raw: &str, vendor: Option<&str>) -> Option<String> {
    let t = now();
    let mut best: Option<&Agent> = None;
    for a in &st.agents {
        if a.raw_ide_id.as_deref() != Some(raw) || a.status != "active" {
            continue;
        }
        if let Some(v) = vendor {
            if a.ide_vendor.as_deref() != Some(v) {
                continue;
            }
        }
        if a.last_heartbeat < t - 600.0 {
            continue;
        }
        if best.map(|b| a.last_heartbeat > b.last_heartbeat).unwrap_or(true) {
            best = Some(a);
        }
    }
    best.map(|a| a.agent_id.clone())
}

pub fn norm(root: &Path, path: &str) -> String {
    normalize_path(path, root)
}

#[allow(dead_code)]
pub fn arr(v: Vec<Json>) -> Json {
    arr_json(v)
}
