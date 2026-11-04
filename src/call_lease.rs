//! Tasks, messages, intents, dependencies, handoffs, leases, spawns, visibility.

use crate::call_msg::cap;
use crate::json::Json;
use crate::model::*;
use crate::util::{arg_bool, arg_f64, arg_opt_f64, arg_str, arg_string, env_i64, obj, truncate_opt};
use crate::validate::py_repr;
const LEASE: &str = "COORDINATOR_LEADER";

pub fn acquire_lease(st: &mut State, args: &Json) -> Json {
    let agent = arg_string(args, "agent_id").unwrap_or_default();
    let ttl = arg_opt_f64(args, "ttl").unwrap_or(10.0);
    let t = now();
    if let Some(l) = &st.lease {
        if l.lease_name == LEASE && l.expires_at > t && l.holder_id != agent {
            return obj(vec![
                ("acquired", Json::Bool(false)),
                ("lease_name", Json::String(LEASE.into())),
                ("holder", lease_holder(l)),
            ]);
        }
    }
    let exp = t + ttl;
    st.lease = Some(Lease { lease_name: LEASE.into(), holder_id: agent.clone(), acquired_at: t, ttl, expires_at: exp });
    obj(vec![
        ("acquired", Json::Bool(true)),
        ("lease_name", Json::String(LEASE.into())),
        ("holder_id", Json::String(agent)),
        ("ttl", Json::Float(ttl)),
        ("expires_at", Json::Float(exp)),
    ])
}

fn lease_holder(l: &Lease) -> Json {
    obj(vec![
        ("lease_name", Json::String(l.lease_name.clone())),
        ("holder_id", Json::String(l.holder_id.clone())),
        ("acquired_at", Json::Float(l.acquired_at)),
        ("ttl", Json::Float(l.ttl)),
        ("expires_at", Json::Float(l.expires_at)),
    ])
}

pub fn manage_leases(st: &mut State, args: &Json) -> Json {
    match arg_str(args, "action").unwrap_or("") {
        "get" => obj(vec![("leader", current_leader(st))]),
        "acquire" => acquire_lease(st, args),
        "refresh" => refresh_lease(st, &arg_string(args, "agent_id").unwrap_or_default()),
        "release" => release_lease(st, &arg_string(args, "agent_id").unwrap_or_default()),
        "claim" => acquire_lease(st, args),
        other => obj(vec![("error", Json::String(format!("Unknown action: {}", py_repr(&Json::String(other.into())))))]),
    }
}

fn current_leader(st: &State) -> Json {
    let t = now();
    match &st.lease {
        Some(l) if l.expires_at > t => lease_holder(l),
        _ => Json::Null,
    }
}

pub fn refresh_lease(st: &mut State, agent: &str) -> Json {
    let t = now();
    let Some(l) = st.lease.as_mut() else {
        return obj(vec![("refreshed", Json::Bool(false)), ("error", Json::String("Not the current lease holder".into()))]);
    };
    if l.holder_id != agent || l.expires_at <= t {
        return obj(vec![("refreshed", Json::Bool(false)), ("error", Json::String("Not the current lease holder".into()))]);
    }
    l.acquired_at = t;
    l.expires_at = t + l.ttl;
    let exp = l.expires_at;
    obj(vec![
        ("refreshed", Json::Bool(true)),
        ("lease_name", Json::String(LEASE.into())),
        ("expires_at", Json::Float(exp)),
    ])
}

pub fn release_lease(st: &mut State, agent: &str) -> Json {
    match &st.lease {
        Some(l) if l.holder_id == agent => {
            st.lease = None;
            obj(vec![("released", Json::Bool(true)), ("lease_name", Json::String(LEASE.into()))])
        }
        _ => obj(vec![("released", Json::Bool(false)), ("error", Json::String("Not the current lease holder".into()))]),
    }
}

pub fn leader_status(st: &State) -> Json {
    obj(vec![
        ("leader", current_leader(st)),
        ("lease_name", Json::String(LEASE.into())),
    ])
}

pub fn spawn(st: &mut State, args: &Json) -> Json {
    let parent = arg_string(args, "parent_agent_id").unwrap_or_default();
    let kind = arg_string(args, "subagent_type").unwrap_or_default();
    if kind.is_empty() || !kind.chars().all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-') {
        return obj(vec![
            ("stashed", Json::Bool(false)),
            ("reason", Json::String("invalid_subagent_type".into())),
            ("subagent_type", Json::String(kind)),
        ]);
    }
    let source = arg_str(args, "source").unwrap_or("external").to_string();
    let valid = ["cc", "external", "stdio_adapter"];
    if !valid.contains(&source.as_str()) {
        return obj(vec![
            ("stashed", Json::Bool(false)),
            ("reason", Json::String("invalid_source".into())),
            ("source", Json::String(source)),
            ("valid_sources", Json::Array(valid.iter().map(|s| Json::String((*s).into())).collect())),
        ]);
    }
    let pending = st.spawns.iter().filter(|s| s.status == "pending").count();
    let max_pending = env_i64("COORDINATIONHUB_MAX_PENDING_SPAWNS", 1000);
    if pending as i64 >= max_pending {
        return obj(vec![
            ("stashed", Json::Bool(false)),
            ("reason", Json::String("max_pending_spawns_reached".into())),
            ("pending_count", Json::Int(pending as i64)),
            ("max_pending_spawns", Json::Int(max_pending)),
        ]);
    }
    let seq = st.spawns.iter().filter(|s| s.scope_id == parent && s.subagent_type.as_deref() == Some(kind.as_str())).count();
    let id = format!("{parent}.{kind}.{seq}");
    st.spawns.push(Spawn {
        task_id: id.clone(),
        scope_id: parent.clone(),
        subagent_type: Some(kind),
        description: truncate_opt(arg_str(args, "description"), cap("COORDINATIONHUB_MAX_DESCRIPTION", 10_000)),
        prompt: truncate_opt(arg_str(args, "prompt"), cap("COORDINATIONHUB_MAX_PROMPT", 100_000)),
        created_at: now(),
        status: "pending".into(),
        source,
        child_agent_id: None,
        consumed_at: None,
    });
    obj(vec![
        ("stashed", Json::Bool(true)),
        ("spawn_id", Json::String(id)),
        ("parent_agent_id", Json::String(parent)),
    ])
}

pub fn report_spawn(st: &mut State, args: &Json) -> Json {
    let parent = arg_string(args, "parent_agent_id").unwrap_or_default();
    let child = arg_string(args, "child_agent_id").unwrap_or_default();
    let kind = arg_string(args, "subagent_type");
    let source = arg_str(args, "source").unwrap_or("external").to_string();
    let caller = arg_string(args, "caller_agent_id");
    if let Some(c) = &caller {
        if c != &parent {
            return obj(vec![
                ("reported", Json::Bool(false)),
                ("error", Json::String("caller_agent_id does not match parent_agent_id".into())),
                ("reason", Json::String("caller_mismatch".into())),
            ]);
        }
    }
    let valid = ["cc", "external", "stdio_adapter"];
    if !valid.contains(&source.as_str()) {
        return obj(vec![
            ("reported", Json::Bool(false)),
            ("reason", Json::String("invalid_source".into())),
            ("source", Json::String(source)),
            ("valid_sources", Json::Array(valid.iter().map(|s| Json::String((*s).into())).collect())),
            ("parent_agent_id", Json::String(parent)),
            ("child_agent_id", Json::String(child)),
        ]);
    }
    let idx = st.spawns.iter().position(|s| {
        s.scope_id == parent && s.status == "pending" && kind.as_ref().map(|k| s.subagent_type.as_deref() == Some(k.as_str())).unwrap_or(true)
    });
    let (spawn_id, description, prompt) = if let Some(i) = idx {
        st.spawns[i].status = "registered".into();
        st.spawns[i].consumed_at = Some(now());
        st.spawns[i].source = source;
        st.spawns[i].child_agent_id = Some(child.clone());
        (Some(st.spawns[i].task_id.clone()), st.spawns[i].description.clone(), st.spawns[i].prompt.clone())
    } else {
        (None, None, None)
    };
    obj(vec![
        ("reported", Json::Bool(true)),
        ("parent_agent_id", Json::String(parent)),
        ("child_agent_id", Json::String(child)),
        ("spawn_id", opt_string(&spawn_id)),
        ("description", opt_string(&description)),
        ("prompt", opt_string(&prompt)),
    ])
}

pub fn list_spawns(st: &State, args: &Json) -> Json {
    let parent = arg_string(args, "parent_agent_id").unwrap_or_default();
    let all = arg_bool(args, "include_consumed", false);
    let mut rows: Vec<&Spawn> = st.spawns.iter().filter(|s| s.scope_id == parent && (all || s.status == "pending")).collect();
    rows.sort_by(|a, b| a.created_at.partial_cmp(&b.created_at).unwrap_or(std::cmp::Ordering::Equal));
    Json::Array(rows.into_iter().map(|s| obj(vec![
        ("task_id", Json::String(s.task_id.clone())),
        ("scope_id", Json::String(s.scope_id.clone())),
        ("subagent_type", opt_string(&s.subagent_type)),
        ("description", opt_string(&s.description)),
        ("prompt", opt_string(&s.prompt)),
        ("created_at", Json::Float(s.created_at)),
        ("consumed_at", opt_f64(&s.consumed_at)),
        ("status", Json::String(s.status.clone())),
        ("source", Json::String(s.source.clone())),
    ])).collect())
}

pub fn cancel_spawn(st: &mut State, args: &Json) -> Json {
    let id = arg_string(args, "spawn_id").unwrap_or_default();
    let Some(s) = st.spawns.iter_mut().find(|s| s.task_id == id) else {
        return obj(vec![("not_found", Json::Bool(true)), ("spawn_id", Json::String(id))]);
    };
    if s.status != "pending" {
        let status = s.status.clone();
        return obj(vec![("not_found", Json::Bool(true)), ("spawn_id", Json::String(id)), ("status", Json::String(status))]);
    }
    s.status = "expired".into();
    s.consumed_at = Some(now());
    obj(vec![("cancelled", Json::Bool(true)), ("spawn_id", Json::String(id))])
}

pub fn request_stop(st: &mut State, args: &Json) -> Json {
    let parent = arg_string(args, "parent_agent_id").unwrap_or_default();
    let child = arg_string(args, "child_agent_id").unwrap_or_default();
    let Some(a) = st.agent_mut(&child) else {
        return obj(vec![("not_found", Json::Bool(true)), ("child_agent_id", Json::String(child))]);
    };
    if a.status != "active" {
        let status = a.status.clone();
        return obj(vec![("not_found", Json::Bool(true)), ("child_agent_id", Json::String(child)), ("status", Json::String(status))]);
    }
    a.stop_requested_at = Some(now());
    obj(vec![
        ("requested", Json::Bool(true)),
        ("child_agent_id", Json::String(child)),
        ("requested_by", Json::String(parent)),
    ])
}

pub fn stop_requested(st: &State, args: &Json) -> Json {
    let agent = arg_string(args, "agent_id").unwrap_or_default();
    match st.agent(&agent) {
        None => obj(vec![("agent_id", Json::String(agent)), ("stop_requested", Json::Bool(false))]),
        Some(a) => obj(vec![
            ("agent_id", Json::String(agent)),
            ("stop_requested", Json::Bool(a.stop_requested_at.is_some())),
            ("stop_requested_at", opt_f64(&a.stop_requested_at)),
        ]),
    }
}

pub fn await_stopped(st: &State, args: &Json) -> Json {
    let child = arg_string(args, "child_agent_id").unwrap_or_default();
    let timeout = arg_f64(args, "timeout", 30.0);
    match st.agent(&child) {
        None => obj(vec![("stopped", Json::Bool(true)), ("child_agent_id", Json::String(child))]),
        Some(a) if a.status == "stopped" => obj(vec![("stopped", Json::Bool(true)), ("child_agent_id", Json::String(child))]),
        Some(_) => obj(vec![
            ("timed_out", Json::Bool(true)),
            ("child_agent_id", Json::String(child)),
            ("timeout", Json::Float(timeout)),
            ("escalate", Json::Bool(true)),
        ]),
    }
}

pub fn await_reg(st: &State, args: &Json) -> Json {
    let parent = arg_string(args, "parent_agent_id").unwrap_or_default();
    let kind = arg_string(args, "subagent_type");
    let timeout = arg_opt_f64(args, "timeout").unwrap_or(300.0);
    if let Some(s) = st.spawns.iter().find(|s| s.scope_id == parent && s.status == "registered" && kind.as_ref().map(|k| s.subagent_type.as_deref() == Some(k.as_str())).unwrap_or(true)) {
        return obj(vec![("registered", Json::Bool(true)), ("spawn", spawn_public(s))]);
    }
    obj(vec![
        ("timed_out", Json::Bool(true)),
        ("timeout", Json::Float(timeout)),
        ("parent_agent_id", Json::String(parent)),
        ("subagent_type", opt_string(&kind)),
    ])
}

fn spawn_public(s: &Spawn) -> Json {
    obj(vec![
        ("task_id", Json::String(s.task_id.clone())),
        ("scope_id", Json::String(s.scope_id.clone())),
        ("subagent_type", opt_string(&s.subagent_type)),
        ("description", opt_string(&s.description)),
        ("prompt", opt_string(&s.prompt)),
        ("created_at", Json::Float(s.created_at)),
        ("consumed_at", opt_f64(&s.consumed_at)),
        ("status", Json::String(s.status.clone())),
        ("source", Json::String(s.source.clone())),
    ])
}

pub fn await_agent(st: &State, args: &Json) -> Json {
    let agent = arg_string(args, "agent_id").unwrap_or_default();
    let timeout = arg_f64(args, "timeout_s", 60.0);
    match st.agent(&agent) {
        None => obj(vec![
            ("awaited", Json::Bool(true)),
            ("agent_id", Json::String(agent)),
            ("status", Json::String("not_found".into())),
            ("waited_s", Json::Float(0.0)),
        ]),
        Some(a) if a.status == "stopped" => obj(vec![
            ("awaited", Json::Bool(true)),
            ("agent_id", Json::String(agent)),
            ("status", Json::String("stopped".into())),
            ("waited_s", Json::Float(0.0)),
        ]),
        Some(_) => obj(vec![
            ("awaited", Json::Bool(false)),
            ("agent_id", Json::String(agent)),
            ("status", Json::String("timeout".into())),
            ("timeout_s", Json::Float(timeout)),
        ]),
    }
}

