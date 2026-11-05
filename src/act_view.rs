//! Graph, scan, trees, assessment, ownership claims, and dashboard rows.

use crate::db::{Claim, ClaimEvent, Ownership, Responsibility, State};
use crate::json::{self, Value};
use crate::util::{self, normalize_path};
use std::path::{Path, PathBuf};

#[derive(Clone, Debug)]
pub struct Graph {
    pub path: String,
    pub agents: Vec<GraphAgent>,
    pub handoffs: Vec<(String, String)>,
    pub raw_agents: Vec<String>,
}

#[derive(Clone, Debug)]
pub struct GraphAgent {
    pub id: String,
    pub role: String,
    pub model: String,
    pub responsibilities: Vec<String>,
}

pub fn load_spec(
    state: &mut State,
    project_root: Option<&Path>,
    path: Option<&str>,
) -> (Value, Option<Graph>) {
    let target = if let Some(path) = path {
        let file = PathBuf::from(path);
        if !file.is_file() {
            return (
                json::obj(vec![
                    ("loaded", json::b(false)),
                    (
                        "error",
                        json::s(format!("Coordination spec not found: {path}")),
                    ),
                ]),
                None,
            );
        }
        file
    } else if let Some(root) = project_root {
        let mut found = None;
        for name in [
            "coordination_spec.yaml",
            "coordination_spec.yml",
            "coordination_spec.json",
        ] {
            let candidate = root.join(name);
            if candidate.is_file() {
                found = Some(candidate);
                break;
            }
        }
        let Some(found) = found else {
            return (
                json::obj(vec![("loaded", json::b(false)), ("path", json::null())]),
                None,
            );
        };
        found
    } else {
        return (
            json::obj(vec![("loaded", json::b(false)), ("path", json::null())]),
            None,
        );
    };
    let ext = target
        .extension()
        .and_then(|ext| ext.to_str())
        .unwrap_or("");
    if ext == "yaml" || ext == "yml" {
        return (
            json::obj(vec![(
                "loaded",
                json::b(false),
            ), (
                "error",
                json::s("YAML support requires ruamel.yaml. Install it with: pip install coordinationhub[yaml] Or use coordination_spec.json instead."),
            )]),
            None,
        );
    }
    let text = match std::fs::read_to_string(&target) {
        Ok(text) => text,
        Err(err) => {
            return (
                json::obj(vec![
                    ("loaded", json::b(false)),
                    ("error", json::s(err.to_string())),
                ]),
                None,
            )
        }
    };
    let data = match json::parse(&text) {
        Ok(data) => data,
        Err(err) => {
            return (
                json::obj(vec![("loaded", json::b(false)), ("error", json::s(err))]),
                None,
            )
        }
    };
    let validation = validate_graph(&data);
    if validation.get("valid").and_then(Value::as_bool) != Some(true) {
        return (
            json::obj(vec![
                ("loaded", json::b(false)),
                (
                    "errors",
                    validation
                        .get("errors")
                        .cloned()
                        .unwrap_or(json::arr(vec![])),
                ),
            ]),
            None,
        );
    }
    let graph = graph_from(&data, &target);
    populate(state, &graph);
    let names = graph.raw_agents.clone();
    (
        json::obj(vec![
            ("loaded", json::b(true)),
            ("path", json::s(target.to_string_lossy().replace('\\', "/"))),
            ("agent_count", json::i(names.len() as i64)),
            ("agents", json::string_list(&names)),
        ]),
        Some(graph),
    )
}

fn graph_from(data: &Value, path: &Path) -> Graph {
    let mut agents = Vec::new();
    let mut names = Vec::new();
    if let Some(Value::Array(items)) = data.get("agents") {
        for item in items {
            let Some(id) = item.get("id").and_then(Value::as_str) else {
                continue;
            };
            names.push(id.to_string());
            let responsibilities = item
                .get("responsibilities")
                .and_then(Value::as_array)
                .map(|rows| {
                    rows.iter()
                        .filter_map(Value::as_str)
                        .map(|s| s.to_string())
                        .collect()
                })
                .unwrap_or_default();
            agents.push(GraphAgent {
                id: id.to_string(),
                role: item
                    .get("role")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string(),
                model: item
                    .get("model")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string(),
                responsibilities,
            });
        }
    }
    let mut handoffs = Vec::new();
    if let Some(Value::Array(items)) = data.get("handoffs") {
        for item in items {
            if let (Some(from), Some(to)) = (
                item.get("from").and_then(Value::as_str),
                item.get("to").and_then(Value::as_str),
            ) {
                handoffs.push((from.to_string(), to.to_string()));
            }
        }
    }
    Graph {
        path: path.display().to_string(),
        agents,
        handoffs,
        raw_agents: names,
    }
}

fn populate(state: &mut State, graph: &Graph) {
    let now = util::now();
    for agent in &graph.agents {
        if state.agent(&agent.id).is_none() {
            continue;
        }
        let encoded = json::dumps(&json::string_list(&agent.responsibilities));
        if let Some(resp) = state
            .responsibilities
            .iter_mut()
            .find(|row| row.agent_id == agent.id)
        {
            resp.graph_agent_id = Some(agent.id.clone());
            resp.role = Some(agent.role.clone());
            resp.model = Some(agent.model.clone());
            resp.responsibilities = Some(encoded);
            resp.updated_at = now;
        } else {
            state.responsibilities.push(Responsibility {
                agent_id: agent.id.clone(),
                graph_agent_id: Some(agent.id.clone()),
                role: Some(agent.role.clone()),
                model: Some(agent.model.clone()),
                responsibilities: Some(encoded),
                current_task: None,
                scope: None,
                updated_at: now,
            });
        }
    }
}

pub fn validate_graph(data: &Value) -> Value {
    if !matches!(data, Value::Object(_)) {
        return json::obj(vec![
            ("valid", json::b(false)),
            ("errors", json::arr(vec![json::s("root must be an object")])),
        ]);
    }
    let mut errors = Vec::new();
    let agents: Vec<Value> = match data.get("agents") {
        None => {
            errors.push("agents: required field is missing".to_string());
            Vec::new()
        }
        Some(Value::Array(items)) => items.clone(),
        Some(_) => {
            errors.push("agents must be a list".to_string());
            Vec::new()
        }
    };
    let required = ["id", "responsibilities", "role"];
    let allowed = ["id", "model", "responsibilities", "role"];
    let mut seen = Vec::new();
    let mut ids = Vec::new();
    for (index, agent) in agents.iter().enumerate() {
        let Value::Object(pairs) = agent else {
            errors.push(format!("agents[{index}]: must be an object"));
            continue;
        };
        let keys: Vec<&str> = pairs.iter().map(|(k, _)| k.as_str()).collect();
        let mut missing: Vec<&str> = required
            .iter()
            .copied()
            .filter(|key| !keys.contains(key))
            .collect();
        missing.sort();
        if !missing.is_empty() {
            errors.push(format!(
                "agents[{index}]: missing required fields: {}",
                json::py_repr(&json::string_list(
                    &missing.iter().map(|s| (*s).to_string()).collect::<Vec<_>>()
                ))
            ));
        }
        let mut unknown: Vec<String> = keys
            .iter()
            .filter(|key| !allowed.contains(*key))
            .map(|s| (*s).to_string())
            .collect();
        unknown.sort();
        if !unknown.is_empty() {
            errors.push(format!(
                "agents[{index}]: unknown fields: {}",
                json::py_repr(&json::string_list(&unknown))
            ));
        }
        if let Some(id) = agent.get("id").and_then(Value::as_str) {
            if seen.iter().any(|item: &String| item == id) {
                errors.push(format!(
                    "agents[{index}]: duplicate agent id {}",
                    json::py_repr(&json::s(id))
                ));
            }
            seen.push(id.to_string());
            ids.push(id.to_string());
        }
        if let Some(resp) = agent.get("responsibilities") {
            if !matches!(resp, Value::Array(_)) {
                errors.push(format!("agents[{index}].responsibilities must be a list"));
            }
        }
    }
    match data.get("handoffs") {
        None => {}
        Some(Value::Array(items)) => {
            let ids_set: Vec<String> = ids.clone();
            for (index, handoff) in items.iter().enumerate() {
                let Value::Object(pairs) = handoff else {
                    errors.push(format!("handoffs[{index}]: must be an object"));
                    continue;
                };
                let keys: Vec<&str> = pairs.iter().map(|(k, _)| k.as_str()).collect();
                let need = ["from", "to", "condition"];
                let mut missing: Vec<&str> = need.iter().copied().filter(|k| !keys.contains(k)).collect();
                missing.sort();
                if !missing.is_empty() {
                    errors.push(format!(
                        "handoffs[{index}]: missing required fields: {}",
                        json::py_repr(&json::string_list(&missing.iter().map(|s| (*s).to_string()).collect::<Vec<_>>()))
                    ));
                }
                let mut unknown: Vec<String> = keys.iter().filter(|k| !need.contains(*k)).map(|s| (*s).to_string()).collect();
                unknown.sort();
                if !unknown.is_empty() {
                    errors.push(format!(
                        "handoffs[{index}]: unknown fields: {}",
                        json::py_repr(&json::string_list(&unknown))
                    ));
                }
                if !ids_set.is_empty() {
                    for field in ["from", "to"] {
                        if let Some(value) = handoff.get(field).and_then(Value::as_str) {
                            if !ids_set.iter().any(|id| id == value) {
                                errors.push(format!(
                                    "handoffs[{index}].{field}: {} is not a defined agent id",
                                    json::py_repr(&json::s(value))
                                ));
                            }
                        }
                    }
                }
            }
        }
        Some(_) => errors.push("handoffs must be a list".into()),
    }
    if let Some(escalation) = data.get("escalation") {
        if !matches!(escalation, Value::Object(_)) {
            errors.push("escalation must be an object".into());
        }
    }
    if let Some(assessment) = data.get("assessment") {
        if !matches!(assessment, Value::Object(_)) {
            errors.push("assessment must be an object".into());
        }
    }
    (
        json::obj(vec![
            ("valid", json::b(errors.is_empty())),
            ("errors", json::string_list(&errors)),
        ]),
        ids,
    )
        .0
}

pub fn scan(
    state: &mut State,
    project_root: Option<&Path>,
    worktree: Option<&str>,
    extensions: Option<&[String]>,
) -> Value {
    let root = match worktree {
        None => project_root.map(|p| p.to_path_buf()),
        Some(path) => {
            let candidate = PathBuf::from(path);
            if !candidate.exists() {
                return json::obj(vec![
                    ("scanned", json::i(0)),
                    ("owned", json::i(0)),
                    (
                        "error",
                        json::s(format!(
                            "worktree_root does not exist: {}",
                            json::py_repr(&json::s(path))
                        )),
                    ),
                ]);
            }
            if candidate
                .symlink_metadata()
                .map(|m| m.file_type().is_symlink())
                .unwrap_or(false)
            {
                return json::obj(vec![
                    ("scanned", json::i(0)),
                    ("owned", json::i(0)),
                    ("error", json::s("worktree_root may not be a symlink")),
                ]);
            }
            if !candidate.is_dir() {
                return json::obj(vec![
                    ("scanned", json::i(0)),
                    ("owned", json::i(0)),
                    (
                        "error",
                        json::s(format!(
                            "worktree_root must be a directory: {}",
                            json::py_repr(&json::s(path))
                        )),
                    ),
                ]);
            }
            let resolved = util::soft_resolve(&candidate);
            if let Some(project) = project_root {
                let project = util::soft_resolve(project);
                if !resolved.starts_with(&project) {
                    return json::obj(vec![
                        ("scanned", json::i(0)),
                        ("owned", json::i(0)),
                        (
                            "error",
                            json::s("worktree_root must be inside project_root"),
                        ),
                    ]);
                }
            }
            Some(resolved)
        }
    };
    let Some(root) = root else {
        return json::obj(vec![
            ("scanned", json::i(0)),
            ("owned", json::i(0)),
            ("error", json::s("No project root")),
        ]);
    };
    let default_exts = [".py", ".md", ".json", ".yaml", ".yml", ".txt", ".toml"];
    let exts: Vec<String> = extensions
        .map(|items| items.to_vec())
        .unwrap_or_else(|| default_exts.iter().map(|s| (*s).to_string()).collect());
    let skip = [
        "__pycache__",
        ".pytest_cache",
        "node_modules",
        ".coordinationhub",
        ".git",
        ".venv",
        "venv",
        ".env",
        ".eggs",
        ".mypy_cache",
        ".tox",
        ".ruff_cache",
    ];
    let fallback = state
        .agents
        .iter()
        .filter(|agent| agent.status == "active")
        .min_by(|a, b| {
            a.started_at
                .partial_cmp(&b.started_at)
                .unwrap_or(std::cmp::Ordering::Equal)
        })
        .map(|agent| agent.agent_id.clone())
        .unwrap_or_else(|| "unassigned".into());
    let mut scanned = 0_i64;
    let mut owned = 0_i64;
    let mut files = Vec::new();
    let _ = walk_files(&root, &skip, &exts, 0, &mut files);
    let now = util::now();
    for file in files {
        scanned += 1;
        let rel = file
            .strip_prefix(&root)
            .unwrap_or(&file)
            .to_string_lossy()
            .replace('\\', "/");
        let assigned = state
            .ownership
            .iter()
            .find(|row| row.document_path == rel)
            .map(|row| row.assigned_agent_id.clone())
            .unwrap_or_else(|| fallback.clone());
        if let Some(row) = state
            .ownership
            .iter_mut()
            .find(|row| row.document_path == rel)
        {
            row.assigned_agent_id = assigned.clone();
            row.assigned_at = now;
        } else {
            state.ownership.push(Ownership {
                document_path: rel,
                assigned_agent_id: assigned.clone(),
                assigned_at: now,
                last_claimed_by: None,
                task_description: None,
            });
        }
        if assigned != "unassigned" {
            owned += 1;
        }
    }
    state.record_event(
        "scan.completed",
        json::obj(vec![
            ("scanned", json::i(scanned)),
            ("owned", json::i(owned)),
            (
                "worktree_root",
                json::s(
                    worktree
                        .map(|path| path.to_string())
                        .unwrap_or_else(|| root.to_string_lossy().replace('\\', "/")),
                ),
            ),
        ]),
    );
    json::obj(vec![
        ("scanned", json::i(scanned)),
        ("owned", json::i(owned)),
    ])
}

fn walk_files(dir: &Path, skip: &[&str], exts: &[String], depth: i64, out: &mut Vec<PathBuf>) {
    if depth > 20 || out.len() >= 50_000 {
        return;
    }
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    let mut entries: Vec<_> = entries.filter_map(|e| e.ok()).collect();
    entries.sort_by_key(|e| e.file_name());
    for entry in entries {
        let name = entry.file_name().to_string_lossy().to_string();
        if name.starts_with('.')
            || skip.iter().any(|part| *part == name)
            || name.ends_with(".egg-info")
        {
            continue;
        }
        let path = entry.path();
        if path.is_dir() {
            walk_files(&path, skip, exts, depth + 1, out);
        } else if exts.iter().any(|ext| name.ends_with(ext))
            && !path
                .symlink_metadata()
                .map(|m| m.file_type().is_symlink())
                .unwrap_or(false)
        {
            out.push(path);
        }
    }
}

pub fn agent_status(state: &State, agent_id: &str) -> Value {
    let Some(agent) = state.agent(agent_id) else {
        return json::obj(vec![(
            "error",
            json::s(format!("Agent not found: {agent_id}")),
        )]);
    };
    let resp = state
        .responsibilities
        .iter()
        .find(|row| row.agent_id == agent_id);
    let responsibilities = resp
        .and_then(|row| row.responsibilities.as_deref())
        .and_then(|raw| json::parse(raw).ok())
        .unwrap_or_else(|| json::arr(vec![]));
    let owned: Vec<&Ownership> = state
        .ownership
        .iter()
        .filter(|row| row.assigned_agent_id == agent_id)
        .collect();
    let locks: Vec<Value> = state
        .locks
        .iter()
        .filter(|lock| lock.locked_by == agent_id)
        .map(|lock| json::s(&lock.document_path))
        .collect();
    let with_tasks: Vec<Value> = owned
        .iter()
        .map(|row| {
            json::obj(vec![
                ("file", json::s(&row.document_path)),
                (
                    "task",
                    json::s(row.task_description.clone().unwrap_or_default()),
                ),
            ])
        })
        .collect();
    let files: Vec<Value> = owned
        .iter()
        .map(|row| json::s(&row.document_path))
        .collect();
    json::obj(vec![
        ("agent_id", json::s(agent_id)),
        ("status", json::s(&agent.status)),
        ("parent_id", json::opt_string(&agent.parent_id)),
        (
            "graph_agent_id",
            json::opt_string(&resp.and_then(|row| row.graph_agent_id.clone())),
        ),
        (
            "role",
            json::opt_string(&resp.and_then(|row| row.role.clone())),
        ),
        (
            "model",
            json::opt_string(&resp.and_then(|row| row.model.clone())),
        ),
        ("responsibilities", responsibilities),
        (
            "current_task",
            json::opt_string(&resp.and_then(|row| row.current_task.clone())),
        ),
        ("owned_files", json::arr(files)),
        ("owned_files_with_tasks", json::arr(with_tasks)),
        ("active_locks", json::arr(locks)),
        ("lineage", crate::act_identity::lineage(state, agent_id)),
    ])
}

pub fn agent_tree(state: &State, agent_id: Option<&str>) -> Value {
    let root_id = if let Some(agent_id) = agent_id {
        if state.agent(agent_id).is_none() {
            return json::obj(vec![(
                "error",
                json::s(format!("Agent not found: {agent_id}")),
            )]);
        }
        agent_id.to_string()
    } else {
        match state
            .agents
            .iter()
            .filter(|agent| agent.parent_id.is_none() && agent.status == "active")
            .min_by(|a, b| {
                a.last_heartbeat
                    .partial_cmp(&b.last_heartbeat)
                    .unwrap_or(std::cmp::Ordering::Equal)
            }) {
            Some(agent) => agent.agent_id.clone(),
            None => return json::obj(vec![("error", json::s("No active root agent found"))]),
        }
    };
    let now = util::now();
    let Some(root) = build_node(state, &root_id, now, &mut Vec::new(), 0) else {
        return json::obj(vec![(
            "error",
            json::s(format!("Agent not found: {root_id}")),
        )]);
    };
    let mut ancestors = Vec::new();
    let mut current = root_id.clone();
    loop {
        let Some(parent) = state
            .agent(&current)
            .and_then(|agent| agent.parent_id.clone())
        else {
            break;
        };
        ancestors.push(json::obj(vec![("agent_id", json::s(&parent))]));
        current = parent;
    }
    let text = render_tree(&root);
    json::obj(vec![
        ("root", root),
        ("ancestors", json::arr(ancestors)),
        ("text_tree", json::s(text)),
    ])
}

fn build_node(
    state: &State,
    agent_id: &str,
    now: f64,
    seen: &mut Vec<String>,
    depth: i64,
) -> Option<Value> {
    if depth >= 100 || seen.iter().any(|id| id == agent_id) {
        return None;
    }
    let agent = state.agent(agent_id)?;
    seen.push(agent_id.to_string());
    let resp = state
        .responsibilities
        .iter()
        .find(|row| row.agent_id == agent_id);
    let responsibilities = resp
        .and_then(|row| row.responsibilities.as_deref())
        .and_then(|raw| json::parse(raw).ok())
        .unwrap_or_else(|| json::arr(vec![]));
    let mut locks = Vec::new();
    for lock in state
        .locks
        .iter()
        .filter(|lock| lock.locked_by == agent_id && lock.locked_at + lock.lock_ttl > now)
    {
        let mut entry = vec![
            ("path".to_string(), json::s(&lock.document_path)),
            ("type".to_string(), json::s(&lock.lock_type)),
        ];
        if let Some(start) = lock.region_start {
            entry.push((
                "region".into(),
                json::s(format!("L{start}-{}", lock.region_end.unwrap_or(start))),
            ));
        }
        if let Some(owner) = state
            .ownership
            .iter()
            .find(|row| row.document_path == lock.document_path)
            .map(|row| row.assigned_agent_id.clone())
        {
            if owner != agent_id {
                entry.push(("boundary_warning".into(), json::s(owner)));
            }
        }
        locks.push(Value::Object(entry));
    }
    let children_ids: Vec<String> = state
        .agents
        .iter()
        .filter(|row| row.parent_id.as_deref() == Some(agent_id) && row.status == "active")
        .map(|row| row.agent_id.clone())
        .collect();
    let mut children = Vec::new();
    for child in children_ids {
        if let Some(node) = build_node(state, &child, now, seen, depth + 1) {
            children.push(node);
        }
    }
    Some(json::obj(vec![
        ("agent_id", json::s(agent_id)),
        ("status", json::s(&agent.status)),
        (
            "graph_agent_id",
            json::opt_string(&resp.and_then(|row| row.graph_agent_id.clone())),
        ),
        (
            "role",
            json::opt_string(&resp.and_then(|row| row.role.clone())),
        ),
        (
            "current_task",
            json::opt_string(&resp.and_then(|row| row.current_task.clone())),
        ),
        ("responsibilities", responsibilities),
        ("locks", json::arr(locks)),
        ("children", json::arr(children)),
    ]))
}

fn render_tree(root: &Value) -> String {
    let mut lines = Vec::new();
    render_node(root, &mut lines, "", true, true);
    lines.join("\n")
}

fn render_node(node: &Value, lines: &mut Vec<String>, prefix: &str, is_root: bool, is_last: bool) {
    let aid = node.get("agent_id").and_then(Value::as_str).unwrap_or("");
    let tag = node
        .get("graph_agent_id")
        .and_then(Value::as_str)
        .unwrap_or_else(|| node.get("status").and_then(Value::as_str).unwrap_or(""));
    let connector = if is_root {
        ""
    } else if is_last {
        "└── "
    } else {
        "├── "
    };
    let mut header = format!("{prefix}{connector}{aid} [{tag}]");
    if let Some(task) = node.get("current_task").and_then(Value::as_str) {
        header.push_str(&format!(" — \"{task}\""));
    } else if let Some(role) = node.get("role").and_then(Value::as_str) {
        header.push_str(&format!(" — {role}"));
    }
    lines.push(header);
    let owned_prefix;
    let child_prefix: &str = if is_root {
        ""
    } else if is_last {
        owned_prefix = format!("{prefix}    ");
        &owned_prefix
    } else {
        owned_prefix = format!("{prefix}│   ");
        &owned_prefix
    };
    let locks = node.get("locks").and_then(Value::as_array).unwrap_or(&[]);
    let children = node
        .get("children")
        .and_then(Value::as_array)
        .unwrap_or(&[]);
    for (index, lock) in locks.iter().enumerate() {
        let last_item = index + 1 == locks.len() && children.is_empty();
        let mark = if last_item { "└─ " } else { "├─ " };
        let path = lock.get("path").and_then(Value::as_str).unwrap_or("");
        let mut info = lock
            .get("type")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        if let Some(region) = lock.get("region").and_then(Value::as_str) {
            info.push(' ');
            info.push_str(region);
        }
        let mut line = format!("{child_prefix}{mark}◆ {path} [{info}]");
        if let Some(owner) = lock.get("boundary_warning").and_then(Value::as_str) {
            line.push_str(&format!(" ⚠ owned by {owner}"));
        }
        lines.push(line);
    }
    for (index, child) in children.iter().enumerate() {
        render_node(
            child,
            lines,
            &child_prefix,
            false,
            index + 1 == children.len(),
        );
    }
}

pub fn file_map(state: &State, agent_id: Option<&str>) -> Value {
    let mut rows: Vec<&Ownership> = state
        .ownership
        .iter()
        .filter(|row| {
            agent_id
                .map(|id| row.assigned_agent_id == id)
                .unwrap_or(true)
        })
        .collect();
    rows.sort_by(|a, b| {
        if agent_id.is_some() {
            a.document_path.cmp(&b.document_path)
        } else {
            a.assigned_agent_id
                .cmp(&b.assigned_agent_id)
                .then(a.document_path.cmp(&b.document_path))
        }
    });
    let entries: Vec<Value> = rows
        .into_iter()
        .map(|row| {
            let resp = state
                .responsibilities
                .iter()
                .find(|resp| resp.agent_id == row.assigned_agent_id);
            let responsibilities = resp
                .and_then(|item| item.responsibilities.as_deref())
                .and_then(|raw| json::parse(raw).ok())
                .unwrap_or_else(|| json::arr(vec![]));
            json::obj(vec![
                ("document_path", json::s(&row.document_path)),
                ("assigned_agent_id", json::s(&row.assigned_agent_id)),
                (
                    "graph_agent_id",
                    json::s(
                        resp.and_then(|item| item.graph_agent_id.clone())
                            .unwrap_or_else(|| "unknown".into()),
                    ),
                ),
                (
                    "role",
                    json::s(
                        resp.and_then(|item| item.role.clone())
                            .unwrap_or_else(|| "unknown".into()),
                    ),
                ),
                ("responsibilities", responsibilities),
                ("task_description", json::opt_string(&row.task_description)),
            ])
        })
        .collect();
    let total = entries.len() as i64;
    json::obj(vec![
        ("files", json::arr(entries)),
        ("total", json::i(total)),
    ])
}

pub fn implicit_graph(state: &State) -> Graph {
    let mut agents = vec![GraphAgent {
        id: "orchestrator".into(),
        role: "project manager".into(),
        model: String::new(),
        responsibilities: vec![
            "coordinate".into(),
            "orchestrate".into(),
            "monitor".into(),
        ],
    }];
    let mut handoffs = Vec::new();
    let active: Vec<&crate::db::Agent> = state
        .agents
        .iter()
        .filter(|agent| agent.status == "active")
        .collect();
    let ids: Vec<&str> = active.iter().map(|agent| agent.agent_id.as_str()).collect();
    for agent in &active {
        let Some(parent) = agent.parent_id.as_deref() else {
            continue;
        };
        let role = match agent.agent_id.rsplit_once('.') {
            Some((_, suffix)) if suffix.chars().all(|ch| ch.is_ascii_digit()) => "worker",
            Some((_, suffix)) => suffix,
            None => "worker",
        };
        if !agents.iter().any(|row| row.id == agent.agent_id) {
            agents.push(GraphAgent {
                id: agent.agent_id.clone(),
                role: role.into(),
                model: String::new(),
                responsibilities: vec!["implement".into(), "write".into(), "code".into()],
            });
        }
        handoffs.push(("orchestrator".into(), agent.agent_id.clone()));
        if ids.iter().any(|id| *id == parent) {
            handoffs.push((parent.to_string(), agent.agent_id.clone()));
        }
    }
    let raw_agents = agents.iter().map(|agent| agent.id.clone()).collect();
    Graph {
        path: String::new(),
        agents,
        handoffs,
        raw_agents,
    }
}

pub fn validate_loaded(graph: Option<&Graph>) -> Value {
    let Some(graph) = graph else {
        return json::obj(vec![
            ("valid", json::b(false)),
            (
                "errors",
                json::arr(vec![json::s("No coordination graph is currently loaded")]),
            ),
        ]);
    };
    let _ = graph;
    json::obj(vec![
        ("valid", json::b(true)),
        ("errors", json::arr(vec![])),
    ])
}

pub fn assess(
    state: &mut State,
    suite_path: Option<&str>,
    format: &str,
    graph_agent_id: Option<&str>,
    graph: Option<&Graph>,
) -> Value {
    let graph_loaded = graph.is_some();
    let suite = if let Some(path) = suite_path {
        if !Path::new(path).is_file() {
            return json::obj(vec![(
                "error",
                json::s(format!("Suite file not found: {path}")),
            )]);
        }
        match std::fs::read_to_string(path)
            .ok()
            .and_then(|text| json::parse(&text).ok())
        {
            Some(value) => value,
            None => {
                return json::obj(vec![(
                    "error",
                    json::s(format!("Failed to load suite: {path}")),
                )])
            }
        }
    } else {
        let trace = live_trace(state);
        json::obj(vec![
            ("name", json::s("live_session")),
            ("traces", json::arr(vec![trace])),
        ])
    };
    let name = suite
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or("unnamed")
        .to_string();
    let metrics = [
        "role_stability",
        "handoff_latency",
        "outcome_verifiability",
        "protocol_adherence",
        "spawn_propagation",
        "leader_stability",
    ];
    let mut traces = suite
        .get("traces")
        .and_then(Value::as_array)
        .map(|items| items.to_vec())
        .unwrap_or_default();
    if let Some(filter) = graph_agent_id {
        traces.retain(|trace| {
            trace
                .get("events")
                .and_then(Value::as_array)
                .map(|events| {
                    events.iter().any(|event| {
                        event.get("type").and_then(Value::as_str) == Some("register")
                            && event.get("graph_id").and_then(Value::as_str) == Some(filter)
                    })
                })
                .unwrap_or(false)
        });
    }
    let mut trace_scores = Vec::new();
    let mut totals = [0.0_f64; 6];
    for trace in &traces {
        let id = trace
            .get("trace_id")
            .and_then(Value::as_str)
            .unwrap_or("unknown")
            .to_string();
        let scores = score_trace(trace, graph);
        for (index, metric) in metrics.iter().enumerate() {
            totals[index] += scores
                .get(*metric)
                .and_then(Value::as_f64)
                .unwrap_or(0.0);
        }
        trace_scores.push((id, scores));
    }
    let averages = if traces.is_empty() {
        json::obj(metrics.iter().map(|metric| (*metric, json::f(0.0))).collect())
    } else {
        let divisor = traces.len() as f64;
        json::obj(
            metrics
                .iter()
                .enumerate()
                .map(|(index, metric)| (*metric, json::f(totals[index] / divisor)))
                .collect(),
        )
    };
    let overall = if traces.is_empty() {
        0.0
    } else {
        totals.iter().sum::<f64>() / (traces.len() as f64 * metrics.len() as f64)
    };
    let result = json::obj(vec![
        ("suite_name", json::s(name)),
        ("run_at", json::f(util::now())),
        ("metrics", averages),
        ("overall_score", json::f(overall)),
        ("traces", Value::Object(trace_scores)),
        ("graph_loaded", json::b(graph_loaded)),
        ("graph_agent_id_filter", json::opt_str(graph_agent_id)),
        ("suggested_refinements", json::arr(vec![])),
        (
            "full_trace_json",
            json::s(json::dumps(&Value::Array(traces))),
        ),
    ]);
    let _ = state;
    if format == "json" {
        result
    } else {
        let report = format_report(&result);
        json::obj(vec![("report", json::s(report)), ("scores", result)])
    }
}

fn live_trace(state: &State) -> Value {
    let mut stamped: Vec<(f64, Value)> = Vec::new();
    for agent in &state.agents {
        let mut pairs = vec![
            ("type".to_string(), json::s("register")),
            ("agent_id".to_string(), json::s(&agent.agent_id)),
        ];
        if let Some(row) = state
            .responsibilities
            .iter()
            .find(|row| row.agent_id == agent.agent_id)
        {
            if let Some(graph_id) = &row.graph_agent_id {
                pairs.push(("graph_id".into(), json::s(graph_id.clone())));
            }
        }
        if let Some(parent) = &agent.parent_id {
            pairs.push(("parent_id".into(), json::s(parent.clone())));
        }
        stamped.push((agent.started_at, Value::Object(pairs)));
    }
    for note in state
        .notifications
        .iter()
        .filter(|row| row.change_type == "modified")
    {
        let path = json::s(&note.document_path);
        let agent = json::s(&note.agent_id);
        stamped.push((
            note.created_at - 1e-6,
            json::obj(vec![
                ("type", json::s("lock")),
                ("path", path.clone()),
                ("agent_id", agent.clone()),
            ]),
        ));
        stamped.push((
            note.created_at,
            json::obj(vec![
                ("type", json::s("modified")),
                ("path", path.clone()),
                ("agent_id", agent.clone()),
            ]),
        ));
        stamped.push((
            note.created_at + 1e-6,
            json::obj(vec![
                ("type", json::s("unlock")),
                ("path", path),
                ("agent_id", agent),
            ]),
        ));
    }
    stamped.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal));
    json::obj(vec![
        ("trace_id", json::s("live_session")),
        (
            "events",
            json::arr(stamped.into_iter().map(|(_, event)| event).collect()),
        ),
    ])
}

fn event_matches(event_type: &str, responsibilities: &[String]) -> bool {
    const GROUPS: &[(&[&str], &[&str])] = &[
        (
            &["file_scan", "modified", "write"],
            &[
                "write", "edit", "modify", "implement", "develop", "build", "author", "produce",
                "create", "code", "fix",
            ],
        ),
        (
            &["acquire_lock", "release_lock", "refresh_lock"],
            &["coordinate", "lock", "manage", "orchestrate", "synchronize"],
        ),
        (
            &["get_notifications", "prune_notifications"],
            &["notify", "coordinate", "monitor", "track", "observe"],
        ),
        (
            &["read", "search", "explore"],
            &[
                "read", "review", "explore", "research", "investigate", "analyze", "examine",
                "inspect", "quality",
            ],
        ),
        (
            &["test", "run_tests"],
            &["test", "verify", "validate", "quality", "qa", "check", "assert", "ensure"],
        ),
        (
            &["plan", "decompose"],
            &[
                "plan", "decompose", "design", "architect", "organize", "prioritize", "schedule",
                "scope",
            ],
        ),
        (
            &["deploy", "release", "publish"],
            &["deploy", "release", "publish", "ship", "deliver", "pipeline"],
        ),
    ];
    for (types, keywords) in GROUPS {
        if types.contains(&event_type) {
            for resp in responsibilities {
                let lower = resp.to_ascii_lowercase();
                if keywords.iter().any(|word| lower.contains(word)) {
                    return true;
                }
            }
            return false;
        }
    }
    let tokens: Vec<String> = event_type
        .to_ascii_lowercase()
        .replace('_', " ")
        .split_whitespace()
        .map(|s| s.to_string())
        .collect();
    responsibilities.iter().any(|resp| {
        resp.to_ascii_lowercase()
            .split_whitespace()
            .any(|word| tokens.iter().any(|token| token == word))
    })
}

fn score_trace(trace: &Value, graph: Option<&Graph>) -> Value {
    let events = trace.get("events").and_then(Value::as_array);
    let empty = Vec::new();
    let events = events.unwrap_or(&empty);
    let mut agent_graph: Vec<(String, String)> = Vec::new();
    let mut agent_parent: Vec<(String, String)> = Vec::new();
    for event in events {
        if event.get("type").and_then(Value::as_str) == Some("register") {
            if let Some(id) = event.get("agent_id").and_then(Value::as_str) {
                agent_graph.push((
                    id.into(),
                    event
                        .get("graph_id")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .into(),
                ));
                agent_parent.push((
                    id.into(),
                    event
                        .get("parent_id")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .into(),
                ));
            }
        }
    }
    let lookup = |table: &[(String, String)], key: &str| -> String {
        table
            .iter()
            .find(|(id, _)| id == key)
            .map(|(_, value)| value.clone())
            .unwrap_or_default()
    };
    let resp_of = |gid: &str| -> Vec<String> {
        graph
            .and_then(|graph| graph.agents.iter().find(|agent| agent.id == gid))
            .map(|agent| agent.responsibilities.clone())
            .unwrap_or_default()
    };
    const PRIMITIVES: &[&str] = &[
        "lock",
        "unlock",
        "notify_change",
        "register",
        "handoff",
        "heartbeat",
        "acquire_lock",
        "release_lock",
        "refresh_lock",
        "get_notifications",
        "prune_notifications",
    ];
    let role = if events.is_empty() {
        1.0
    } else {
        let mut violations = 0.0;
        let mut scored = 0.0;
        for event in events {
            let kind = event.get("type").and_then(Value::as_str).unwrap_or("");
            if PRIMITIVES.contains(&kind) {
                continue;
            }
            let aid = event.get("agent_id").and_then(Value::as_str).unwrap_or("");
            let responsibilities = resp_of(&lookup(&agent_graph, aid));
            if responsibilities.is_empty() {
                continue;
            }
            scored += 1.0;
            if !event_matches(kind, &responsibilities) {
                violations += 1.0;
            }
        }
        if scored == 0.0 {
            1.0
        } else {
            (1.0_f64 - violations / scored).max(0.0)
        }
    };
    let handoff = if graph.is_none() {
        0.0
    } else if events.is_empty() {
        1.0
    } else {
        let hands: Vec<&Value> = events
            .iter()
            .filter(|event| event.get("type").and_then(Value::as_str) == Some("handoff"))
            .collect();
        if hands.is_empty() {
            1.0
        } else {
            let mut score = 0.0;
            for event in hands.iter() {
                let from = event.get("from").and_then(Value::as_str).unwrap_or("");
                let to = event.get("to").and_then(Value::as_str).unwrap_or("");
                if !graph
                    .unwrap()
                    .handoffs
                    .iter()
                    .any(|(left, right)| left == from && right == to)
                {
                    continue;
                }
                let condition = event.get("condition").and_then(Value::as_str).unwrap_or("");
                if !condition.is_empty() && condition != "always" && condition != "true" {
                    score += 1.0;
                } else if condition == "always" || condition == "true" {
                    score += 0.75;
                } else {
                    score += 1.0;
                }
            }
            (score / hands.len() as f64).min(1.0)
        }
    };
    let outcome = if events.is_empty() {
        1.0
    } else {
        let mut locked: Vec<(String, bool)> = Vec::new();
        let mut checks: Vec<bool> = Vec::new();
        for event in events {
            let kind = event.get("type").and_then(Value::as_str).unwrap_or("");
            let path = event
                .get("path")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            if kind == "lock" {
                locked.push((path, false));
            } else if kind == "write" || kind == "modified" {
                if let Some(row) = locked.iter_mut().rev().find(|(name, _)| name == &path) {
                    row.1 = true;
                } else if !path.is_empty() {
                    checks.push(false);
                }
            } else if kind == "unlock" {
                if let Some(index) = locked.iter().rposition(|(name, _)| name == &path) {
                    checks.push(locked[index].1);
                    locked.remove(index);
                } else if !path.is_empty() {
                    checks.push(false);
                }
            }
        }
        if checks.is_empty() {
            1.0
        } else {
            checks.iter().filter(|item| **item).count() as f64 / checks.len() as f64
        }
    };
    let protocol = if events.is_empty() {
        1.0
    } else {
        let mut violations = 0.0;
        let mut scored = 0.0;
        for event in events {
            let kind = event.get("type").and_then(Value::as_str).unwrap_or("");
            if PRIMITIVES.contains(&kind) {
                continue;
            }
            let aid = event.get("agent_id").and_then(Value::as_str).unwrap_or("");
            let responsibilities = resp_of(&lookup(&agent_graph, aid));
            scored += 1.0;
            if !responsibilities.is_empty() && !event_matches(kind, &responsibilities) {
                violations += 1.0;
            }
        }
        if scored == 0.0 {
            1.0
        } else {
            (1.0_f64 - violations / scored).max(0.0)
        }
    };
    let spawn = if events.is_empty() {
        1.0
    } else {
        let mut violations = 0.0;
        let mut scored = 0.0;
        for event in events {
            let kind = event.get("type").and_then(Value::as_str).unwrap_or("");
            if PRIMITIVES.contains(&kind) {
                continue;
            }
            let aid = event.get("agent_id").and_then(Value::as_str).unwrap_or("");
            let mut responsibilities = resp_of(&lookup(&agent_graph, aid));
            let parent = lookup(&agent_parent, aid);
            if !parent.is_empty() {
                responsibilities.extend(resp_of(&lookup(&agent_graph, &parent)));
            }
            if responsibilities.is_empty() {
                continue;
            }
            scored += 1.0;
            if !event_matches(kind, &responsibilities) {
                violations += 1.0;
            }
        }
        if scored == 0.0 {
            1.0
        } else {
            (1.0_f64 - violations / scored).max(0.0)
        }
    };
    let markers = ["transfer", "leaseExpired", "leadershipClaimed"];
    let leader = if events.is_empty() {
        1.0
    } else {
        let count = events
            .iter()
            .filter(|event| {
                event
                    .get("type")
                    .and_then(Value::as_str)
                    .map(|kind| markers.contains(&kind))
                    .unwrap_or(false)
            })
            .count();
        if count == 0 {
            1.0
        } else {
            (1.0 - count as f64 * 0.25).max(0.0)
        }
    };
    json::obj(vec![
        ("role_stability", json::f(role)),
        ("handoff_latency", json::f(handoff)),
        ("outcome_verifiability", json::f(outcome)),
        ("protocol_adherence", json::f(protocol)),
        ("spawn_propagation", json::f(spawn)),
        ("leader_stability", json::f(leader)),
    ])
}

fn format_report(result: &Value) -> String {
    let name = result
        .get("suite_name")
        .and_then(Value::as_str)
        .unwrap_or("");
    let overall = result
        .get("overall_score")
        .and_then(Value::as_f64)
        .unwrap_or(0.0);
    let loaded = if result
        .get("graph_loaded")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        "True"
    } else {
        "False"
    };
    let run_at = result.get("run_at").and_then(Value::as_f64).unwrap_or(0.0);
    let stamp = util::local_stamp(run_at);
    let mut lines = vec![
        format!("# Assessment Report: {name}"),
        String::new(),
        format!("**Overall Score:** {:.2}%", overall * 100.0),
        format!("**Graph Loaded:** {loaded}"),
        format!("**Run At:** {stamp}"),
    ];
    if let Some(filter) = result.get("graph_agent_id_filter").and_then(Value::as_str) {
        lines.push(format!("**Filtered by:** graph_agent_id = {filter}"));
    }
    lines.push(String::new());
    lines.push("## Metric Scores".into());
    lines.push(String::new());
    lines.push("| Metric | Score |".into());
    lines.push("|--------|-------|".into());
    if let Some(Value::Object(pairs)) = result.get("metrics") {
        for (metric, score) in pairs {
            let n = score.as_f64().unwrap_or(0.0);
            lines.push(format!("| {metric} | {:.2}% |", n * 100.0));
        }
    }
    lines.push(String::new());
    if let Some(Value::Object(traces)) = result.get("traces") {
        if !traces.is_empty() {
            lines.push("## Per-Trace Breakdown".into());
            lines.push(String::new());
            for (trace_id, scores) in traces {
                lines.push(format!("### {trace_id}"));
                if let Value::Object(pairs) = scores {
                    for (metric, score) in pairs {
                        let n = score.as_f64().unwrap_or(0.0);
                        lines.push(format!("- {metric}: {:.2}%", n * 100.0));
                    }
                }
                lines.push(String::new());
            }
        }
    }
    lines.join("\n")
}

pub fn who(
    state: &mut State,
    action: &str,
    agent_id: Option<&str>,
    area: Option<&str>,
    to_agent: Option<&str>,
    ttl: f64,
    note: Option<&str>,
) -> Value {
    let now = util::now();
    expire_claims(state, now);
    match action {
        "status" | "" => who_status(state, now),
        "claim" => {
            let (Some(agent_id), Some(area)) = (agent_id, area) else {
                return json::obj(vec![
                    ("claimed", json::b(false)),
                    ("error", json::s("agent_id and area are required")),
                ]);
            };
            if !agent_live(state, agent_id, now) {
                return json::obj(vec![
                    ("claimed", json::b(false)),
                    ("error", json::s("agent is not registered")),
                    ("agent_id", json::s(agent_id)),
                ]);
            }
            if let Some(existing) = state.claims.iter().find(|claim| {
                claim.agent_id != agent_id && claim.expires_at > now && areas_overlap(&claim.area, area)
            }) {
                return json::obj(vec![
                    ("claimed", json::b(false)),
                    ("area", json::s(area)),
                    ("holder", json::s(&existing.agent_id)),
                    ("expires_at", json::f(existing.expires_at)),
                    ("error", json::s("area is already claimed")),
                ]);
            }
            if let Some(claim) = state
                .claims
                .iter_mut()
                .find(|claim| claim.area == area && claim.agent_id == agent_id)
            {
                claim.expires_at = now + ttl;
                claim.ttl = ttl;
                claim.note = note.map(|s| s.to_string());
            } else {
                let id = state.next("claims");
                state.claims.push(Claim {
                    id,
                    agent_id: agent_id.into(),
                    area: area.into(),
                    claimed_at: now,
                    ttl,
                    expires_at: now + ttl,
                    note: note.map(|s| s.to_string()),
                });
            }
            push_event(state, area, None, Some(agent_id), "claim", now);
            json::obj(vec![
                ("claimed", json::b(true)),
                ("agent_id", json::s(agent_id)),
                ("area", json::s(area)),
                ("expires_at", json::f(now + ttl)),
            ])
        }
        "release" => {
            let (Some(agent_id), Some(area)) = (agent_id, area) else {
                return json::obj(vec![
                    ("released", json::b(false)),
                    ("error", json::s("agent_id and area are required")),
                ]);
            };
            let before = state.claims.len();
            state
                .claims
                .retain(|claim| !(claim.agent_id == agent_id && claim.area == area));
            let released = before != state.claims.len();
            if released {
                push_event(state, area, Some(agent_id), None, "release", now);
            }
            json::obj(vec![
                ("released", json::b(released)),
                ("agent_id", json::s(agent_id)),
                ("area", json::s(area)),
            ])
        }
        "handoff" => {
            let (Some(agent_id), Some(area), Some(to_agent)) = (agent_id, area, to_agent) else {
                return json::obj(vec![
                    ("handed_off", json::b(false)),
                    (
                        "error",
                        json::s("agent_id, area, and to_agent_id are required"),
                    ),
                ]);
            };
            if !agent_live(state, agent_id, now) || !agent_live(state, to_agent, now) {
                return json::obj(vec![
                    ("handed_off", json::b(false)),
                    ("error", json::s("agent is not registered")),
                ]);
            }
            let Some(pos) = state.claims.iter().position(|claim| {
                claim.area == area && claim.agent_id == agent_id && claim.expires_at > now
            }) else {
                return json::obj(vec![
                    ("handed_off", json::b(false)),
                    ("error", json::s("no live claim to hand off")),
                ]);
            };
            state.claims[pos].agent_id = to_agent.into();
            state.claims[pos].claimed_at = now;
            state.claims[pos].expires_at = now + state.claims[pos].ttl;
            push_event(state, area, Some(agent_id), Some(to_agent), "handoff", now);
            json::obj(vec![
                ("handed_off", json::b(true)),
                ("area", json::s(area)),
                ("from_agent_id", json::s(agent_id)),
                ("to_agent_id", json::s(to_agent)),
            ])
        }
        other => json::obj(vec![(
            "error",
            json::s(format!("unknown who action {other}")),
        )]),
    }
}

fn agent_live(state: &State, agent_id: &str, now: f64) -> bool {
    let stale = util::env_f64("COORDINATIONHUB_AGENTS_STALE_TIMEOUT_S", 600.0);
    match state.agent(agent_id) {
        Some(agent) if agent.status == "active" => now - agent.last_heartbeat <= stale,
        _ => false,
    }
}

fn areas_overlap(left: &str, right: &str) -> bool {
    if left == right {
        return true;
    }
    let pathish = |value: &str| value.contains('/') || value.contains('\\');
    if !pathish(left) || !pathish(right) {
        return false;
    }
    let left = left.trim_end_matches(['/', '\\']);
    let right = right.trim_end_matches(['/', '\\']);
    left == right
        || left.starts_with(&format!("{right}/"))
        || right.starts_with(&format!("{left}/"))
        || left.starts_with(&format!("{right}\\"))
        || right.starts_with(&format!("{left}\\"))
}

fn expire_claims(state: &mut State, now: f64) {
    let dead: Vec<Claim> = state
        .claims
        .iter()
        .filter(|claim| claim.expires_at <= now || !agent_live(state, &claim.agent_id, now))
        .cloned()
        .collect();
    for claim in dead {
        state.claims.retain(|row| row.id != claim.id);
        push_event(
            state,
            &claim.area,
            Some(&claim.agent_id),
            None,
            "expired",
            now,
        );
    }
}

fn push_event(
    state: &mut State,
    area: &str,
    from: Option<&str>,
    to: Option<&str>,
    action: &str,
    now: f64,
) {
    let id = state.next("claim_events");
    state.claim_events.push(ClaimEvent {
        id,
        area: area.into(),
        from_agent_id: from.map(|s| s.to_string()),
        to_agent_id: to.map(|s| s.to_string()),
        action: action.into(),
        at: now,
    });
}

fn who_status(state: &State, now: f64) -> Value {
    let mut agents: Vec<&crate::db::Agent> = state
        .agents
        .iter()
        .filter(|agent| agent.status == "active")
        .collect();
    agents.sort_by(|a, b| a.agent_id.cmp(&b.agent_id));
    let rows: Vec<Value> = agents
        .into_iter()
        .map(|agent| {
            let task = state
                .responsibilities
                .iter()
                .find(|row| row.agent_id == agent.agent_id)
                .and_then(|row| row.current_task.clone());
            let locks: Vec<Value> = state
                .locks
                .iter()
                .filter(|lock| {
                    lock.locked_by == agent.agent_id && lock.locked_at + lock.lock_ttl > now
                })
                .map(|lock| json::s(&lock.document_path))
                .collect();
            let claims: Vec<Value> = state
                .claims
                .iter()
                .filter(|claim| claim.agent_id == agent.agent_id)
                .map(|claim| json::s(&claim.area))
                .collect();
            json::obj(vec![
                ("agent_id", json::s(&agent.agent_id)),
                ("status", json::s(&agent.status)),
                ("current_task", json::opt_string(&task)),
                ("locks", json::arr(locks)),
                ("claims", json::arr(claims)),
                ("last_heartbeat", json::f(agent.last_heartbeat)),
            ])
        })
        .collect();
    let claims: Vec<Value> = state
        .claims
        .iter()
        .map(|claim| {
            json::obj(vec![
                ("area", json::s(&claim.area)),
                ("agent_id", json::s(&claim.agent_id)),
                ("expires_at", json::f(claim.expires_at)),
                ("note", json::opt_string(&claim.note)),
            ])
        })
        .collect();
    json::obj(vec![
        ("agents", json::arr(rows)),
        ("claims", json::arr(claims)),
        ("claim_count", json::i(state.claims.len() as i64)),
        (
            "agent_count",
            json::i(
                state
                    .agents
                    .iter()
                    .filter(|agent| agent.status == "active")
                    .count() as i64,
            ),
        ),
    ])
}

pub fn dashboard(state: &State) -> Value {
    let agents: Vec<Value> = state
        .agents
        .iter()
        .filter(|agent| agent.status != "stopped")
        .map(|agent| {
            let resp = state
                .responsibilities
                .iter()
                .find(|row| row.agent_id == agent.agent_id);
            json::obj(vec![
                ("agent_id", json::s(&agent.agent_id)),
                ("worktree_root", json::s(&agent.worktree_root)),
                ("started_at", json::f(agent.started_at)),
                ("status", json::s(&agent.status)),
                ("last_heartbeat", json::f(agent.last_heartbeat)),
                ("parent_id", json::opt_string(&agent.parent_id)),
                ("ide_vendor", json::opt_string(&agent.ide_vendor)),
                (
                    "current_task",
                    json::opt_string(&resp.and_then(|row| row.current_task.clone())),
                ),
                (
                    "role",
                    json::opt_string(&resp.and_then(|row| row.role.clone())),
                ),
                (
                    "graph_agent_id",
                    json::opt_string(&resp.and_then(|row| row.graph_agent_id.clone())),
                ),
            ])
        })
        .collect();
    json::obj(vec![
        ("agents", json::arr(agents)),
        (
            "tasks",
            json::arr(
                state
                    .tasks
                    .iter()
                    .map(|task| {
                        json::obj(vec![
                            ("id", json::s(&task.id)),
                            ("parent_agent_id", json::s(&task.parent_agent_id)),
                            ("parent_task_id", json::opt_string(&task.parent_task_id)),
                            ("assigned_agent_id", json::opt_string(&task.assigned_agent_id)),
                            ("description", json::s(&task.description)),
                            ("status", json::s(&task.status)),
                            (
                                "depends_on",
                                json::parse(&task.depends_on)
                                    .unwrap_or_else(|_| json::arr(vec![])),
                            ),
                            ("blocked_by", json::opt_string(&task.blocked_by)),
                            ("summary", json::opt_string(&task.summary)),
                            ("priority", json::i(task.priority)),
                            ("error", json::opt_string(&task.error)),
                            ("created_at", json::f(task.created_at)),
                            ("updated_at", json::f(task.updated_at)),
                        ])
                    })
                    .collect(),
            ),
        ),
        (
            "work_intents",
            json::arr(
                state
                    .intents
                    .iter()
                    .map(|row| {
                        json::obj(vec![
                            ("agent_id", json::s(&row.agent_id)),
                            ("document_path", json::s(&row.document_path)),
                            ("intent", json::s(&row.intent)),
                            ("declared_at", json::f(row.declared_at)),
                            ("ttl", json::f(row.ttl)),
                        ])
                    })
                    .collect(),
            ),
        ),
        (
            "locks",
            json::arr(
                state
                    .locks
                    .iter()
                    .map(|lock| {
                        let owner = state
                            .ownership
                            .iter()
                            .find(|row| row.document_path == lock.document_path)
                            .map(|row| row.assigned_agent_id.clone());
                        json::obj(vec![
                            ("id", json::i(lock.id)),
                            ("document_path", json::s(&lock.document_path)),
                            ("locked_by", json::s(&lock.locked_by)),
                            ("locked_at", json::f(lock.locked_at)),
                            ("lock_ttl", json::f(lock.lock_ttl)),
                            ("lock_type", json::s(&lock.lock_type)),
                            ("region_start", json::opt_i(lock.region_start)),
                            ("region_end", json::opt_i(lock.region_end)),
                            ("worktree_root", json::opt_string(&lock.worktree_root)),
                            ("owner_agent_id", json::opt_string(&owner)),
                        ])
                    })
                    .collect(),
            ),
        ),
        (
            "claims",
            json::arr(
                state
                    .claims
                    .iter()
                    .map(|claim| {
                        json::obj(vec![
                            ("area", json::s(&claim.area)),
                            ("agent_id", json::s(&claim.agent_id)),
                            ("expires_at", json::f(claim.expires_at)),
                        ])
                    })
                    .collect(),
            ),
        ),
        (
            "handoffs",
            json::arr(
                state
                    .handoffs
                    .iter()
                    .rev()
                    .take(100)
                    .map(|row| {
                        json::obj(vec![
                            ("id", json::i(row.id)),
                            ("from_agent_id", json::s(&row.from_agent_id)),
                            (
                                "to_agents",
                                json::parse(&row.to_agents).unwrap_or_else(|_| json::arr(vec![])),
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
                    .collect(),
            ),
        ),
        (
            "dependencies",
            json::arr(
                state
                    .dependencies
                    .iter()
                    .map(|row| {
                        json::obj(vec![
                            ("id", json::i(row.id)),
                            ("dependent_agent_id", json::s(&row.dependent_agent_id)),
                            ("depends_on_agent_id", json::s(&row.depends_on_agent_id)),
                            (
                                "depends_on_task_id",
                                json::opt_string(&row.depends_on_task_id),
                            ),
                            ("condition", json::s(&row.condition)),
                            ("satisfied", json::b(row.satisfied != 0)),
                            ("created_at", json::f(row.created_at)),
                        ])
                    })
                    .collect(),
            ),
        ),
    ])
}

pub fn claim_ownership(state: &mut State, path: &str, agent_id: &str, project_root: Option<&Path>) {
    let norm = normalize_path(path, project_root);
    if state
        .ownership
        .iter()
        .any(|row| row.document_path == norm)
    {
        return;
    }
    state.ownership.push(Ownership {
        document_path: norm,
        assigned_agent_id: agent_id.into(),
        assigned_at: util::now(),
        last_claimed_by: Some(agent_id.into()),
        task_description: None,
    });
}

pub fn norm_area(area: &str, project_root: Option<&Path>) -> String {
    let root = project_root
        .map(|path| path.to_path_buf())
        .or_else(|| {
            std::env::current_dir()
                .ok()
                .and_then(|cwd| util::detect_project_root(&cwd))
        })
        .unwrap_or_else(|| PathBuf::from("."));
    let raw = Path::new(area);
    let joined = if raw.is_absolute() {
        raw.to_path_buf()
    } else {
        root.join(area)
    };
    let mut out = PathBuf::new();
    for component in joined.components() {
        match component {
            std::path::Component::CurDir => {}
            std::path::Component::ParentDir => {
                out.pop();
            }
            other => out.push(other.as_os_str()),
        }
    }
    out.to_string_lossy().replace('\\', "/")
}
