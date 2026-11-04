//! Tasks, messages, intents, dependencies, handoffs, leases, spawns, visibility.

use crate::json::{self, Json};
use crate::model::*;
use crate::util::{arg_bool, arg_i64, arg_opt_i64, arg_str, arg_string, arg_strs, env_i64, obj, truncate};
use crate::validate::py_repr;

pub(crate) fn cap(name: &str, default: i64) -> usize {
    env_i64(name, default) as usize
}

pub fn send_message(st: &mut State, args: &Json) -> Json {
    let from = arg_string(args, "from_agent_id").unwrap_or_default();
    let to = arg_string(args, "to_agent_id").unwrap_or_default();
    let kind = arg_string(args, "message_type").unwrap_or_default();
    let caller = arg_string(args, "caller_agent_id");
    if let Some(c) = &caller {
        if c != &from {
            return obj(vec![
                ("sent", Json::Bool(false)),
                ("error", Json::String("caller_agent_id does not match from_agent_id".into())),
                ("reason", Json::String("caller_mismatch".into())),
            ]);
        }
    }
    let payload = args.get("payload").cloned();
    let payload_json = match payload {
        None | Some(Json::Null) => None,
        Some(v) => Some(truncate(&v.dump(), cap("COORDINATIONHUB_MAX_MESSAGE", 10_000))),
    };
    let id = st.alloc("message");
    st.messages.push(Message {
        id,
        from_agent_id: from,
        to_agent_id: to,
        message_type: kind,
        payload_json,
        created_at: now(),
        read_at: None,
    });
    obj(vec![
        ("sent", Json::Bool(true)),
        ("message_id", Json::Int(id)),
    ])
}

pub(crate) fn message_json(m: &Message) -> Json {
    let payload = match &m.payload_json {
        Some(s) => json::parse(s).unwrap_or(Json::String(s.clone())),
        None => Json::Null,
    };
    obj(vec![
        ("id", Json::Int(m.id)),
        ("from_agent_id", Json::String(m.from_agent_id.clone())),
        ("to_agent_id", Json::String(m.to_agent_id.clone())),
        ("message_type", Json::String(m.message_type.clone())),
        ("created_at", Json::Float(m.created_at)),
        ("read_at", opt_f64(&m.read_at)),
        ("payload", payload),
    ])
}

pub fn get_messages(st: &State, args: &Json) -> Json {
    let agent = arg_string(args, "agent_id").unwrap_or_default();
    let caller = arg_string(args, "caller_agent_id");
    if let Some(c) = &caller {
        if c != &agent {
            return obj(vec![
                ("messages", Json::Array(vec![])),
                ("count", Json::Int(0)),
                ("error", Json::String("caller_agent_id does not match agent_id".into())),
                ("reason", Json::String("caller_mismatch".into())),
            ]);
        }
    }
    let unread = arg_bool(args, "unread_only", false);
    let limit = arg_i64(args, "limit", 50) as usize;
    let since = arg_opt_i64(args, "since_id");
    let mut rows: Vec<&Message> = st
        .messages
        .iter()
        .filter(|m| m.to_agent_id == agent)
        .filter(|m| !unread || m.read_at.is_none())
        .filter(|m| since.map(|s| m.id > s).unwrap_or(true))
        .collect();
    rows.sort_by(|a, b| b.created_at.partial_cmp(&a.created_at).unwrap_or(std::cmp::Ordering::Equal).then(b.id.cmp(&a.id)));
    rows.truncate(limit);
    let n = rows.len() as i64;
    obj(vec![
        ("messages", Json::Array(rows.into_iter().map(message_json).collect())),
        ("count", Json::Int(n)),
    ])
}

pub fn mark_read(st: &mut State, args: &Json) -> Json {
    let agent = arg_string(args, "agent_id").unwrap_or_default();
    let ids = arg_strs(args, "message_ids").map(|v| v.iter().filter_map(|s| s.parse::<i64>().ok()).collect::<Vec<_>>());
    let id_ints = args.get("message_ids").and_then(|v| v.as_array()).map(|a| {
        a.iter().filter_map(|x| x.as_i64()).collect::<Vec<_>>()
    });
    let ids = id_ints.or(ids);
    let t = now();
    let mut n = 0i64;
    for m in st.messages.iter_mut() {
        if m.to_agent_id != agent || m.read_at.is_some() {
            continue;
        }
        if let Some(list) = &ids {
            if !list.contains(&m.id) {
                continue;
            }
        }
        m.read_at = Some(t);
        n += 1;
    }
    obj(vec![("marked_read", Json::Int(n))])
}

pub fn manage_messages(st: &mut State, args: &Json) -> Json {
    match arg_str(args, "action").unwrap_or("") {
        "send" => {
            if arg_str(args, "from_agent_id").is_none()
                || arg_str(args, "to_agent_id").is_none()
                || arg_str(args, "message_type").is_none()
            {
                return obj(vec![(
                    "error",
                    Json::String("from_agent_id, to_agent_id, and message_type are required for send".into()),
                )]);
            }
            send_message(st, args)
        }
        "get" => get_messages(st, args),
        "mark_read" => mark_read(st, args),
        other => obj(vec![(
            "error",
            Json::String(format!("Unknown action: {}", py_repr(&Json::String(other.into())))),
        )]),
    }
}

pub fn create_task(st: &mut State, args: &Json) -> Json {
    let task_id = arg_string(args, "task_id").unwrap_or_default();
    let parent = arg_string(args, "parent_agent_id").unwrap_or_default();
    let description = truncate(
        arg_str(args, "description").unwrap_or(""),
        cap("COORDINATIONHUB_MAX_DESCRIPTION", 10_000),
    );
    let depends = arg_strs(args, "depends_on").unwrap_or_default();
    let priority = arg_i64(args, "priority", 0);
    if st.tasks.iter().any(|t| t.id == task_id) {
        return obj(vec![
            ("created", Json::Bool(false)),
            ("reason", Json::String("exists".into())),
            ("task_id", Json::String(task_id)),
        ]);
    }
    let t = now();
    st.tasks.push(Task {
        id: task_id.clone(),
        parent_agent_id: parent,
        parent_task_id: arg_string(args, "parent_task_id"),
        assigned_agent_id: None,
        description,
        status: "pending".into(),
        created_at: t,
        updated_at: t,
        depends_on: depends,
        blocked_by: None,
        summary: None,
        priority,
        error: None,
    });
    let mut pairs = vec![
        ("created", Json::Bool(true)),
        ("task_id", Json::String(task_id.clone())),
        ("priority", Json::Int(priority)),
    ];
    if args.get("parent_task_id").and_then(|v| v.as_str()).is_some() {
        pairs.insert(2, ("parent_task_id", Json::String(arg_string(args, "parent_task_id").unwrap())));
    }
    Json::object(pairs)
}

pub fn assign_task(st: &mut State, args: &Json) -> Json {
    let task_id = arg_string(args, "task_id").unwrap_or_default();
    let agent = arg_string(args, "assigned_agent_id").unwrap_or_default();
    let Some(task) = st.tasks.iter_mut().find(|t| t.id == task_id) else {
        return obj(vec![
            ("assigned", Json::Bool(false)),
            ("reason", Json::String("task_not_found".into())),
            ("task_id", Json::String(task_id)),
        ]);
    };
    let description = task.description.clone();
    task.assigned_agent_id = Some(agent.clone());
    task.updated_at = now();
    let t = now();
    if let Some(r) = st.responsibilities.iter_mut().find(|r| r.agent_id == agent) {
        r.current_task = Some(description);
        r.updated_at = t;
    } else {
        st.responsibilities.push(crate::model::Responsibility {
            agent_id: agent,
            graph_agent_id: None,
            role: None,
            model: None,
            responsibilities: "[]".into(),
            current_task: Some(description),
            scope: None,
            updated_at: t,
        });
    }
    obj(vec![
        ("assigned", Json::Bool(true)),
        ("task_id", Json::String(task_id)),
    ])
}

pub fn update_task(st: &mut State, args: &Json) -> Json {
    let task_id = arg_string(args, "task_id").unwrap_or_default();
    let status = arg_string(args, "status").unwrap_or_default();
    let valid = ["blocked", "completed", "dead_letter", "failed", "in_progress", "pending"];
    if !valid.contains(&status.as_str()) {
        return obj(vec![
            ("updated", Json::Bool(false)),
            ("reason", Json::String("invalid_status".into())),
            ("task_id", Json::String(task_id)),
            ("status", Json::String(status)),
            (
                "valid_statuses",
                Json::Array(valid.iter().map(|s| Json::String((*s).into())).collect()),
            ),
        ]);
    }
    let Some(idx) = st.tasks.iter().position(|t| t.id == task_id) else {
        return obj(vec![
            ("updated", Json::Bool(false)),
            ("reason", Json::String("task_not_found".into())),
            ("task_id", Json::String(task_id)),
        ]);
    };
    let prior = st.tasks[idx].status.clone();
    let t = now();
    st.tasks[idx].status = status.clone();
    st.tasks[idx].updated_at = t;
    if let Some(s) = arg_str(args, "summary") {
        st.tasks[idx].summary = Some(truncate(s, cap("COORDINATIONHUB_MAX_SUMMARY", 10_000)));
    }
    if let Some(b) = arg_string(args, "blocked_by") {
        st.tasks[idx].blocked_by = Some(b);
    }
    if let Some(e) = arg_str(args, "error") {
        st.tasks[idx].error = Some(truncate(e, cap("COORDINATIONHUB_MAX_ERROR", 10_000)));
    }
    let mut satisfied = 0i64;
    let mut failure = Json::Null;
    if prior != status && status == "completed" {
        for d in st.deps.iter_mut() {
            if d.depends_on_task_id.as_deref() == Some(task_id.as_str()) && !d.satisfied {
                d.satisfied = true;
                d.satisfied_at = Some(t);
                satisfied += 1;
            }
        }
    }
    if prior != status && status == "failed" {
        failure = record_failure(st, &task_id, st.tasks[idx].error.clone());
    }
    obj(vec![
        ("updated", Json::Bool(true)),
        ("task_id", Json::String(task_id)),
        ("status", Json::String(status)),
        ("prior_status", Json::String(prior)),
        ("dependencies_satisfied", Json::Int(satisfied)),
        ("failure_record", failure),
    ])
}

fn record_failure(st: &mut State, task_id: &str, error: Option<String>) -> Json {
    let t = now();
    if let Some(f) = st.failures.iter_mut().find(|f| f.task_id == task_id) {
        f.attempt += 1;
        f.error = error;
        f.last_attempt_at = t;
        if f.attempt >= f.max_retries {
            f.status = "dead_letter".into();
            f.dead_letter_at = Some(t);
        }
        return obj(vec![
            ("recorded", Json::Bool(true)),
            ("status", Json::String(f.status.clone())),
            ("attempt", Json::Int(f.attempt)),
        ]);
    }
    let fid = (st.failures.iter().map(|f| f.id).max().unwrap_or(0)) + 1;
    st.failures.push(Failure {
        id: fid,
        task_id: task_id.to_string(),
        error,
        attempt: 1,
        max_retries: 3,
        first_attempt_at: t,
        last_attempt_at: t,
        dead_letter_at: None,
        status: "failed".into(),
    });
    obj(vec![
        ("recorded", Json::Bool(true)),
        ("status", Json::String("failed".into())),
        ("attempt", Json::Int(1)),
    ])
}

pub fn query_tasks(st: &State, args: &Json) -> Json {
    let qt = arg_str(args, "query_type").unwrap_or("all");
    match qt {
        "task" => {
            let Some(id) = arg_string(args, "task_id") else {
                return obj(vec![("error", Json::String("task_id is required for query_type='task'".into()))]);
            };
            match st.tasks.iter().find(|t| t.id == id) {
                Some(t) => obj(vec![("task", t.to_json())]),
                None => obj(vec![("error", Json::String(format!("Task {id:?} not found")))]),
            }
        }
        "child" => {
            let Some(id) = arg_string(args, "parent_agent_id") else {
                return obj(vec![("error", Json::String("parent_agent_id is required for query_type='child'".into()))]);
            };
            let mut tasks: Vec<&Task> = st.tasks.iter().filter(|t| t.parent_agent_id == id).collect();
            tasks.sort_by(|a, b| b.priority.cmp(&a.priority).then(a.created_at.partial_cmp(&b.created_at).unwrap_or(std::cmp::Ordering::Equal)));
            let n = tasks.len() as i64;
            obj(vec![("tasks", Json::Array(tasks.into_iter().map(|t| t.to_json()).collect())), ("count", Json::Int(n))])
        }
        "by_agent" => {
            let Some(id) = arg_string(args, "assigned_agent_id") else {
                return obj(vec![("error", Json::String("assigned_agent_id is required for query_type='by_agent'".into()))]);
            };
            let tasks: Vec<&Task> = st.tasks.iter().filter(|t| t.assigned_agent_id.as_deref() == Some(id.as_str())).collect();
            let n = tasks.len() as i64;
            obj(vec![("tasks", Json::Array(tasks.into_iter().map(|t| t.to_json()).collect())), ("count", Json::Int(n))])
        }
        "subtasks" => {
            let Some(id) = arg_string(args, "parent_task_id") else {
                return obj(vec![("error", Json::String("parent_task_id is required for query_type='subtasks'".into()))]);
            };
            let tasks: Vec<&Task> = st.tasks.iter().filter(|t| t.parent_task_id.as_deref() == Some(id.as_str())).collect();
            let n = tasks.len() as i64;
            obj(vec![("subtasks", Json::Array(tasks.into_iter().map(|t| t.to_json()).collect())), ("count", Json::Int(n))])
        }
        "tree" => {
            let Some(id) = arg_string(args, "root_task_id") else {
                return obj(vec![("error", Json::String("root_task_id is required for query_type='tree'".into()))]);
            };
            task_tree(st, &id).unwrap_or_else(|| obj(vec![("error", Json::String(format!("Task {id:?} not found")))]))
        }
        "all" => {
            let mut tasks: Vec<&Task> = st.tasks.iter().collect();
            tasks.sort_by(|a, b| b.priority.cmp(&a.priority));
            let n = tasks.len() as i64;
            obj(vec![("tasks", Json::Array(tasks.into_iter().map(|t| t.to_json()).collect())), ("count", Json::Int(n))])
        }
        other => obj(vec![("error", Json::String(format!("Unknown query_type: {}", py_repr(&Json::String(other.into())))))]),
    }
}

fn task_tree(st: &State, id: &str) -> Option<Json> {
    let task = st.tasks.iter().find(|t| t.id == id)?;
    let mut node = task.to_json();
    let kids: Vec<Json> = st
        .tasks
        .iter()
        .filter(|t| t.parent_task_id.as_deref() == Some(id))
        .filter_map(|t| task_tree(st, &t.id))
        .collect();
    if let Json::Object(pairs) = &mut node {
        pairs.push(("subtasks".into(), Json::Array(kids)));
    }
    Some(node)
}

pub fn available_tasks(st: &State, args: &Json) -> Json {
    let agent = arg_string(args, "agent_id");
    let tasks: Vec<Json> = st
        .tasks
        .iter()
        .filter(|t| t.status == "pending")
        .filter(|t| {
            t.depends_on.iter().all(|d| {
                st.tasks.iter().find(|x| x.id == *d).map(|x| x.status == "completed").unwrap_or(false)
            })
        })
        .filter(|t| agent.as_ref().map(|a| t.assigned_agent_id.as_deref() == Some(a.as_str()) || t.assigned_agent_id.is_none()).unwrap_or(true))
        .map(|t| t.to_json())
        .collect();
    let n = tasks.len() as i64;
    obj(vec![("tasks", Json::Array(tasks)), ("count", Json::Int(n))])
}

pub fn task_failures(st: &mut State, args: &Json) -> Json {
    match arg_str(args, "action").unwrap_or("list_dead_letter") {
        "retry" => {
            let Some(id) = arg_string(args, "task_id") else {
                return obj(vec![("error", Json::String("task_id is required for retry".into()))]);
            };
            if let Some(f) = st.failures.iter_mut().find(|f| f.task_id == id && f.status == "dead_letter") {
                f.status = "failed".into();
            }
            if let Some(t) = st.tasks.iter_mut().find(|t| t.id == id) {
                t.status = "pending".into();
                t.updated_at = now();
            }
            obj(vec![("retried", Json::Bool(true)), ("task_id", Json::String(id))])
        }
        "history" => {
            let Some(id) = arg_string(args, "task_id") else {
                return obj(vec![("error", Json::String("task_id is required for history".into()))]);
            };
            let hist: Vec<Json> = st.failures.iter().filter(|f| f.task_id == id).map(|f| obj(vec![
                ("id", Json::Int(f.id)),
                ("task_id", Json::String(f.task_id.clone())),
                ("error", opt_string(&f.error)),
                ("attempt", Json::Int(f.attempt)),
                ("max_retries", Json::Int(f.max_retries)),
                ("first_attempt_at", Json::Float(f.first_attempt_at)),
                ("last_attempt_at", Json::Float(f.last_attempt_at)),
                ("dead_letter_at", opt_f64(&f.dead_letter_at)),
                ("status", Json::String(f.status.clone())),
            ])).collect();
            let n = hist.len() as i64;
            obj(vec![("task_id", Json::String(id)), ("history", Json::Array(hist)), ("count", Json::Int(n))])
        }
        "list_dead_letter" => {
            let limit = arg_i64(args, "limit", 50) as usize;
            let rows: Vec<Json> = st.failures.iter().filter(|f| f.status == "dead_letter").take(limit).map(|f| {
                let orphan = !st.tasks.iter().any(|t| t.id == f.task_id);
                obj(vec![
                    ("task_id", Json::String(f.task_id.clone())),
                    ("error", opt_string(&f.error)),
                    ("attempt", Json::Int(f.attempt)),
                    ("status", Json::String(f.status.clone())),
                    ("orphan", Json::Bool(orphan)),
                ])
            }).collect();
            let n = rows.len() as i64;
            obj(vec![("dead_letter_tasks", Json::Array(rows)), ("count", Json::Int(n))])
        }
        other => obj(vec![("error", Json::String(format!("Unknown action: {}", py_repr(&Json::String(other.into())))))]),
    }
}

