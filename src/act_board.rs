//! Tasks, messages, intents, dependencies, leases, spawns, broadcasts, handoffs.

use crate::act_identity;
use crate::db::{
    Broadcast, BroadcastAck, BroadcastTarget, Dependency, Failure, Handoff, HandoffAck, Intent,
    Message, Spawn, State, Task,
};
use crate::json::{self, Value};
use crate::util::{self, normalize_path, truncate};
use std::path::Path;

const VALID_STATUS: &[&str] = &[
    "blocked",
    "completed",
    "dead_letter",
    "failed",
    "in_progress",
    "pending",
];
const COORDINATOR: &str = "COORDINATOR_LEADER";
const DEFAULT_LEASE_TTL: f64 = 10.0;

fn max_description() -> i64 {
    util::env_i64("COORDINATIONHUB_MAX_DESCRIPTION", 10_000)
}
fn max_prompt() -> i64 {
    util::env_i64("COORDINATIONHUB_MAX_PROMPT", 100_000)
}
fn max_summary() -> i64 {
    util::env_i64("COORDINATIONHUB_MAX_SUMMARY", 10_000)
}
fn max_error() -> i64 {
    util::env_i64("COORDINATIONHUB_MAX_ERROR", 10_000)
}
fn max_intent() -> i64 {
    util::env_i64("COORDINATIONHUB_MAX_INTENT", 1_000)
}
fn max_message() -> i64 {
    util::env_i64("COORDINATIONHUB_MAX_MESSAGE", 10_000)
}
fn max_task() -> i64 {
    util::env_i64("COORDINATIONHUB_MAX_CURRENT_TASK", 5_000)
}

pub fn create_task(
    state: &mut State,
    task_id: &str,
    parent_agent_id: &str,
    description: &str,
    depends_on: Option<&[String]>,
    priority: i64,
) -> Result<Value, String> {
    if state.tasks.iter().any(|task| task.id == task_id) {
        return Err("UNIQUE constraint failed: tasks.id".into());
    }
    let now = util::now();
    let description =
        truncate(Some(description.to_string()), max_description()).unwrap_or_default();
    let depends = match depends_on {
        Some(items) if !items.is_empty() => json::dumps(&json::string_list(items)),
        _ => "[]".into(),
    };
    state.tasks.push(Task {
        id: task_id.into(),
        parent_agent_id: parent_agent_id.into(),
        parent_task_id: None,
        assigned_agent_id: None,
        description,
        status: "pending".into(),
        created_at: now,
        updated_at: now,
        depends_on: depends,
        blocked_by: None,
        summary: None,
        priority,
        error: None,
    });
    Ok(json::obj(vec![
        ("created", json::b(true)),
        ("task_id", json::s(task_id)),
        ("priority", json::i(priority)),
    ]))
}

pub fn assign_task(state: &mut State, task_id: &str, assigned: &str) -> Value {
    let Some(pos) = state.tasks.iter().position(|task| task.id == task_id) else {
        return json::obj(vec![
            ("assigned", json::b(false)),
            ("reason", json::s("task_not_found")),
            ("task_id", json::s(task_id)),
        ]);
    };
    let prior = state.tasks[pos].assigned_agent_id.clone();
    let description = state.tasks[pos].description.clone();
    let now = util::now();
    state.tasks[pos].assigned_agent_id = Some(assigned.into());
    state.tasks[pos].updated_at = now;
    if let Some(prev) = prior {
        if prev != assigned {
            if let Some(resp) = state.responsibilities.iter_mut().find(|row| {
                row.agent_id == prev && row.current_task.as_deref() == Some(description.as_str())
            }) {
                resp.current_task = None;
                resp.updated_at = now;
            }
        }
    }
    upsert_task_text(state, assigned, Some(description), now);
    json::obj(vec![
        ("assigned", json::b(true)),
        ("task_id", json::s(task_id)),
    ])
}

fn upsert_task_text(state: &mut State, agent_id: &str, current_task: Option<String>, now: f64) {
    if let Some(resp) = state
        .responsibilities
        .iter_mut()
        .find(|row| row.agent_id == agent_id)
    {
        resp.current_task = current_task;
        resp.updated_at = now;
    } else {
        state.responsibilities.push(crate::db::Responsibility {
            agent_id: agent_id.into(),
            graph_agent_id: None,
            role: None,
            model: None,
            responsibilities: None,
            current_task,
            scope: None,
            updated_at: now,
        });
    }
}

pub fn update_task(
    state: &mut State,
    task_id: &str,
    status: &str,
    summary: Option<String>,
    blocked_by: Option<String>,
    error: Option<String>,
) -> Value {
    if !VALID_STATUS.contains(&status) {
        return json::obj(vec![
            ("updated", json::b(false)),
            ("reason", json::s("invalid_status")),
            ("task_id", json::s(task_id)),
            ("status", json::s(status)),
            (
                "valid_statuses",
                json::string_list(
                    &VALID_STATUS
                        .iter()
                        .map(|s| (*s).to_string())
                        .collect::<Vec<_>>(),
                ),
            ),
        ]);
    }
    let Some(pos) = state.tasks.iter().position(|task| task.id == task_id) else {
        return json::obj(vec![
            ("updated", json::b(false)),
            ("reason", json::s("task_not_found")),
            ("task_id", json::s(task_id)),
        ]);
    };
    let prior = state.tasks[pos].status.clone();
    let now = util::now();
    state.tasks[pos].status = status.into();
    state.tasks[pos].updated_at = now;
    if let Some(summary) = truncate(summary, max_summary()) {
        state.tasks[pos].summary = Some(summary);
    }
    if let Some(blocked) = blocked_by {
        state.tasks[pos].blocked_by = Some(blocked);
    }
    if let Some(error) = truncate(error, max_error()) {
        state.tasks[pos].error = Some(error);
    }
    let changed = prior != status;
    let mut satisfied = 0_i64;
    let mut failure = Value::Null;
    if status == "completed" && changed {
        for dep in &mut state.dependencies {
            if dep.depends_on_task_id.as_deref() == Some(task_id) && dep.satisfied == 0 {
                dep.satisfied = 1;
                dep.satisfied_at = Some(now);
                satisfied += 1;
            }
        }
    } else if status == "failed" && changed {
        failure = record_failure(state, task_id, state.tasks[pos].error.clone());
    }
    json::obj(vec![
        ("updated", json::b(true)),
        ("task_id", json::s(task_id)),
        ("status", json::s(status)),
        ("prior_status", json::s(prior)),
        ("dependencies_satisfied", json::i(satisfied)),
        ("failure_record", failure),
    ])
}

fn record_failure(state: &mut State, task_id: &str, error: Option<String>) -> Value {
    let now = util::now();
    if let Some(existing) = state
        .failures
        .iter_mut()
        .rev()
        .find(|row| row.task_id == task_id && row.status != "retried")
    {
        existing.attempt += 1;
        let dead = existing.attempt >= existing.max_retries;
        existing.error = error;
        existing.last_attempt_at = now;
        existing.status = if dead {
            "dead_letter".into()
        } else {
            "failed".into()
        };
        existing.dead_letter_at = if dead { Some(now) } else { None };
        if dead {
            if let Some(task) = state.tasks.iter_mut().find(|task| task.id == task_id) {
                task.status = "dead_letter".into();
            }
        }
        return json::obj(vec![
            ("recorded", json::b(true)),
            ("status", json::s(&existing.status)),
            ("attempt", json::i(existing.attempt)),
        ]);
    }
    let id = state.next("failures");
    state.failures.push(Failure {
        id,
        task_id: task_id.into(),
        error,
        attempt: 1,
        max_retries: 3,
        first_attempt_at: now,
        last_attempt_at: now,
        dead_letter_at: None,
        status: "failed".into(),
    });
    json::obj(vec![
        ("recorded", json::b(true)),
        ("status", json::s("failed")),
        ("attempt", json::i(1)),
    ])
}

fn task_value(task: &Task) -> Value {
    let depends = json::parse(&task.depends_on).unwrap_or_else(|_| json::arr(vec![]));
    json::obj(vec![
        ("id", json::s(&task.id)),
        ("parent_agent_id", json::s(&task.parent_agent_id)),
        ("parent_task_id", json::opt_string(&task.parent_task_id)),
        (
            "assigned_agent_id",
            json::opt_string(&task.assigned_agent_id),
        ),
        ("description", json::s(&task.description)),
        ("status", json::s(&task.status)),
        ("created_at", json::f(task.created_at)),
        ("updated_at", json::f(task.updated_at)),
        ("depends_on", depends),
        ("blocked_by", json::opt_string(&task.blocked_by)),
        ("summary", json::opt_string(&task.summary)),
        ("priority", json::i(task.priority)),
        ("error", json::opt_string(&task.error)),
    ])
}

pub fn query_tasks(
    state: &State,
    query_type: &str,
    task_id: Option<&str>,
    parent_agent: Option<&str>,
    assigned: Option<&str>,
    parent_task: Option<&str>,
    root_task: Option<&str>,
) -> Value {
    match query_type {
        "task" => {
            let Some(id) = task_id else {
                return json::obj(vec![(
                    "error",
                    json::s("task_id is required for query_type='task'"),
                )]);
            };
            match state.tasks.iter().find(|task| task.id == id) {
                Some(task) => json::obj(vec![("task", task_value(task))]),
                None => json::obj(vec![(
                    "error",
                    json::s(format!("Task {}", json::py_repr(&json::s(id)))),
                )]),
            }
        }
        "child" => {
            let Some(id) = parent_agent else {
                return json::obj(vec![(
                    "error",
                    json::s("parent_agent_id is required for query_type='child'"),
                )]);
            };
            let tasks: Vec<Value> = state
                .tasks
                .iter()
                .filter(|task| task.parent_agent_id == id)
                .map(task_value)
                .collect();
            let count = tasks.len() as i64;
            json::obj(vec![("tasks", json::arr(tasks)), ("count", json::i(count))])
        }
        "by_agent" => {
            let Some(id) = assigned else {
                return json::obj(vec![(
                    "error",
                    json::s("assigned_agent_id is required for query_type='by_agent'"),
                )]);
            };
            let tasks: Vec<Value> = state
                .tasks
                .iter()
                .filter(|task| task.assigned_agent_id.as_deref() == Some(id))
                .map(task_value)
                .collect();
            let count = tasks.len() as i64;
            json::obj(vec![("tasks", json::arr(tasks)), ("count", json::i(count))])
        }
        "all" => {
            let tasks: Vec<Value> = state.tasks.iter().map(task_value).collect();
            let count = tasks.len() as i64;
            json::obj(vec![("tasks", json::arr(tasks)), ("count", json::i(count))])
        }
        "subtasks" => {
            let Some(id) = parent_task else {
                return json::obj(vec![(
                    "error",
                    json::s("parent_task_id is required for query_type='subtasks'"),
                )]);
            };
            let mut tasks: Vec<&Task> = state
                .tasks
                .iter()
                .filter(|task| task.parent_task_id.as_deref() == Some(id))
                .collect();
            tasks.sort_by(|a, b| {
                b.priority.cmp(&a.priority).then_with(|| {
                    a.created_at
                        .partial_cmp(&b.created_at)
                        .unwrap_or(std::cmp::Ordering::Equal)
                })
            });
            let values: Vec<Value> = tasks.into_iter().map(task_value).collect();
            let count = values.len() as i64;
            json::obj(vec![
                ("subtasks", json::arr(values)),
                ("count", json::i(count)),
            ])
        }
        "tree" => {
            let Some(id) = root_task else {
                return json::obj(vec![(
                    "error",
                    json::s("root_task_id is required for query_type='tree'"),
                )]);
            };
            task_tree(state, id).unwrap_or_else(|| {
                json::obj(vec![(
                    "error",
                    json::s(format!("Task {}", json::py_repr(&json::s(id)))),
                )])
            })
        }
        other => json::obj(vec![(
            "error",
            json::s(format!(
                "Unknown query_type: {}",
                json::py_repr(&json::s(other))
            )),
        )]),
    }
}

fn task_tree(state: &State, root: &str) -> Option<Value> {
    fn walk(state: &State, id: &str, depth: i64, seen: &mut Vec<String>) -> Option<Value> {
        if depth >= 32 || seen.iter().any(|item| item == id) {
            return None;
        }
        let task = state.tasks.iter().find(|task| task.id == id)?;
        seen.push(id.to_string());
        let mut node = match task_value(task) {
            Value::Object(pairs) => pairs,
            other => return Some(other),
        };
        let mut children = Vec::new();
        for child in state
            .tasks
            .iter()
            .filter(|task| task.parent_task_id.as_deref() == Some(id))
        {
            if let Some(branch) = walk(state, &child.id, depth + 1, seen) {
                children.push(branch);
            }
        }
        node.push(("subtasks".into(), json::arr(children)));
        Some(Value::Object(node))
    }
    walk(state, root, 0, &mut Vec::new())
}

pub fn create_subtask(
    state: &mut State,
    task_id: &str,
    parent_task_id: &str,
    parent_agent_id: &str,
    description: &str,
    depends_on: Option<&[String]>,
    priority: i64,
) -> Result<Value, String> {
    if !state.tasks.iter().any(|task| task.id == parent_task_id) {
        return Ok(json::obj(vec![
            ("created", json::b(false)),
            ("reason", json::s("parent_not_found")),
            ("task_id", json::s(task_id)),
            ("parent_task_id", json::s(parent_task_id)),
        ]));
    }
    if would_cycle(state, task_id, parent_task_id) {
        return Ok(json::obj(vec![
            ("created", json::b(false)),
            ("reason", json::s("cycle")),
            ("task_id", json::s(task_id)),
            ("parent_task_id", json::s(parent_task_id)),
        ]));
    }
    create_task(
        state,
        task_id,
        parent_agent_id,
        description,
        depends_on,
        priority,
    )?;
    if let Some(task) = state.tasks.iter_mut().find(|task| task.id == task_id) {
        task.parent_task_id = Some(parent_task_id.into());
    }
    Ok(json::obj(vec![
        ("created", json::b(true)),
        ("task_id", json::s(task_id)),
        ("parent_task_id", json::s(parent_task_id)),
        ("priority", json::i(priority)),
    ]))
}

fn would_cycle(state: &State, task_id: &str, parent_task_id: &str) -> bool {
    let mut cursor = Some(parent_task_id.to_string());
    let mut depth = 0;
    let mut seen = Vec::new();
    while let Some(current) = cursor {
        if current == task_id || depth >= 32 || seen.iter().any(|item: &String| item == &current) {
            return current == task_id || depth >= 32;
        }
        seen.push(current.clone());
        cursor = state
            .tasks
            .iter()
            .find(|task| task.id == current)
            .and_then(|task| task.parent_task_id.clone());
        depth += 1;
    }
    false
}

pub fn available_tasks(state: &State, agent_id: Option<&str>) -> Value {
    let tasks: Vec<Value> = state
        .tasks
        .iter()
        .filter(|task| task.status == "pending")
        .filter(|task| {
            agent_id
                .map(|id| {
                    task.assigned_agent_id.as_deref() == Some(id)
                        || task.assigned_agent_id.is_none()
                })
                .unwrap_or(true)
        })
        .filter(|task| deps_done(state, task))
        .filter(|task| {
            task.assigned_agent_id
                .as_ref()
                .map(|id| !agent_blocked(state, id))
                .unwrap_or(true)
        })
        .map(task_value)
        .collect();
    let count = tasks.len() as i64;
    json::obj(vec![("tasks", json::arr(tasks)), ("count", json::i(count))])
}

fn deps_done(state: &State, task: &Task) -> bool {
    let Ok(Value::Array(items)) = json::parse(&task.depends_on) else {
        return true;
    };
    items.iter().filter_map(Value::as_str).all(|id| {
        state
            .tasks
            .iter()
            .any(|other| other.id == id && other.status == "completed")
    })
}

fn agent_blocked(state: &State, agent_id: &str) -> bool {
    state
        .dependencies
        .iter()
        .any(|dep| dep.dependent_agent_id == agent_id && dep.satisfied == 0)
}

pub fn task_failures(state: &mut State, action: &str, task_id: Option<&str>, limit: i64) -> Value {
    match action {
        "retry" => {
            let Some(id) = task_id else {
                return json::obj(vec![("error", json::s("task_id is required for retry"))]);
            };
            retry_task(state, id)
        }
        "list_dead_letter" => {
            let tasks: Vec<Value> = state
                .failures
                .iter()
                .filter(|row| row.status == "dead_letter")
                .take(limit.max(0) as usize)
                .map(|row| {
                    json::obj(vec![
                        ("id", json::i(row.id)),
                        ("task_id", json::s(&row.task_id)),
                        ("error", json::opt_string(&row.error)),
                        ("attempt", json::i(row.attempt)),
                        ("status", json::s(&row.status)),
                    ])
                })
                .collect();
            let count = tasks.len() as i64;
            json::obj(vec![
                ("dead_letter_tasks", json::arr(tasks)),
                ("count", json::i(count)),
            ])
        }
        "history" => {
            let Some(id) = task_id else {
                return json::obj(vec![("error", json::s("task_id is required for history"))]);
            };
            let history: Vec<Value> = state
                .failures
                .iter()
                .filter(|row| row.task_id == id)
                .map(|row| {
                    json::obj(vec![
                        ("id", json::i(row.id)),
                        ("task_id", json::s(&row.task_id)),
                        ("error", json::opt_string(&row.error)),
                        ("attempt", json::i(row.attempt)),
                        ("max_retries", json::i(row.max_retries)),
                        ("first_attempt_at", json::f(row.first_attempt_at)),
                        ("last_attempt_at", json::f(row.last_attempt_at)),
                        (
                            "dead_letter_at",
                            match row.dead_letter_at {
                                Some(n) => json::f(n),
                                None => json::null(),
                            },
                        ),
                        ("status", json::s(&row.status)),
                    ])
                })
                .collect();
            let count = history.len() as i64;
            json::obj(vec![
                ("task_id", json::s(id)),
                ("history", json::arr(history)),
                ("count", json::i(count)),
            ])
        }
        other => json::obj(vec![(
            "error",
            json::s(format!(
                "Unknown action: {}",
                json::py_repr(&json::s(other))
            )),
        )]),
    }
}

fn retry_task(state: &mut State, task_id: &str) -> Value {
    let Some(row) = state
        .failures
        .iter_mut()
        .rev()
        .find(|row| row.task_id == task_id && row.status == "dead_letter")
    else {
        return json::obj(vec![
            ("retried", json::b(false)),
            ("reason", json::s("not_in_dead_letter")),
        ]);
    };
    row.status = "retried".into();
    row.dead_letter_at = None;
    if let Some(task) = state.tasks.iter_mut().find(|task| task.id == task_id) {
        task.status = "pending".into();
        task.updated_at = util::now();
    }
    json::obj(vec![
        ("retried", json::b(true)),
        ("task_id", json::s(task_id)),
    ])
}

pub fn send_message(
    state: &mut State,
    from: &str,
    to: &str,
    message_type: &str,
    payload: Option<&Value>,
    caller: Option<&str>,
) -> Value {
    if let Some(caller) = caller {
        if caller != from {
            return json::obj(vec![
                ("sent", json::b(false)),
                (
                    "error",
                    json::s("caller_agent_id does not match from_agent_id"),
                ),
                ("reason", json::s("caller_mismatch")),
            ]);
        }
    }
    let now = util::now();
    let payload_json =
        payload.map(|value| truncate(Some(json::dumps(value)), max_message()).unwrap_or_default());
    let id = state.next("messages");
    state.messages.push(Message {
        id,
        from_agent_id: from.into(),
        to_agent_id: to.into(),
        message_type: message_type.into(),
        payload_json,
        created_at: now,
        read_at: None,
    });
    json::obj(vec![("sent", json::b(true)), ("message_id", json::i(id))])
}

pub fn get_messages(
    state: &State,
    agent_id: &str,
    unread_only: bool,
    limit: i64,
    since_id: Option<i64>,
    caller: Option<&str>,
) -> Value {
    if let Some(caller) = caller {
        if caller != agent_id {
            return json::obj(vec![
                ("messages", json::arr(vec![])),
                ("count", json::i(0)),
                ("error", json::s("caller_agent_id does not match agent_id")),
                ("reason", json::s("caller_mismatch")),
            ]);
        }
    }
    let mut rows: Vec<&Message> = state
        .messages
        .iter()
        .filter(|row| row.to_agent_id == agent_id)
        .filter(|row| !unread_only || row.read_at.is_none())
        .filter(|row| since_id.map(|id| row.id > id).unwrap_or(true))
        .collect();
    rows.sort_by(|a, b| {
        b.created_at
            .partial_cmp(&a.created_at)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then(b.id.cmp(&a.id))
    });
    rows.truncate(limit.max(0) as usize);
    let items: Vec<Value> = rows.into_iter().map(message_value).collect();
    let count = items.len() as i64;
    json::obj(vec![
        ("messages", json::arr(items)),
        ("count", json::i(count)),
    ])
}

fn message_value(row: &Message) -> Value {
    let payload = row
        .payload_json
        .as_deref()
        .and_then(|raw| json::parse(raw).ok())
        .unwrap_or(Value::Null);
    json::obj(vec![
        ("id", json::i(row.id)),
        ("from_agent_id", json::s(&row.from_agent_id)),
        ("to_agent_id", json::s(&row.to_agent_id)),
        ("message_type", json::s(&row.message_type)),
        ("created_at", json::f(row.created_at)),
        (
            "read_at",
            match row.read_at {
                Some(n) => json::f(n),
                None => json::null(),
            },
        ),
        ("payload", payload),
    ])
}

pub fn mark_read(
    state: &mut State,
    agent_id: &str,
    ids: Option<&[i64]>,
    caller: Option<&str>,
) -> Value {
    if let Some(caller) = caller {
        if caller != agent_id {
            return json::obj(vec![
                ("marked_read", json::i(0)),
                ("error", json::s("caller_agent_id does not match agent_id")),
                ("reason", json::s("caller_mismatch")),
            ]);
        }
    }
    let now = util::now();
    let mut count = 0_i64;
    for row in &mut state.messages {
        if row.to_agent_id == agent_id
            && row.read_at.is_none()
            && ids.map(|list| list.contains(&row.id)).unwrap_or(true)
        {
            row.read_at = Some(now);
            count += 1;
        }
    }
    json::obj(vec![("marked_read", json::i(count))])
}

pub fn declare_intent(
    state: &mut State,
    agent_id: &str,
    document_path: &str,
    intent: &str,
    ttl: f64,
    project_root: Option<&std::path::Path>,
) -> Value {
    let path = normalize_path(document_path, project_root);
    let intent = truncate(Some(intent.to_string()), max_intent()).unwrap_or_default();
    let now = util::now();
    if let Some(row) = state
        .intents
        .iter_mut()
        .find(|row| row.agent_id == agent_id && row.document_path == path)
    {
        row.intent = intent;
        row.declared_at = now;
        row.ttl = ttl;
    } else {
        state.intents.push(Intent {
            agent_id: agent_id.into(),
            document_path: path.clone(),
            intent,
            declared_at: now,
            ttl,
        });
    }
    json::obj(vec![
        ("recorded", json::b(true)),
        ("agent_id", json::s(agent_id)),
        ("document_path", json::s(path)),
    ])
}

pub fn list_intents(state: &State, agent_id: Option<&str>) -> Value {
    let now = util::now();
    let mut rows: Vec<&Intent> = state
        .intents
        .iter()
        .filter(|row| row.declared_at + row.ttl > now)
        .filter(|row| agent_id.map(|id| row.agent_id == id).unwrap_or(true))
        .collect();
    if agent_id.is_none() {
        rows.sort_by(|a, b| {
            b.declared_at
                .partial_cmp(&a.declared_at)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
    }
    let items: Vec<Value> = rows
        .into_iter()
        .map(|row| {
            json::obj(vec![
                ("agent_id", json::s(&row.agent_id)),
                ("document_path", json::s(&row.document_path)),
                ("intent", json::s(&row.intent)),
                ("declared_at", json::f(row.declared_at)),
                ("ttl", json::f(row.ttl)),
            ])
        })
        .collect();
    let count = items.len() as i64;
    json::obj(vec![
        ("intents", json::arr(items)),
        ("count", json::i(count)),
    ])
}

pub fn clear_intent(
    state: &mut State,
    agent_id: &str,
    document_path: Option<&str>,
    project_root: Option<&Path>,
) -> Value {
    let norm = document_path.map(|path| normalize_path(path, project_root));
    let before = state.intents.len();
    state.intents.retain(|row| {
        !(row.agent_id == agent_id
            && norm
                .as_ref()
                .map(|path| row.document_path == *path)
                .unwrap_or(true))
    });
    json::obj(vec![
        ("cleared", json::b(true)),
        ("agent_id", json::s(agent_id)),
        ("document_path", json::opt_string(&norm)),
        (
            "rows_cleared",
            json::i((before - state.intents.len()) as i64),
        ),
    ])
}

pub fn manage_deps(
    state: &mut State,
    mode: &str,
    agent_id: Option<&str>,
    dependent: Option<&str>,
    depends_on: Option<&str>,
    task: Option<&str>,
    condition: &str,
    dep_id: Option<i64>,
) -> Value {
    match mode {
        "declare" => {
            let (Some(dependent), Some(depends_on)) = (dependent, depends_on) else {
                return json::obj(vec![(
                    "error",
                    json::s("dependent_agent_id and depends_on_agent_id are required for declare"),
                )]);
            };
            let id = state.next("dependencies");
            state.dependencies.push(Dependency {
                id,
                dependent_agent_id: dependent.into(),
                depends_on_agent_id: depends_on.into(),
                depends_on_task_id: task.map(|s| s.to_string()),
                condition: condition.into(),
                satisfied: 0,
                satisfied_at: None,
                created_at: util::now(),
            });
            json::obj(vec![("declared", json::b(true)), ("dep_id", json::i(id))])
        }
        "check" | "blockers" => {
            let Some(agent_id) = agent_id else {
                return json::obj(vec![(
                    "error",
                    json::s("agent_id is required for check/blockers"),
                )]);
            };
            let blockers = unsatisfied(state, agent_id);
            json::obj(vec![
                ("agent_id", json::s(agent_id)),
                ("blocked", json::b(!blockers.is_empty())),
                ("unsatisfied", json::arr(blockers)),
            ])
        }
        "assert" => {
            let Some(agent_id) = agent_id else {
                return json::obj(vec![("error", json::s("agent_id is required for assert"))]);
            };
            let blockers = unsatisfied(state, agent_id);
            if blockers.is_empty() {
                json::obj(vec![("can_start", json::b(true))])
            } else {
                json::obj(vec![
                    ("can_start", json::b(false)),
                    ("blockers", json::arr(blockers)),
                ])
            }
        }
        "satisfy" => {
            let Some(dep_id) = dep_id else {
                return json::obj(vec![("error", json::s("dep_id is required for satisfy"))]);
            };
            if let Some(dep) = state.dependencies.iter_mut().find(|dep| dep.id == dep_id) {
                dep.satisfied = 1;
                dep.satisfied_at = Some(util::now());
                json::obj(vec![
                    ("satisfied", json::b(true)),
                    ("dep_id", json::i(dep_id)),
                ])
            } else {
                json::obj(vec![
                    ("satisfied", json::b(false)),
                    ("dep_id", json::i(dep_id)),
                    ("reason", json::s("not_found")),
                ])
            }
        }
        "list" => {
            let rows = dep_rows(state, agent_id);
            let count = rows.len() as i64;
            json::obj(vec![
                ("dependencies", json::arr(rows)),
                ("count", json::i(count)),
            ])
        }
        "wait" => {
            let Some(dep_id) = dep_id else {
                return json::obj(vec![("error", json::s("dep_id is required for wait"))]);
            };
            match state.dependencies.iter().find(|dep| dep.id == dep_id) {
                Some(dep) if dep.satisfied == 1 => json::obj(vec![
                    ("satisfied", json::b(true)),
                    ("dep_id", json::i(dep_id)),
                    ("timed_out", json::b(false)),
                ]),
                Some(_) => json::obj(vec![
                    ("satisfied", json::b(false)),
                    ("dep_id", json::i(dep_id)),
                    ("timed_out", json::b(true)),
                ]),
                None => json::obj(vec![
                    ("satisfied", json::b(false)),
                    ("dep_id", json::i(dep_id)),
                    ("timed_out", json::b(true)),
                    ("reason", json::s("not_found")),
                ]),
            }
        }
        other => json::obj(vec![(
            "error",
            json::s(format!("Unknown mode: {}", json::py_repr(&json::s(other)))),
        )]),
    }
}

fn unsatisfied(state: &State, agent_id: &str) -> Vec<Value> {
    state
        .dependencies
        .iter()
        .filter(|dep| dep.dependent_agent_id == agent_id && dep.satisfied == 0)
        .map(dep_value)
        .collect()
}

fn dep_rows(state: &State, agent_id: Option<&str>) -> Vec<Value> {
    state
        .dependencies
        .iter()
        .filter(|dep| {
            agent_id
                .map(|id| dep.dependent_agent_id == id)
                .unwrap_or(true)
        })
        .map(dep_value)
        .collect()
}

fn dep_value(dep: &Dependency) -> Value {
    json::obj(vec![
        ("id", json::i(dep.id)),
        ("dependent_agent_id", json::s(&dep.dependent_agent_id)),
        ("depends_on_agent_id", json::s(&dep.depends_on_agent_id)),
        (
            "depends_on_task_id",
            json::opt_string(&dep.depends_on_task_id),
        ),
        ("condition", json::s(&dep.condition)),
        ("satisfied", json::i(dep.satisfied)),
        (
            "satisfied_at",
            match dep.satisfied_at {
                Some(n) => json::f(n),
                None => json::null(),
            },
        ),
        ("created_at", json::f(dep.created_at)),
    ])
}

pub fn acquire_lease(state: &mut State, agent_id: &str, ttl: Option<f64>) -> Value {
    let ttl = ttl.unwrap_or(DEFAULT_LEASE_TTL);
    let now = util::now();
    let expires = now + ttl;
    if let Some(lease) = state
        .leases
        .iter_mut()
        .find(|lease| lease.lease_name == COORDINATOR)
    {
        if lease.holder_id != agent_id && lease.expires_at > now {
            let holder = lease_value(lease);
            return json::obj(vec![
                ("acquired", json::b(false)),
                ("lease_name", json::s(COORDINATOR)),
                ("holder", holder),
            ]);
        }
        lease.holder_id = agent_id.into();
        lease.acquired_at = now;
        lease.ttl = ttl;
        lease.expires_at = expires;
    } else {
        state.leases.push(crate::db::Lease {
            lease_name: COORDINATOR.into(),
            holder_id: agent_id.into(),
            acquired_at: now,
            ttl,
            expires_at: expires,
        });
    }
    json::obj(vec![
        ("acquired", json::b(true)),
        ("lease_name", json::s(COORDINATOR)),
        ("holder_id", json::s(agent_id)),
        ("ttl", whole(ttl)),
        ("expires_at", json::f(expires)),
    ])
}

fn whole(n: f64) -> Value {
    if n.fract() == 0.0 && n.abs() < 1e15 {
        json::i(n as i64)
    } else {
        json::f(n)
    }
}

pub fn refresh_lease(state: &mut State, agent_id: &str) -> Value {
    let now = util::now();
    let Some(lease) = state
        .leases
        .iter_mut()
        .find(|lease| lease.lease_name == COORDINATOR)
    else {
        return json::obj(vec![
            ("refreshed", json::b(false)),
            ("error", json::s("Not the current lease holder")),
        ]);
    };
    if lease.holder_id != agent_id {
        return json::obj(vec![
            ("refreshed", json::b(false)),
            ("error", json::s("Not the current lease holder")),
        ]);
    }
    lease.acquired_at = now;
    lease.expires_at = now + lease.ttl;
    let expires = lease.expires_at;
    json::obj(vec![
        ("refreshed", json::b(true)),
        ("lease_name", json::s(COORDINATOR)),
        ("expires_at", json::f(expires)),
    ])
}

pub fn release_lease_tool(state: &mut State, agent_id: &str) -> Value {
    let Some(pos) = state
        .leases
        .iter()
        .position(|lease| lease.lease_name == COORDINATOR)
    else {
        return json::obj(vec![
            ("released", json::b(false)),
            ("error", json::s("Not the current lease holder")),
        ]);
    };
    if state.leases[pos].holder_id != agent_id {
        return json::obj(vec![
            ("released", json::b(false)),
            ("error", json::s("Not the current lease holder")),
        ]);
    }
    state.leases.remove(pos);
    json::obj(vec![
        ("released", json::b(true)),
        ("lease_name", json::s(COORDINATOR)),
    ])
}

pub fn leader(state: &State) -> Value {
    match live_lease(state) {
        Some(lease) => json::obj(vec![("leader", lease_value(lease))]),
        None => json::obj(vec![("leader", json::null())]),
    }
}

fn live_lease(state: &State) -> Option<&crate::db::Lease> {
    let now = util::now();
    state
        .leases
        .iter()
        .find(|lease| lease.lease_name == COORDINATOR && lease.expires_at > now)
}

fn lease_value(lease: &crate::db::Lease) -> Value {
    json::obj(vec![
        ("lease_name", json::s(&lease.lease_name)),
        ("holder_id", json::s(&lease.holder_id)),
        ("acquired_at", json::f(lease.acquired_at)),
        ("ttl", json::f(lease.ttl)),
        ("expires_at", json::f(lease.expires_at)),
    ])
}

pub fn claim_leadership(state: &mut State, agent_id: &str, ttl: Option<f64>) -> Value {
    if live_lease(state)
        .map(|lease| lease.holder_id != agent_id)
        .unwrap_or(false)
    {
        let holder = live_lease(state).map(lease_value).unwrap_or(json::null());
        return json::obj(vec![
            ("claimed", json::b(false)),
            ("error", json::s("Leadership is held by a live agent")),
            ("lease_name", json::s(COORDINATOR)),
            ("holder", holder),
        ]);
    }
    let got = acquire_lease(state, agent_id, ttl);
    json::obj(vec![
        ("claimed", json::b(true)),
        ("lease_name", json::s(COORDINATOR)),
        ("holder_id", json::s(agent_id)),
        (
            "ttl",
            got.get("ttl").cloned().unwrap_or(whole(DEFAULT_LEASE_TTL)),
        ),
        (
            "expires_at",
            got.get("expires_at").cloned().unwrap_or(json::null()),
        ),
    ])
}

pub fn spawn(
    state: &mut State,
    parent: &str,
    subagent_type: Option<&str>,
    description: Option<&str>,
    prompt: Option<&str>,
    source: &str,
) -> Value {
    let valid_sources = ["cc", "external", "stdio_adapter"];
    if let Some(kind) = subagent_type {
        if !kind
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || ch == '_' || ch == '-')
            || kind.is_empty()
        {
            return json::obj(vec![
                ("stashed", json::b(false)),
                ("reason", json::s("invalid_subagent_type")),
                ("subagent_type", json::s(kind)),
            ]);
        }
    }
    if !valid_sources.contains(&source) {
        return json::obj(vec![
            ("stashed", json::b(false)),
            ("reason", json::s("invalid_source")),
            ("source", json::s(source)),
            (
                "valid_sources",
                json::string_list(
                    &valid_sources
                        .iter()
                        .map(|s| (*s).to_string())
                        .collect::<Vec<_>>(),
                ),
            ),
        ]);
    }
    let now = util::now();
    let ttl = util::env_f64("COORDINATIONHUB_SPAWN_TTL_SECONDS", 600.0);
    for row in &mut state.spawns {
        if row.status == "pending" && row.created_at < now - ttl {
            row.status = "expired".into();
        }
    }
    let pending = state
        .spawns
        .iter()
        .filter(|row| row.status == "pending")
        .count() as i64;
    let cap = util::env_i64("COORDINATIONHUB_MAX_PENDING_SPAWNS", 1000);
    if pending >= cap {
        return json::obj(vec![
            ("stashed", json::b(false)),
            ("reason", json::s("max_pending_spawns_reached")),
            ("pending_count", json::i(pending)),
            ("max_pending_spawns", json::i(cap)),
        ]);
    }
    let Some(kind) = subagent_type else {
        return json::obj(vec![(
            "error",
            json::s("subagent_type is required when spawn_id is not supplied"),
        )]);
    };
    let seq = state
        .spawns
        .iter()
        .filter(|row| row.parent_agent_id == parent && row.subagent_type.as_deref() == Some(kind))
        .count();
    let spawn_id = format!("{parent}.{kind}.{seq}");
    state.spawns.push(Spawn {
        id: spawn_id.clone(),
        parent_agent_id: parent.into(),
        subagent_type: Some(kind.into()),
        description: truncate(description.map(|s| s.to_string()), max_description()),
        prompt: truncate(prompt.map(|s| s.to_string()), max_prompt()),
        created_at: now,
        consumed_at: None,
        status: "pending".into(),
        source: source.into(),
        child_agent_id: None,
        stop_requested: false,
    });
    json::obj(vec![
        ("stashed", json::b(true)),
        ("spawn_id", json::s(spawn_id)),
        ("parent_agent_id", json::s(parent)),
    ])
}

pub fn report_spawned(
    state: &mut State,
    parent: &str,
    kind: Option<&str>,
    child: &str,
    source: &str,
) -> Value {
    if let Some(row) = state.spawns.iter_mut().find(|row| {
        row.parent_agent_id == parent
            && row.status == "pending"
            && kind
                .map(|k| row.subagent_type.as_deref() == Some(k))
                .unwrap_or(true)
    }) {
        row.status = "registered".into();
        row.consumed_at = Some(util::now());
        row.child_agent_id = Some(child.into());
        row.source = source.into();
        let spawn_id = row.id.clone();
        let description = row.description.clone();
        let prompt = row.prompt.clone();
        row.status = "registered".into();
        row.consumed_at = Some(util::now());
        row.child_agent_id = Some(child.into());
        row.source = source.into();
        return json::obj(vec![
            ("reported", json::b(true)),
            ("parent_agent_id", json::s(parent)),
            ("child_agent_id", json::s(child)),
            ("spawn_id", json::s(spawn_id)),
            ("description", json::opt_string(&description)),
            ("prompt", json::opt_string(&prompt)),
        ]);
    }
    json::obj(vec![
        ("reported", json::b(false)),
        ("reason", json::s("no_pending_spawn")),
        ("parent_agent_id", json::s(parent)),
    ])
}

pub fn pending_spawns(state: &State, parent: &str, include_consumed: bool) -> Value {
    let rows: Vec<Value> = state
        .spawns
        .iter()
        .filter(|row| {
            row.parent_agent_id == parent && (include_consumed || row.status == "pending")
        })
        .map(|row| {
            json::obj(vec![
                ("task_id", json::s(&row.id)),
                ("scope_id", json::s(&row.parent_agent_id)),
                ("subagent_type", json::opt_string(&row.subagent_type)),
                ("description", json::opt_string(&row.description)),
                ("prompt", json::opt_string(&row.prompt)),
                ("created_at", json::f(row.created_at)),
                (
                    "consumed_at",
                    match row.consumed_at {
                        Some(n) => json::f(n),
                        None => json::null(),
                    },
                ),
                ("status", json::s(&row.status)),
                ("source", json::s(&row.source)),
            ])
        })
        .collect();
    json::arr(rows)
}

pub fn request_stop(state: &mut State, parent: &str, child: &str) -> Value {
    let Some(agent) = state.agent(child) else {
        return json::obj(vec![
            ("not_found", json::b(true)),
            ("child_agent_id", json::s(child)),
        ]);
    };
    if agent.parent_id.as_deref() != Some(parent) && agent.status == "stopped" {
        return json::obj(vec![
            ("not_found", json::b(true)),
            ("child_agent_id", json::s(child)),
            ("status", json::s(&agent.status)),
        ]);
    }
    if let Some(row) = state
        .spawns
        .iter_mut()
        .find(|row| row.child_agent_id.as_deref() == Some(child))
    {
        row.stop_requested = true;
    } else {
        state.spawns.push(Spawn {
            id: format!("stop.{child}"),
            parent_agent_id: parent.into(),
            subagent_type: None,
            description: None,
            prompt: None,
            created_at: util::now(),
            consumed_at: None,
            status: "stop".into(),
            source: "external".into(),
            child_agent_id: Some(child.into()),
            stop_requested: true,
        });
    }
    json::obj(vec![
        ("requested", json::b(true)),
        ("child_agent_id", json::s(child)),
        ("requested_by", json::s(parent)),
    ])
}

pub fn stop_requested(state: &State, agent_id: &str) -> Value {
    let requested = state
        .spawns
        .iter()
        .any(|row| row.child_agent_id.as_deref() == Some(agent_id) && row.stop_requested);
    json::obj(vec![
        ("agent_id", json::s(agent_id)),
        ("stop_requested", json::b(requested)),
    ])
}

pub fn broadcast(
    state: &mut State,
    agent_id: &str,
    document_path: Option<&str>,
    ttl: f64,
    handoff_targets: Option<&[String]>,
    require_ack: bool,
    message: Option<&str>,
    project_root: Option<&Path>,
) -> Value {
    if let Some(targets) = handoff_targets {
        if !targets.is_empty() {
            let recorded = record_handoff(state, agent_id, targets, document_path, project_root);
            let id = recorded.get("handoff_id").cloned().unwrap_or(json::null());
            return json::obj(vec![
                ("handoff_id", id),
                ("to_agents", json::string_list(targets)),
                ("document_path", json::opt_str(document_path)),
                ("handoff_type", json::s("scope_transfer")),
            ]);
        }
    }
    let now = util::now();
    let live: Vec<String> = act_identity::siblings(state, agent_id)
        .into_iter()
        .filter(|row| {
            now - row
                .get("last_heartbeat")
                .and_then(Value::as_f64)
                .unwrap_or(0.0)
                <= ttl
        })
        .filter_map(|row| {
            row.get("agent_id")
                .and_then(Value::as_str)
                .map(|s| s.to_string())
        })
        .collect();
    if require_ack && !live.is_empty() {
        let id = state.next("broadcasts");
        state.broadcasts.push(Broadcast {
            id,
            from_agent_id: agent_id.into(),
            document_path: document_path.map(|s| s.to_string()),
            message: message.map(|s| s.to_string()),
            created_at: now,
            ttl,
            expires_at: now + ttl,
            expected_count: live.len() as i64,
        });
        for sib in &live {
            state.broadcast_targets.push(BroadcastTarget {
                broadcast_id: id,
                agent_id: sib.clone(),
            });
            let payload = json::obj(vec![
                ("broadcast_id", json::i(id)),
                ("document_path", json::opt_str(document_path)),
                ("message", json::opt_str(message)),
            ]);
            let _ = send_message(
                state,
                agent_id,
                sib,
                "broadcast_ack_request",
                Some(&payload),
                None,
            );
        }
        return json::obj(vec![
            ("broadcast_id", json::i(id)),
            ("acknowledged_by", json::arr(vec![])),
            ("pending_acks", json::string_list(&live)),
            ("conflicts", json::arr(vec![])),
        ]);
    }
    let mut conflicts = Vec::new();
    if let Some(path) = document_path {
        if !live.is_empty() {
            let norm = normalize_path(path, project_root);
            let worktree = project_root.map(|root| util::path_str(root));
            for lock in &state.locks {
                if lock.document_path == norm
                    && live.iter().any(|id| id == &lock.locked_by)
                    && lock.locked_by != agent_id
                {
                    let same = worktree
                        .as_ref()
                        .map(|root| {
                            lock.worktree_root
                                .as_deref()
                                .map(|stored| stored == root)
                                .unwrap_or(true)
                        })
                        .unwrap_or(true);
                    if same {
                        conflicts.push(json::obj(vec![
                            ("document_path", json::s(path)),
                            ("locked_by", json::s(&lock.locked_by)),
                        ]));
                    }
                }
            }
        }
    }
    json::obj(vec![
        ("acknowledged_by", json::arr(vec![])),
        ("conflicts", json::arr(conflicts)),
    ])
}

pub fn ack_broadcast(state: &mut State, broadcast_id: i64, agent_id: &str) -> Value {
    let now = util::now();
    let Some(row) = state
        .broadcasts
        .iter()
        .find(|row| row.id == broadcast_id && row.expires_at > now)
    else {
        return json::obj(vec![
            ("acknowledged", json::b(false)),
            ("reason", json::s("expired_or_not_found")),
        ]);
    };
    let _ = row;
    if state
        .broadcast_acks
        .iter()
        .any(|ack| ack.broadcast_id == broadcast_id && ack.agent_id == agent_id)
    {
        return json::obj(vec![
            ("acknowledged", json::b(true)),
            ("broadcast_id", json::i(broadcast_id)),
            ("agent_id", json::s(agent_id)),
            ("already_acked", json::b(true)),
        ]);
    }
    state.broadcast_acks.push(BroadcastAck {
        broadcast_id,
        agent_id: agent_id.into(),
        acknowledged_at: now,
    });
    json::obj(vec![
        ("acknowledged", json::b(true)),
        ("broadcast_id", json::i(broadcast_id)),
        ("agent_id", json::s(agent_id)),
        ("already_acked", json::b(false)),
    ])
}

pub fn broadcast_status(state: &State, broadcast_id: i64) -> Value {
    let Some(row) = state.broadcasts.iter().find(|row| row.id == broadcast_id) else {
        return json::obj(vec![("found", json::b(false))]);
    };
    let acked: Vec<String> = state
        .broadcast_acks
        .iter()
        .filter(|ack| ack.broadcast_id == broadcast_id)
        .map(|ack| ack.agent_id.clone())
        .collect();
    let pending: Vec<String> = state
        .broadcast_targets
        .iter()
        .filter(|target| {
            target.broadcast_id == broadcast_id && !acked.iter().any(|id| id == &target.agent_id)
        })
        .map(|target| target.agent_id.clone())
        .collect();
    json::obj(vec![
        ("found", json::b(true)),
        ("broadcast_id", json::i(broadcast_id)),
        ("from_agent_id", json::s(&row.from_agent_id)),
        ("document_path", json::opt_string(&row.document_path)),
        ("message", json::opt_string(&row.message)),
        ("expected_count", json::i(row.expected_count)),
        ("acknowledged_by", json::string_list(&acked)),
        ("pending_acks", json::string_list(&pending)),
        ("expires_at", json::f(row.expires_at)),
    ])
}

pub fn record_handoff(
    state: &mut State,
    from: &str,
    targets: &[String],
    document_path: Option<&str>,
    project_root: Option<&Path>,
) -> Value {
    let id = state.next("handoffs");
    let path = document_path.map(|path| normalize_path(path, project_root));
    state.handoffs.push(Handoff {
        id,
        from_agent_id: from.into(),
        to_agents: json::dumps(&json::string_list(targets)),
        document_path: path,
        handoff_type: "scope_transfer".into(),
        status: "pending".into(),
        created_at: util::now(),
        acknowledged_at: None,
        completed_at: None,
    });
    for target in targets {
        let payload = json::obj(vec![
            ("handoff_id", json::i(id)),
            ("document_path", json::opt_str(document_path)),
        ]);
        let _ = send_message(state, from, target, "handoff", Some(&payload), None);
    }
    json::obj(vec![
        ("recorded", json::b(true)),
        ("handoff_id", json::i(id)),
    ])
}

pub fn ack_handoff(state: &mut State, handoff_id: i64, agent_id: &str) -> Value {
    let Some(pos) = state.handoffs.iter().position(|row| row.id == handoff_id) else {
        return json::obj(vec![
            ("acknowledged", json::b(false)),
            ("reason", json::s("not_found")),
            ("handoff_id", json::i(handoff_id)),
        ]);
    };
    let targets = json::parse(&state.handoffs[pos].to_agents)
        .ok()
        .and_then(|v| {
            v.as_array().map(|items| {
                items
                    .iter()
                    .filter_map(Value::as_str)
                    .map(|s| s.to_string())
                    .collect::<Vec<_>>()
            })
        })
        .unwrap_or_default();
    if !targets.iter().any(|id| id == agent_id) {
        return json::obj(vec![
            ("acknowledged", json::b(false)),
            ("reason", json::s("not_recipient")),
            ("handoff_id", json::i(handoff_id)),
            ("agent_id", json::s(agent_id)),
        ]);
    }
    let status = state.handoffs[pos].status.clone();
    if status == "completed" || status == "cancelled" {
        return json::obj(vec![
            ("acknowledged", json::b(false)),
            (
                "reason",
                json::s(format!("illegal_transition_from_{status}")),
            ),
            ("handoff_id", json::i(handoff_id)),
        ]);
    }
    let now = util::now();
    if !state
        .handoff_acks
        .iter()
        .any(|ack| ack.handoff_id == handoff_id && ack.agent_id == agent_id)
    {
        state.handoff_acks.push(HandoffAck {
            handoff_id,
            agent_id: agent_id.into(),
            acknowledged_at: now,
        });
    }
    let acks = state
        .handoff_acks
        .iter()
        .filter(|ack| ack.handoff_id == handoff_id)
        .count() as i64;
    let expected = targets.len() as i64;
    let aggregate = if expected > 0 && acks >= expected {
        state.handoffs[pos].status = "acknowledged".into();
        state.handoffs[pos].acknowledged_at = Some(now);
        "acknowledged"
    } else {
        state.handoffs[pos].status = "partially_acknowledged".into();
        "partially_acknowledged"
    };
    json::obj(vec![
        ("acknowledged", json::b(true)),
        ("handoff_id", json::i(handoff_id)),
        ("agent_id", json::s(agent_id)),
        ("status", json::s(aggregate)),
        ("acks", json::i(acks)),
        ("expected", json::i(expected)),
    ])
}

pub fn finish_handoff(state: &mut State, handoff_id: i64, complete: bool) -> Value {
    let Some(row) = state.handoffs.iter_mut().find(|row| row.id == handoff_id) else {
        return json::obj(vec![
            (
                if complete { "completed" } else { "cancelled" },
                json::b(false),
            ),
            ("reason", json::s("not_found")),
            ("handoff_id", json::i(handoff_id)),
        ]);
    };
    if complete {
        row.status = "completed".into();
        row.completed_at = Some(util::now());
        json::obj(vec![
            ("completed", json::b(true)),
            ("handoff_id", json::i(handoff_id)),
        ])
    } else {
        row.status = "cancelled".into();
        json::obj(vec![
            ("cancelled", json::b(true)),
            ("handoff_id", json::i(handoff_id)),
        ])
    }
}

pub fn handoff_rows(state: &State, status: Option<&str>, from: Option<&str>, limit: i64) -> Value {
    let rows: Vec<Value> = state
        .handoffs
        .iter()
        .filter(|row| status.map(|s| row.status == s).unwrap_or(true))
        .filter(|row| from.map(|id| row.from_agent_id == id).unwrap_or(true))
        .take(limit.max(0) as usize)
        .map(|row| {
            json::obj(vec![
                ("id", json::i(row.id)),
                ("from_agent_id", json::s(&row.from_agent_id)),
                (
                    "to_agents",
                    json::parse(&row.to_agents).unwrap_or_else(|_| json::s(&row.to_agents)),
                ),
                ("document_path", json::opt_string(&row.document_path)),
                ("handoff_type", json::s(&row.handoff_type)),
                ("status", json::s(&row.status)),
                ("created_at", json::f(row.created_at)),
                (
                    "acknowledged_at",
                    match row.acknowledged_at {
                        Some(n) => json::f(n),
                        None => json::null(),
                    },
                ),
                (
                    "completed_at",
                    match row.completed_at {
                        Some(n) => json::f(n),
                        None => json::null(),
                    },
                ),
            ])
        })
        .collect();
    let count = rows.len() as i64;
    json::obj(vec![
        ("handoffs", json::arr(rows)),
        ("count", json::i(count)),
    ])
}

pub fn update_agent_status(
    state: &mut State,
    agent_id: &str,
    current_task: Option<String>,
    scope: Option<Vec<String>>,
) -> Value {
    if state.agent(agent_id).is_none() {
        return json::obj(vec![
            ("updated", json::b(false)),
            ("error", json::s(format!("Agent not found: {agent_id}"))),
        ]);
    }
    let now = util::now();
    let current_task = truncate(current_task, max_task());
    if state
        .responsibilities
        .iter()
        .any(|row| row.agent_id == agent_id)
    {
        let resp = state
            .responsibilities
            .iter_mut()
            .find(|row| row.agent_id == agent_id)
            .unwrap();
        if current_task.is_some() {
            resp.current_task = current_task;
        }
        if let Some(scope) = scope {
            resp.scope = Some(json::dumps(&json::string_list(&scope)));
        }
        resp.updated_at = now;
    } else {
        state.responsibilities.push(crate::db::Responsibility {
            agent_id: agent_id.into(),
            graph_agent_id: None,
            role: None,
            model: None,
            responsibilities: None,
            current_task,
            scope: scope.map(|items| json::dumps(&json::string_list(&items))),
            updated_at: now,
        });
    }
    json::obj(vec![
        ("updated", json::b(true)),
        ("agent_id", json::s(agent_id)),
    ])
}

pub fn system_status(state: &State, graph_loaded: bool, tool_count: i64) -> Value {
    let now = util::now();
    let registered = state
        .agents
        .iter()
        .filter(|agent| agent.status == "active")
        .count() as i64;
    let active = state
        .agents
        .iter()
        .filter(|agent| agent.status == "active" && agent.last_heartbeat > now - 600.0)
        .count() as i64;
    json::obj(vec![
        ("registered_agents", json::i(registered)),
        ("active_agents", json::i(active)),
        ("active_locks", json::i(state.locks.len() as i64)),
        (
            "pending_notifications",
            json::i(state.notifications.len() as i64),
        ),
        ("recent_conflicts", json::i(state.conflicts.len() as i64)),
        ("owned_files", json::i(state.ownership.len() as i64)),
        ("graph_loaded", json::b(graph_loaded)),
        ("tools", json::i(tool_count)),
    ])
}
