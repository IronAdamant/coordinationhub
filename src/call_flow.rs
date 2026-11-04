//! Tasks, messages, intents, dependencies, handoffs, leases, spawns, visibility.

use crate::call_id::{norm, siblings_of};
use crate::call_lock::status_for;
use crate::call_msg::cap;
use crate::json::Json;
use crate::model::*;
use crate::util::{arg_bool, arg_f64, arg_i64, arg_opt_i64, arg_str, arg_string, arg_strs, obj, truncate};
use crate::validate::py_repr;
use std::path::Path;

pub fn intents(st: &mut State, root: &Path, args: &Json) -> Json {
    let action = arg_str(args, "action").unwrap_or("");
    let agent = arg_string(args, "agent_id").unwrap_or_default();
    match action {
        "declare" => {
            let Some(path) = arg_string(args, "document_path") else {
                return obj(vec![("error", Json::String("document_path and intent are required for declare".into()))]);
            };
            let Some(intent) = arg_str(args, "intent") else {
                return obj(vec![("error", Json::String("document_path and intent are required for declare".into()))]);
            };
            let path = norm(root, &path);
            let intent = truncate(intent, cap("COORDINATIONHUB_MAX_INTENT", 1_000));
            let ttl = arg_f64(args, "ttl", 60.0);
            let t = now();
            if let Some(row) = st.intents.iter_mut().find(|i| i.agent_id == agent && i.document_path == path) {
                row.intent = intent;
                row.declared_at = t;
                row.ttl = ttl;
            } else {
                st.intents.push(Intent { agent_id: agent.clone(), document_path: path.clone(), intent, declared_at: t, ttl });
            }
            obj(vec![
                ("recorded", Json::Bool(true)),
                ("agent_id", Json::String(agent)),
                ("document_path", Json::String(path)),
            ])
        }
        "get" => {
            let t = now();
            let filter = if agent.is_empty() { None } else { Some(agent) };
            let rows: Vec<Json> = st.intents.iter().filter(|i| i.alive(t) && filter.as_ref().map(|a| i.agent_id == *a).unwrap_or(true)).map(|i| i.to_json()).collect();
            let n = rows.len() as i64;
            obj(vec![("intents", Json::Array(rows)), ("count", Json::Int(n))])
        }
        "clear" => {
            let path = arg_string(args, "document_path").map(|p| norm(root, &p));
            let before = st.intents.len();
            st.intents.retain(|i| {
                if i.agent_id != agent {
                    return true;
                }
                if let Some(p) = &path {
                    return i.document_path != *p;
                }
                false
            });
            obj(vec![
                ("cleared", Json::Bool(true)),
                ("agent_id", Json::String(agent)),
                ("document_path", opt_string(&path)),
                ("rows_cleared", Json::Int((before - st.intents.len()) as i64)),
            ])
        }
        other => obj(vec![("error", Json::String(format!("Unknown action: {}", py_repr(&Json::String(other.into())))))]),
    }
}

pub(crate) fn dep_json(d: &Dep) -> Json {
    obj(vec![
        ("id", Json::Int(d.id)),
        ("dependent_agent_id", Json::String(d.dependent_agent_id.clone())),
        ("depends_on_agent_id", Json::String(d.depends_on_agent_id.clone())),
        ("depends_on_task_id", opt_string(&d.depends_on_task_id)),
        ("condition", Json::String(d.condition.clone())),
        ("satisfied", Json::Int(if d.satisfied { 1 } else { 0 })),
        ("satisfied_at", opt_f64(&d.satisfied_at)),
        ("created_at", Json::Float(d.created_at)),
    ])
}

pub fn declare_dep(st: &mut State, args: &Json) -> Json {
    let a = arg_string(args, "dependent_agent_id").unwrap_or_default();
    let b = arg_string(args, "depends_on_agent_id").unwrap_or_default();
    if a.is_empty() || b.is_empty() {
        return obj(vec![("error", Json::String("dependent_agent_id and depends_on_agent_id are required for declare".into()))]);
    }
    let id = st.alloc("dep");
    st.deps.push(Dep {
        id,
        dependent_agent_id: a,
        depends_on_agent_id: b,
        depends_on_task_id: arg_string(args, "depends_on_task_id"),
        condition: arg_str(args, "condition").unwrap_or("task_completed").to_string(),
        satisfied: false,
        satisfied_at: None,
        created_at: now(),
    });
    obj(vec![("declared", Json::Bool(true)), ("dep_id", Json::Int(id))])
}

pub fn manage_deps(st: &mut State, args: &Json) -> Json {
    let mode = arg_str(args, "mode").unwrap_or("");
    match mode {
        "declare" => declare_dep(st, args),
        "check" | "blockers" => {
            let Some(agent) = arg_string(args, "agent_id") else {
                return obj(vec![("error", Json::String("agent_id is required for check/blockers".into()))]);
            };
            let rows = unsatisfied(st, &agent);
            let blocked = !rows.is_empty();
            obj(vec![
                ("agent_id", Json::String(agent)),
                ("blocked", Json::Bool(blocked)),
                ("unsatisfied", Json::Array(rows)),
            ])
        }
        "assert" => {
            let Some(agent) = arg_string(args, "agent_id") else {
                return obj(vec![("error", Json::String("agent_id is required for assert".into()))]);
            };
            let rows = unsatisfied(st, &agent);
            if rows.is_empty() {
                obj(vec![("can_start", Json::Bool(true))])
            } else {
                obj(vec![("can_start", Json::Bool(false)), ("blockers", Json::Array(rows))])
            }
        }
        "satisfy" => {
            let Some(id) = arg_opt_i64(args, "dep_id") else {
                return obj(vec![("error", Json::String("dep_id is required for satisfy".into()))]);
            };
            satisfy_dep(st, id)
        }
        "list" => {
            let agent = arg_string(args, "agent_id");
            let rows: Vec<Json> = st.deps.iter().filter(|d| agent.as_ref().map(|a| d.dependent_agent_id == *a).unwrap_or(true)).map(dep_json).collect();
            let n = rows.len() as i64;
            obj(vec![("dependencies", Json::Array(rows)), ("count", Json::Int(n))])
        }
        "wait" => {
            let Some(id) = arg_opt_i64(args, "dep_id") else {
                return obj(vec![("error", Json::String("dep_id is required for wait".into()))]);
            };
            let done = st.deps.iter().any(|d| d.id == id && d.satisfied);
            if done {
                obj(vec![("satisfied", Json::Bool(true)), ("dep_id", Json::Int(id)), ("timed_out", Json::Bool(false))])
            } else {
                obj(vec![("satisfied", Json::Bool(false)), ("dep_id", Json::Int(id)), ("timed_out", Json::Bool(true))])
            }
        }
        other => obj(vec![("error", Json::String(format!("Unknown mode: {}", py_repr(&Json::String(other.into())))))]),
    }
}

fn unsatisfied(st: &State, agent: &str) -> Vec<Json> {
    st.deps.iter().filter(|d| d.dependent_agent_id == agent && !d.satisfied).map(dep_json).collect()
}

pub fn satisfy_dep(st: &mut State, id: i64) -> Json {
    if let Some(d) = st.deps.iter_mut().find(|d| d.id == id) {
        d.satisfied = true;
        d.satisfied_at = Some(now());
    }
    obj(vec![("satisfied", Json::Bool(true)), ("dep_id", Json::Int(id))])
}

pub fn broadcast(st: &mut State, root: &Path, args: &Json) -> Json {
    let agent = arg_string(args, "agent_id").unwrap_or_default();
    let doc = arg_string(args, "document_path");
    let ttl = arg_f64(args, "ttl", 30.0);
    let targets = arg_strs(args, "handoff_targets");
    let require_ack = arg_bool(args, "require_ack", false);
    let message = arg_string(args, "message");
    if let Some(targets) = targets {
        return handoff_create(st, &agent, &targets, doc.as_deref());
    }
    let t = now();
    let live: Vec<String> = siblings_of(st, &agent)
        .into_iter()
        .filter_map(|s| {
            let id = s.get("agent_id")?.as_str()?.to_string();
            let hb = s.get("last_heartbeat")?.as_f64()?;
            if t - hb <= ttl { Some(id) } else { None }
        })
        .collect();
    if require_ack && !live.is_empty() {
        let id = st.alloc("broadcast");
        st.broadcasts.push(Broadcast {
            id,
            from_agent_id: agent.clone(),
            document_path: doc.clone(),
            message: message.clone(),
            created_at: t,
            ttl,
            expires_at: t + ttl,
            expected_count: live.len() as i64,
            targets: live.clone(),
            acks: Vec::new(),
        });
        for sib in &live {
            let payload = obj(vec![
                ("broadcast_id", Json::Int(id)),
                ("document_path", opt_string(&doc)),
                ("message", opt_string(&message)),
            ]);
            let mid = st.alloc("message");
            st.messages.push(Message {
                id: mid,
                from_agent_id: agent.clone(),
                to_agent_id: sib.clone(),
                message_type: "broadcast_ack_request".into(),
                payload_json: Some(payload.dump()),
                created_at: now(),
                read_at: None,
            });
        }
        return obj(vec![
            ("broadcast_id", Json::Int(id)),
            ("acknowledged_by", Json::Array(vec![])),
            ("pending_acks", Json::Array(live.into_iter().map(Json::String).collect())),
            ("conflicts", Json::Array(vec![])),
        ]);
    }
    let mut conf = Vec::new();
    if let Some(doc) = &doc {
        let path = norm(root, doc);
        for l in &st.locks {
            if l.document_path == path && live.iter().any(|s| s == &l.locked_by) && l.locked_by != agent {
                conf.push(obj(vec![
                    ("document_path", Json::String(doc.clone())),
                    ("locked_by", Json::String(l.locked_by.clone())),
                ]));
            }
        }
    }
    obj(vec![
        ("acknowledged_by", Json::Array(vec![])),
        ("conflicts", Json::Array(conf)),
    ])
}

fn handoff_create(st: &mut State, agent: &str, targets: &[String], doc: Option<&str>) -> Json {
    let id = st.alloc("handoff");
    let t = now();
    st.handoffs.push(Handoff {
        id,
        from_agent_id: agent.to_string(),
        to_agents: targets.to_vec(),
        document_path: doc.map(|s| s.to_string()),
        handoff_type: "scope_transfer".into(),
        status: "pending".into(),
        created_at: t,
        acknowledged_at: None,
        completed_at: None,
        acked_by: Vec::new(),
    });
    for target in targets {
        let payload = obj(vec![
            ("handoff_id", Json::Int(id)),
            ("document_path", match doc { Some(d) => Json::String(d.into()), None => Json::Null }),
            ("handoff_type", Json::String("scope_transfer".into())),
        ]);
        let mid = st.alloc("message");
        st.messages.push(Message {
            id: mid,
            from_agent_id: agent.to_string(),
            to_agent_id: target.clone(),
            message_type: "handoff".into(),
            payload_json: Some(payload.dump()),
            created_at: now(),
            read_at: None,
        });
    }
    obj(vec![
        ("handoff_id", Json::Int(id)),
        ("to_agents", Json::Array(targets.iter().cloned().map(Json::String).collect())),
        ("document_path", match doc { Some(d) => Json::String(d.into()), None => Json::Null }),
        ("handoff_type", Json::String("scope_transfer".into())),
    ])
}

pub fn ack_broadcast(st: &mut State, args: &Json) -> Json {
    let id = arg_i64(args, "broadcast_id", 0);
    let agent = arg_string(args, "agent_id").unwrap_or_default();
    let t = now();
    let Some(b) = st.broadcasts.iter_mut().find(|b| b.id == id) else {
        return obj(vec![("acknowledged", Json::Bool(false)), ("reason", Json::String("expired_or_not_found".into()))]);
    };
    if b.expires_at <= t {
        return obj(vec![("acknowledged", Json::Bool(false)), ("reason", Json::String("expired_or_not_found".into()))]);
    }
    let already = b.acks.iter().any(|a| a == &agent);
    if !already {
        b.acks.push(agent.clone());
    }
    obj(vec![
        ("acknowledged", Json::Bool(true)),
        ("broadcast_id", Json::Int(id)),
        ("agent_id", Json::String(agent)),
        ("already_acked", Json::Bool(already)),
    ])
}

pub fn broadcast_status(st: &State, id: i64) -> Json {
    let Some(b) = st.broadcasts.iter().find(|b| b.id == id) else {
        return obj(vec![("found", Json::Bool(false))]);
    };
    let pending: Vec<Json> = b.targets.iter().filter(|t| !b.acks.iter().any(|a| a == *t)).cloned().map(Json::String).collect();
    obj(vec![
        ("found", Json::Bool(true)),
        ("broadcast_id", Json::Int(b.id)),
        ("from_agent_id", Json::String(b.from_agent_id.clone())),
        ("document_path", opt_string(&b.document_path)),
        ("message", opt_string(&b.message)),
        ("created_at", Json::Float(b.created_at)),
        ("expires_at", Json::Float(b.expires_at)),
        ("expected_count", Json::Int(b.expected_count)),
        ("acknowledged_by", Json::Array(b.acks.iter().cloned().map(Json::String).collect())),
        ("pending_acks", Json::Array(pending)),
    ])
}

pub fn wait_broadcast(st: &State, args: &Json) -> Json {
    let id = arg_i64(args, "broadcast_id", 0);
    let status = broadcast_status(st, id);
    if status.get("found").and_then(|v| v.as_bool()) != Some(true) {
        return obj(vec![("timed_out", Json::Bool(true)), ("reason", Json::String("not_found".into()))]);
    }
    let exp = status.get("expires_at").and_then(|v| v.as_f64()).unwrap_or(0.0);
    if exp < now() {
        return obj(vec![
            ("timed_out", Json::Bool(true)),
            ("reason", Json::String("expired".into())),
            ("acknowledged_by", status.get("acknowledged_by").cloned().unwrap_or(Json::Array(vec![]))),
        ]);
    }
    let expected = status.get("expected_count").and_then(|v| v.as_i64()).unwrap_or(0);
    let acked = status.get("acknowledged_by").and_then(|v| v.as_array()).map(|a| a.len()).unwrap_or(0) as i64;
    if expected <= 0 || acked >= expected {
        return obj(vec![
            ("timed_out", Json::Bool(false)),
            ("acknowledged_by", status.get("acknowledged_by").cloned().unwrap_or(Json::Array(vec![]))),
            ("pending_acks", status.get("pending_acks").cloned().unwrap_or(Json::Array(vec![]))),
        ]);
    }
    obj(vec![
        ("timed_out", Json::Bool(true)),
        ("acknowledged_by", status.get("acknowledged_by").cloned().unwrap_or(Json::Array(vec![]))),
        ("pending_acks", status.get("pending_acks").cloned().unwrap_or(Json::Array(vec![]))),
    ])
}

pub fn wait_locks(st: &State, root: &Path, args: &Json) -> Json {
    let agent = arg_string(args, "agent_id").unwrap_or_default();
    let paths = arg_strs(args, "document_paths").unwrap_or_default();
    let mut released = Vec::new();
    let mut waiting = Vec::new();
    for p in paths {
        let n = norm(root, &p);
        let status = status_for(st, &n);
        let locked = status.get("locked").and_then(|v| v.as_bool()).unwrap_or(false);
        let holder = status.get("locked_by").and_then(|v| v.as_str());
        if !locked || holder == Some(agent.as_str()) {
            released.push(Json::String(n));
        } else {
            waiting.push(Json::String(n));
        }
    }
    obj(vec![
        ("released", Json::Array(released)),
        ("timed_out", Json::Array(waiting)),
    ])
}

fn handoff_row(h: &Handoff) -> Json {
    obj(vec![
        ("id", Json::Int(h.id)),
        ("from_agent_id", Json::String(h.from_agent_id.clone())),
        ("to_agents", Json::Array(h.to_agents.iter().cloned().map(Json::String).collect())),
        ("document_path", opt_string(&h.document_path)),
        ("handoff_type", Json::String(h.handoff_type.clone())),
        ("status", Json::String(h.status.clone())),
        ("created_at", Json::Float(h.created_at)),
        ("acknowledged_at", opt_f64(&h.acknowledged_at)),
        ("completed_at", opt_f64(&h.completed_at)),
    ])
}

pub fn wait_handoff(st: &mut State, args: &Json) -> Json {
    let id = arg_i64(args, "handoff_id", 0);
    let mode = arg_str(args, "mode").unwrap_or("completion");
    let agent = arg_string(args, "agent_id");
    match mode {
        "status" => st.handoffs.iter().find(|h| h.id == id).map(handoff_row).unwrap_or_else(|| obj(vec![("error", Json::String(format!("Handoff {id} not found")))])),
        "ack" => ack_handoff(st, id, agent.as_deref().unwrap_or("")),
        "complete" => complete_handoff(st, id),
        "cancel" => cancel_handoff(st, id),
        "completion" => {
            let done = st.handoffs.iter().any(|h| h.id == id && h.status == "completed");
            obj(vec![("timed_out", Json::Bool(!done)), ("handoff_id", Json::Int(id))])
        }
        other => obj(vec![("error", Json::String(format!("Unknown mode: {}", py_repr(&Json::String(other.into())))))]),
    }
}

pub fn ack_handoff(st: &mut State, id: i64, agent: &str) -> Json {
    let Some(h) = st.handoffs.iter_mut().find(|h| h.id == id) else {
        return obj(vec![("acknowledged", Json::Bool(false)), ("reason", Json::String("not_found".into())), ("handoff_id", Json::Int(id))]);
    };
    if !h.to_agents.iter().any(|a| a == agent) {
        return obj(vec![
            ("acknowledged", Json::Bool(false)),
            ("reason", Json::String("not_recipient".into())),
            ("handoff_id", Json::Int(id)),
            ("agent_id", Json::String(agent.into())),
        ]);
    }
    if h.status == "completed" || h.status == "cancelled" {
        return obj(vec![
            ("acknowledged", Json::Bool(false)),
            ("reason", Json::String(format!("illegal_transition_from_{}", h.status))),
            ("handoff_id", Json::Int(id)),
        ]);
    }
    if !h.acked_by.iter().any(|a| a == agent) {
        h.acked_by.push(agent.to_string());
    }
    let expected = h.to_agents.len();
    let acks = h.acked_by.len();
    let t = now();
    if expected > 0 && acks >= expected {
        h.status = "acknowledged".into();
        h.acknowledged_at = Some(t);
    } else {
        h.status = "partially_acknowledged".into();
    }
    let status = h.status.clone();
    obj(vec![
        ("acknowledged", Json::Bool(true)),
        ("handoff_id", Json::Int(id)),
        ("agent_id", Json::String(agent.into())),
        ("status", Json::String(status)),
        ("acks", Json::Int(acks as i64)),
        ("expected", Json::Int(expected as i64)),
    ])
}

pub fn complete_handoff(st: &mut State, id: i64) -> Json {
    let Some(h) = st.handoffs.iter_mut().find(|h| h.id == id) else {
        return obj(vec![("completed", Json::Bool(false)), ("reason", Json::String("not_found".into())), ("handoff_id", Json::Int(id))]);
    };
    if h.status != "acknowledged" {
        return obj(vec![
            ("completed", Json::Bool(false)),
            ("reason", Json::String(format!("illegal_transition_from_{}", h.status))),
            ("handoff_id", Json::Int(id)),
        ]);
    }
    h.status = "completed".into();
    h.completed_at = Some(now());
    obj(vec![("completed", Json::Bool(true)), ("handoff_id", Json::Int(id))])
}

pub fn cancel_handoff(st: &mut State, id: i64) -> Json {
    let Some(h) = st.handoffs.iter_mut().find(|h| h.id == id) else {
        return obj(vec![("cancelled", Json::Bool(false)), ("reason", Json::String("not_found".into())), ("handoff_id", Json::Int(id))]);
    };
    if matches!(h.status.as_str(), "completed" | "cancelled") {
        return obj(vec![
            ("cancelled", Json::Bool(false)),
            ("reason", Json::String(format!("illegal_transition_from_{}", h.status))),
            ("handoff_id", Json::Int(id)),
        ]);
    }
    h.status = "cancelled".into();
    obj(vec![("cancelled", Json::Bool(true)), ("handoff_id", Json::Int(id))])
}

pub fn get_handoffs(st: &State, args: &Json) -> Json {
    let status = arg_string(args, "status");
    let from = arg_string(args, "from_agent_id");
    let limit = arg_i64(args, "limit", 50) as usize;
    let mut rows: Vec<&Handoff> = st.handoffs.iter().filter(|h| {
        status.as_ref().map(|s| h.status == *s).unwrap_or(true) && from.as_ref().map(|f| h.from_agent_id == *f).unwrap_or(true)
    }).collect();
    rows.sort_by(|a, b| b.created_at.partial_cmp(&a.created_at).unwrap_or(std::cmp::Ordering::Equal));
    rows.truncate(limit);
    let n = rows.len() as i64;
    obj(vec![("handoffs", Json::Array(rows.into_iter().map(handoff_row).collect())), ("count", Json::Int(n))])
}

