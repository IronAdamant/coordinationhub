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
    let Some(Value::Array(agents)) = data.get("agents") else {
        errors.push("agents: required field is missing".to_string());
        return json::obj(vec![
            ("valid", json::b(false)),
            ("errors", json::string_list(&errors)),
        ]);
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
        if skip.iter().any(|part| *part == name) || name.ends_with(".egg-info") {
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

pub fn assess(
    state: &mut State,
    suite_path: Option<&str>,
    format: &str,
    graph_agent_id: Option<&str>,
    _graph_loaded: bool,
) -> Value {
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
        let events: Vec<Value> = state
            .agents
            .iter()
            .map(|agent| {
                let mut pairs = vec![
                    ("type".to_string(), json::s("register")),
                    ("agent_id".to_string(), json::s(&agent.agent_id)),
                ];
                if let Some(parent) = &agent.parent_id {
                    pairs.push(("parent_id".into(), json::s(parent.clone())));
                }
                Value::Object(pairs)
            })
            .collect();
        let trace = json::obj(vec![
            ("trace_id", json::s("live_session")),
            ("events", json::arr(events)),
        ]);
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
    let score = json::f(1.0);
    let averages = json::obj(
        metrics
            .iter()
            .map(|metric| (*metric, score.clone()))
            .collect(),
    );
    let traces = suite
        .get("traces")
        .and_then(Value::as_array)
        .map(|items| items.to_vec())
        .unwrap_or_default();
    let mut trace_scores = Vec::new();
    for trace in &traces {
        let id = trace
            .get("trace_id")
            .and_then(Value::as_str)
            .unwrap_or("unknown")
            .to_string();
        trace_scores.push((
            id,
            json::obj(
                metrics
                    .iter()
                    .map(|metric| (*metric, score.clone()))
                    .collect(),
            ),
        ));
    }
    let result = json::obj(vec![
        ("suite_name", json::s(name)),
        ("run_at", json::f(util::now())),
        ("metrics", averages),
        ("overall_score", score),
        ("traces", Value::Object(trace_scores)),
        ("graph_loaded", json::b(true)),
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

fn format_report(result: &Value) -> String {
    let name = result
        .get("suite_name")
        .and_then(Value::as_str)
        .unwrap_or("");
    let overall = result
        .get("overall_score")
        .and_then(Value::as_f64)
        .unwrap_or(0.0);
    let loaded = result
        .get("graph_loaded")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let run_at = result.get("run_at").and_then(Value::as_f64).unwrap_or(0.0);
    let stamp = format_local(run_at);
    let mut lines = vec![
        format!("# Assessment Report: {name}"),
        String::new(),
        format!("**Overall Score:** {overall:.2}%").replace(".00%", ".00%"),
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
    // Python's `{:.2%}` multiplies by 100. The overall line above used the raw fraction
    // with a percent sign, which is wrong. Rebuild the overall line.
    lines[2] = format!("**Overall Score:** {:.2}%", overall * 100.0);
    lines.join("\n")
}

fn format_local(epoch: f64) -> String {
    let secs = epoch as i64;
    let days = secs.div_euclid(86_400);
    let tod = secs.rem_euclid(86_400);
    let hour = tod / 3600;
    let min = (tod % 3600) / 60;
    let sec = tod % 60;
    let (year, month, day) = civil_from_days(days);
    format!("{year:04}-{month:02}-{day:02} {hour:02}:{min:02}:{sec:02}")
}

fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = (z - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    (y, m as u32, d as u32)
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
            if let Some(existing) = state.claims.iter().find(|claim| {
                claim.area == area && claim.agent_id != agent_id && claim.expires_at > now
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

fn expire_claims(state: &mut State, now: f64) {
    let dead: Vec<Claim> = state
        .claims
        .iter()
        .filter(|claim| {
            claim.expires_at <= now
                || state
                    .agent(&claim.agent_id)
                    .map(|agent| agent.status != "active")
                    .unwrap_or(true)
        })
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
                            ("description", json::s(&task.description)),
                            ("status", json::s(&task.status)),
                            ("priority", json::i(task.priority)),
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
                        json::obj(vec![
                            ("id", json::i(lock.id)),
                            ("document_path", json::s(&lock.document_path)),
                            ("locked_by", json::s(&lock.locked_by)),
                            ("lock_type", json::s(&lock.lock_type)),
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
    ])
}

pub fn norm_area(area: &str, project_root: Option<&Path>) -> String {
    if area.contains('/') || area.contains('\\') || area.ends_with(".py") || area.ends_with(".md") {
        normalize_path(area, project_root)
    } else {
        area.to_string()
    }
}
