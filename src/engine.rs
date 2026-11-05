//! Tool dispatch. One method per MCP tool, plus the additive `who` tool.

use crate::act_board;
use crate::act_identity;
use crate::act_lock;
use crate::act_view::{self, Graph};
use crate::db::{self, Store};
use crate::json::{self, Value};
use crate::util;
use crate::validate::{self, Schema};
use std::path::{Path, PathBuf};

pub struct Engine {
    pub store: Store,
    pub project_root: Option<PathBuf>,
    pub namespace: String,
    pub graph: Option<Graph>,
    pub schema: Schema,
}

impl Engine {
    pub fn open(
        storage_dir: Option<&Path>,
        project_root: Option<&Path>,
        namespace: &str,
    ) -> Result<Self, String> {
        let detected = project_root.map(|p| util::soft_resolve(p)).or_else(|| {
            util::detect_project_root(
                &std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")),
            )
        });
        let storage = db::resolve_storage(storage_dir, detected.as_deref());
        let store = Store::open(&storage)?;
        let schema = validate::load_schema();
        let mut engine = Self {
            store,
            project_root: detected,
            namespace: namespace.to_string(),
            graph: None,
            schema,
        };
        let (loaded, graph) = act_view::load_spec(
            &mut engine.store.state,
            engine.project_root.as_deref(),
            None,
        );
        let _ = loaded;
        engine.graph = graph;
        let _ = engine.store.commit();
        Ok(engine)
    }

    pub fn tool_count(&self) -> i64 {
        (self.schema.tools.len() + 1) as i64
    }

    pub fn call(&mut self, tool: &str, args: &Value) -> Result<Value, String> {
        self.invoke(tool, args, true)
    }

    pub fn call_cli(&mut self, tool: &str, args: &Value) -> Result<Value, String> {
        self.invoke(tool, args, false)
    }

    fn invoke(&mut self, tool: &str, args: &Value, check_schema: bool) -> Result<Value, String> {
        if !matches!(args, Value::Object(_)) && !matches!(args, Value::Null) {
            return Err(format!(
                "{tool}: value must be of type 'object' (got {})",
                args.py_type_name()
            ));
        }
        let args = if matches!(args, Value::Null) {
            Value::Object(vec![])
        } else {
            args.clone()
        };
        if tool == "who" {
            if let Err(err) = who_arguments(&args) {
                return Err(format!("Input validation error: who: {err}"));
            }
        } else if !self.schema.tools.iter().any(|(name, _)| name == tool) {
            return Err(validate::unknown_tool(&self.schema, tool));
        } else if check_schema {
            validate::validate_tool(&self.schema, tool, &args)
                .map_err(|err| format!("Input validation error: {err}"))?;
        }
        if tool == "who" {
            self.store.begin()?;
            let value = self.who(&args);
            self.store.commit()?;
            self.store.end();
            return Ok(value);
        }
        if blocks(tool, &args) {
            return self.blocking(tool, &args);
        }
        self.store.begin()?;
        let result = self.dispatch(tool, &args);
        match result {
            Ok(value) => {
                if !read_only(tool, &args) {
                    self.store.commit()?;
                }
                self.store.end();
                Ok(value)
            }
            Err(err) => {
                self.store.end();
                Err(err)
            }
        }
    }

    fn url(&self) -> String {
        std::env::var("COORDINATIONHUB_COORDINATION_URL")
            .unwrap_or_else(|_| "http://localhost:9877".into())
    }

    fn dispatch(&mut self, tool: &str, args: &Value) -> Result<Value, String> {
        let root = self.project_root.clone();
        let graph_loaded = self.graph.is_some();
        let graph = self.graph.clone();
        let tools = (self.schema.tools.len() + 1) as i64;
        let url = self.url();
        let state = &mut self.store.state;
        let root_ref = root.as_deref();
        Ok(match tool {
            "register_agent" => {
                let id = need_str(args, "agent_id")?;
                let parent = opt_str(args, "parent_id");
                let worktree = act_identity::worktree_of(root_ref, opt_str(args, "worktree_root"));
                let mut value = act_identity::register(
                    state,
                    id,
                    parent,
                    &worktree,
                    None,
                    None,
                    graph_loaded,
                    &url,
                );
                if let Some(graph_id) = opt_str(args, "graph_agent_id") {
                    if let Some(graph) = &self.graph {
                        if let Some(agent) = graph.agents.iter().find(|agent| agent.id == graph_id)
                        {
                            let now = util::now();
                            let encoded = json::dumps(&json::string_list(&agent.responsibilities));
                            state.responsibilities.push(crate::db::Responsibility {
                                agent_id: id.into(),
                                graph_agent_id: Some(graph_id.into()),
                                role: Some(agent.role.clone()),
                                model: Some(agent.model.clone()),
                                responsibilities: Some(encoded.clone()),
                                current_task: None,
                                scope: None,
                                updated_at: now,
                            });
                            if let Value::Object(pairs) = &mut value {
                                pairs.push((
                                    "responsibilities".into(),
                                    json::parse(&encoded).unwrap_or_else(|_| json::arr(vec![])),
                                ));
                                pairs.push(("role".into(), json::s(&agent.role)));
                                pairs.push(("graph_agent_id".into(), json::s(graph_id)));
                                pairs.push(("current_task".into(), json::null()));
                            }
                        }
                    }
                }
                value
            }
            "heartbeat" => act_identity::heartbeat(state, need_str(args, "agent_id")?),
            "deregister_agent" => act_identity::deregister(state, need_str(args, "agent_id")?),
            "list_agents" => act_identity::list_rows(
                state,
                opt_bool(args, "active_only", true),
                opt_f(args, "stale_timeout", 600.0),
                false,
            ),
            "get_agent_relations" => act_identity::relations(
                state,
                need_str(args, "agent_id")?,
                opt_str(args, "mode").unwrap_or("lineage"),
            ),
            "acquire_lock" => act_lock::acquire(
                state,
                need_str(args, "document_path")?,
                need_str(args, "agent_id")?,
                opt_str(args, "lock_type").unwrap_or("exclusive"),
                opt_f(args, "ttl", act_lock::default_ttl()),
                opt_bool(args, "force", false),
                opt_i(args, "region_start"),
                opt_i(args, "region_end"),
                root_ref,
                1,
            ),
            "release_lock" => act_lock::release(
                state,
                need_str(args, "document_path")?,
                need_str(args, "agent_id")?,
                opt_i(args, "region_start"),
                opt_i(args, "region_end"),
                root_ref,
            ),
            "refresh_lock" => act_lock::refresh(
                state,
                need_str(args, "document_path")?,
                need_str(args, "agent_id")?,
                opt_f(args, "ttl", act_lock::default_ttl()),
                opt_i(args, "region_start"),
                opt_i(args, "region_end"),
                root_ref,
            ),
            "get_lock_status" => {
                act_lock::status(state, need_str(args, "document_path")?, root_ref)
            }
            "list_locks" => act_lock::list_locks(state, opt_str(args, "agent_id")),
            "admin_locks" => self.admin_locks(args)?,
            "broadcast" => act_board::broadcast(
                state,
                need_str(args, "agent_id")?,
                opt_str(args, "document_path"),
                opt_f(args, "ttl", 30.0),
                opt_strings(args, "handoff_targets").as_deref(),
                opt_bool(args, "require_ack", false),
                opt_str(args, "message"),
                root_ref,
            ),
            "acknowledge_broadcast" => act_board::ack_broadcast(
                state,
                need_i(args, "broadcast_id")?,
                need_str(args, "agent_id")?,
            ),
            "wait_for_broadcast_acks" => {
                let status = act_board::broadcast_status(state, need_i(args, "broadcast_id")?);
                if status.get("found").and_then(Value::as_bool) != Some(true) {
                    json::obj(vec![
                        ("timed_out", json::b(true)),
                        ("reason", json::s("not_found")),
                    ])
                } else {
                    let expected = status
                        .get("expected_count")
                        .and_then(Value::as_i64)
                        .unwrap_or(0);
                    let acked = status
                        .get("acknowledged_by")
                        .cloned()
                        .unwrap_or(json::arr(vec![]));
                    let pending = status
                        .get("pending_acks")
                        .cloned()
                        .unwrap_or(json::arr(vec![]));
                    let acked_len = acked
                        .as_array()
                        .map(|items| items.len() as i64)
                        .unwrap_or(0);
                    if expected <= 0 {
                        json::obj(vec![
                            ("timed_out", json::b(false)),
                            ("acknowledged_by", acked),
                        ])
                    } else {
                        json::obj(vec![
                            ("timed_out", json::b(acked_len < expected)),
                            ("acknowledged_by", acked),
                            ("pending_acks", pending),
                        ])
                    }
                }
            }
            "wait_for_locks" => {
                let paths = opt_strings(args, "document_paths").unwrap_or_default();
                let busy = paths.iter().any(|path| {
                    act_lock::status(state, path, root_ref)
                        .get("locked")
                        .and_then(Value::as_bool)
                        .unwrap_or(false)
                });
                json::obj(vec![
                    ("ready", json::b(!busy)),
                    ("timed_out", json::b(busy)),
                    ("document_paths", json::string_list(&paths)),
                ])
            }
            "await_agent" => json::obj(vec![
                ("awaited", json::b(false)),
                ("agent_id", json::s(need_str(args, "agent_id")?)),
                ("status", json::s("timeout")),
                (
                    "timeout_s",
                    json::f(opt_f(args, "timeout_s", 60.0)),
                ),
            ]),
            "notify_change" => act_lock::notify(
                state,
                need_str(args, "document_path")?,
                need_str(args, "change_type")?,
                need_str(args, "agent_id")?,
                root_ref,
            ),
            "get_notifications" => {
                if opt_f_opt(args, "prune_max_age_seconds").is_some()
                    || opt_i(args, "prune_max_entries").is_some()
                {
                    let _ = act_lock::prune_notifications(
                        state,
                        opt_f_opt(args, "prune_max_age_seconds"),
                        opt_i(args, "prune_max_entries"),
                    );
                }
                act_lock::notifications(
                    state,
                    opt_f_opt(args, "since"),
                    opt_str(args, "exclude_agent"),
                    opt_i(args, "limit").unwrap_or(100),
                )
            }
            "get_conflicts" => act_lock::conflicts(
                state,
                opt_str(args, "document_path"),
                opt_str(args, "agent_id"),
                opt_i(args, "limit").unwrap_or(20),
                root_ref,
            ),
            "get_contention_hotspots" => {
                act_lock::hotspots(state, opt_i(args, "limit").unwrap_or(10))
            }
            "status" => act_board::system_status(state, graph_loaded, tools),
            "load_coordination_spec" => {
                let path = opt_str(args, "path");
                let (value, graph) = act_view::load_spec(state, root_ref, path);
                let loaded = value.get("loaded").and_then(Value::as_bool).unwrap_or(false);
                let missing = path.map(|path| !std::path::Path::new(path).is_file()).unwrap_or(false);
                if !missing {
                    state.record_event(
                        "graph.loaded",
                        json::obj(vec![
                            ("loaded", json::b(loaded)),
                            (
                                "path",
                                match path {
                                    Some(path) => json::s(path),
                                    None => json::null(),
                                },
                            ),
                        ]),
                    );
                }
                self.graph = graph;
                value
            }
            "scan_project" => {
                let exts = opt_strings(args, "extensions");
                if let Some(exts) = &exts {
                    if exts.is_empty() {
                        return Ok(json::obj(vec![
                            ("scanned", json::i(0)),
                            ("owned", json::i(0)),
                            ("error", json::s("extensions list cannot be empty")),
                        ]));
                    }
                }
                act_view::scan(
                    state,
                    root_ref,
                    opt_str(args, "worktree_root"),
                    exts.as_deref(),
                )
            }
            "get_agent_status" => act_view::agent_status(state, need_str(args, "agent_id")?),
            "get_file_agent_map" => act_view::file_map(state, opt_str(args, "agent_id")),
            "update_agent_status" => act_board::update_agent_status(
                state,
                need_str(args, "agent_id")?,
                opt_str(args, "current_task").map(|s| s.to_string()),
                opt_strings(args, "scope"),
            ),
            "get_agent_tree" => act_view::agent_tree(state, opt_str(args, "agent_id")),
            "run_assessment" => {
                let implicit;
                let graph_ref = if let Some(graph) = graph.as_ref() {
                    graph
                } else {
                    implicit = act_view::implicit_graph(state);
                    &implicit
                };
                let value = act_view::assess(
                    state,
                    opt_str(args, "suite_path"),
                    opt_str(args, "format").unwrap_or("markdown"),
                    opt_str(args, "graph_agent_id"),
                    Some(graph_ref),
                );
                state.record_event(
                    "assessment.completed",
                    json::obj(vec![
                        ("suite_path", json::opt_str(opt_str(args, "suite_path"))),
                        ("graph_agent_id", json::opt_str(opt_str(args, "graph_agent_id"))),
                        (
                            "scores",
                            value.get("scores").cloned().unwrap_or(Value::Object(vec![])),
                        ),
                    ]),
                );
                value
            }
            "send_message" => act_board::send_message(
                state,
                need_str(args, "from_agent_id")?,
                need_str(args, "to_agent_id")?,
                need_str(args, "message_type")?,
                args.get("payload"),
                opt_str(args, "caller_agent_id"),
                true,
            ),
            "manage_messages" => self.manage_messages(args)?,
            "create_task" => {
                let task_id = need_str(args, "task_id")?;
                let parent = need_str(args, "parent_agent_id")?;
                let description = need_str(args, "description")?;
                let value = act_board::create_task(
                    state,
                    task_id,
                    parent,
                    description,
                    opt_strings(args, "depends_on").as_deref(),
                    opt_i(args, "priority").unwrap_or(0),
                )?;
                state.record_event(
                    "task.created",
                    json::obj(vec![
                        ("task_id", json::s(task_id)),
                        ("parent_agent_id", json::s(parent)),
                        ("description", json::s(description)),
                    ]),
                );
                value
            }
            "assign_task" => act_board::assign_task(
                state,
                need_str(args, "task_id")?,
                need_str(args, "assigned_agent_id")?,
            ),
            "update_task_status" => act_board::update_task(
                state,
                need_str(args, "task_id")?,
                need_str(args, "status")?,
                opt_str(args, "summary").map(|s| s.to_string()),
                opt_str(args, "blocked_by").map(|s| s.to_string()),
                opt_str(args, "error").map(|s| s.to_string()),
            ),
            "query_tasks" => act_board::query_tasks(
                state,
                need_str(args, "query_type")?,
                opt_str(args, "task_id"),
                opt_str(args, "parent_agent_id"),
                opt_str(args, "assigned_agent_id"),
                opt_str(args, "parent_task_id"),
                opt_str(args, "root_task_id"),
            ),
            "create_subtask" => act_board::create_subtask(
                state,
                need_str(args, "task_id")?,
                need_str(args, "parent_task_id")?,
                need_str(args, "parent_agent_id")?,
                need_str(args, "description")?,
                opt_strings(args, "depends_on").as_deref(),
                opt_i(args, "priority").unwrap_or(0),
            )?,
            "wait_for_task" => json::obj(vec![
                ("task_id", json::s(need_str(args, "task_id")?)),
                ("status", json::s("timeout")),
                ("timed_out", json::b(true)),
                ("timeout_s", json::f(opt_f(args, "timeout_s", 60.0))),
            ]),
            "get_available_tasks" => act_board::available_tasks(state, opt_str(args, "agent_id")),
            "task_failures" => act_board::task_failures(
                state,
                need_str(args, "action")?,
                opt_str(args, "task_id"),
                opt_i(args, "limit").unwrap_or(50),
            ),
            "manage_work_intents" => self.manage_intents(args)?,
            "wait_for_handoff" => self.wait_handoff(args)?,
            "manage_dependencies" => act_board::manage_deps(
                state,
                need_str(args, "mode")?,
                opt_str(args, "agent_id"),
                opt_str(args, "dependent_agent_id"),
                opt_str(args, "depends_on_agent_id"),
                opt_str(args, "depends_on_task_id"),
                opt_str(args, "condition").unwrap_or("task_completed"),
                opt_i(args, "dep_id"),
            ),
            "acquire_coordinator_lease" => {
                act_board::acquire_lease(state, need_str(args, "agent_id")?, opt_value(args, "ttl"), true)
            }
            "manage_leases" => self.manage_leases(args)?,
            "spawn_subagent" => act_board::spawn(
                state,
                need_str(args, "parent_agent_id")?,
                opt_str(args, "subagent_type"),
                opt_str(args, "description"),
                opt_str(args, "prompt"),
                opt_str(args, "source").unwrap_or("external"),
            ),
            "report_subagent_spawned" => act_board::report_spawned(
                state,
                need_str(args, "parent_agent_id")?,
                opt_str(args, "subagent_type"),
                need_str(args, "child_agent_id")?,
                opt_str(args, "source").unwrap_or("external"),
                opt_str(args, "caller_agent_id"),
            ),
            "get_pending_spawns" => act_board::pending_spawns(
                state,
                need_str(args, "parent_agent_id")?,
                opt_bool(args, "include_consumed", false),
            ),
            "await_subagent_registration" => json::obj(vec![
                ("registered", json::b(false)),
                ("timed_out", json::b(true)),
                (
                    "parent_agent_id",
                    json::s(need_str(args, "parent_agent_id")?),
                ),
            ]),
            "request_subagent_deregistration" => act_board::request_stop(
                state,
                need_str(args, "parent_agent_id")?,
                need_str(args, "child_agent_id")?,
            ),
            "is_subagent_stop_requested" => {
                act_board::stop_requested(state, need_str(args, "agent_id")?)
            }
            "await_subagent_stopped" => {
                let child = need_str(args, "child_agent_id")?;
                let stopped = state
                    .agent(child)
                    .map(|agent| agent.status == "stopped")
                    .unwrap_or(true);
                if stopped {
                    json::obj(vec![
                        ("stopped", json::b(true)),
                        ("child_agent_id", json::s(child)),
                    ])
                } else {
                    json::obj(vec![
                        ("stopped", json::b(false)),
                        ("timed_out", json::b(true)),
                        ("escalate", json::b(true)),
                        ("child_agent_id", json::s(child)),
                    ])
                }
            }
            other => return Err(validate::unknown_tool(&self.schema, other)),
        })
    }

    fn admin_locks(&mut self, args: &Value) -> Result<Value, String> {
        let action = need_str(args, "action")?;
        let state = &mut self.store.state;
        Ok(match action {
            "release_by_agent" => {
                let Some(agent) = opt_str(args, "agent_id") else {
                    return Ok(json::obj(vec![(
                        "error",
                        json::s("agent_id is required for release_by_agent"),
                    )]));
                };
                let value = act_lock::release_agent(state, agent);
                let removed = value.get("released").and_then(Value::as_i64).unwrap_or(0);
                if removed > 0 {
                    state.record_event(
                        "lock.released",
                        json::obj(vec![
                            ("agent_id", json::s(agent)),
                            ("bulk", json::b(true)),
                            ("count", json::i(removed)),
                        ]),
                    );
                }
                value
            }
            "reap_expired" => act_lock::reap_expired(state, opt_f(args, "grace_seconds", 0.0)),
            "reap_stale" => {
                let mut result = act_identity::reap_stale(state, opt_f(args, "timeout", 600.0));
                let stopped: Vec<String> = state
                    .agents
                    .iter()
                    .filter(|agent| agent.status == "stopped")
                    .map(|agent| agent.agent_id.clone())
                    .collect();
                let before = state.locks.len();
                state
                    .locks
                    .retain(|lock| !stopped.iter().any(|id| id == &lock.locked_by));
                if let Value::Object(pairs) = &mut result {
                    pairs.push((
                        "locks_released".into(),
                        json::i((before - state.locks.len()) as i64),
                    ));
                }
                result
            }
            other => json::obj(vec![("error", json::s(format!("Unknown action: {other}")))]),
        })
    }

    fn manage_messages(&mut self, args: &Value) -> Result<Value, String> {
        let action = need_str(args, "action")?;
        let state = &mut self.store.state;
        Ok(match action {
            "send" => {
                let (Some(from), Some(to), Some(kind)) = (
                    opt_str(args, "from_agent_id"),
                    opt_str(args, "to_agent_id"),
                    opt_str(args, "message_type"),
                ) else {
                    return Ok(json::obj(vec![(
                        "error",
                        json::s(
                            "from_agent_id, to_agent_id, and message_type are required for send",
                        ),
                    )]));
                };
                act_board::send_message(
                    state,
                    from,
                    to,
                    kind,
                    args.get("payload"),
                    opt_str(args, "caller_agent_id"),
                    true,
                )
            }
            "get" => act_board::get_messages(
                state,
                need_str(args, "agent_id")?,
                opt_bool(args, "unread_only", false),
                opt_i(args, "limit").unwrap_or(50),
                opt_i(args, "since_id"),
                opt_str(args, "caller_agent_id"),
            ),
            "mark_read" => {
                let ids = args
                    .get("message_ids")
                    .and_then(Value::as_array)
                    .map(|items| items.iter().filter_map(Value::as_i64).collect::<Vec<_>>());
                act_board::mark_read(
                    state,
                    need_str(args, "agent_id")?,
                    ids.as_deref(),
                    opt_str(args, "caller_agent_id"),
                )
            }
            other => json::obj(vec![(
                "error",
                json::s(format!(
                    "Unknown action: {}",
                    json::py_repr(&json::s(other))
                )),
            )]),
        })
    }

    fn manage_intents(&mut self, args: &Value) -> Result<Value, String> {
        let action = need_str(args, "action")?;
        let state = &mut self.store.state;
        let root = self.project_root.clone();
        Ok(match action {
            "declare" => {
                let (Some(agent), Some(path), Some(intent)) = (
                    opt_str(args, "agent_id"),
                    opt_str(args, "document_path"),
                    opt_str(args, "intent"),
                ) else {
                    return Ok(json::obj(vec![(
                        "error",
                        json::s("document_path and intent are required for declare"),
                    )]));
                };
                act_board::declare_intent(
                    state,
                    agent,
                    path,
                    intent,
                    opt_f(args, "ttl", 60.0),
                    root.as_deref(),
                )
            }
            "get" => act_board::list_intents(state, opt_str(args, "agent_id")),
            "clear" => act_board::clear_intent(
                state,
                need_str(args, "agent_id")?,
                opt_str(args, "document_path"),
                root.as_deref(),
            ),
            other => json::obj(vec![(
                "error",
                json::s(format!(
                    "Unknown action: {}",
                    json::py_repr(&json::s(other))
                )),
            )]),
        })
    }

    fn manage_leases(&mut self, args: &Value) -> Result<Value, String> {
        let action = need_str(args, "action")?;
        let state = &mut self.store.state;
        Ok(match action {
            "acquire" => {
                act_board::acquire_lease(state, need_str(args, "agent_id")?, opt_value(args, "ttl"), true)
            }
            "refresh" => act_board::refresh_lease(state, need_str(args, "agent_id")?),
            "release" => act_board::release_lease_tool(state, need_str(args, "agent_id")?),
            "leader" | "get" => act_board::leader(state),
            "claim" => act_board::claim_leadership(
                state,
                need_str(args, "agent_id")?,
                opt_value(args, "ttl"),
            ),
            other => json::obj(vec![(
                "error",
                json::s(format!(
                    "Unknown action: {}",
                    json::py_repr(&json::s(other))
                )),
            )]),
        })
    }

    fn wait_handoff(&mut self, args: &Value) -> Result<Value, String> {
        let id = need_i(args, "handoff_id")?;
        let mode = opt_str(args, "mode").unwrap_or("completion");
        let state = &mut self.store.state;
        Ok(match mode {
            "ack" => act_board::ack_handoff(state, id, need_str(args, "agent_id")?),
            "complete" => act_board::finish_handoff(state, id, true),
            "cancel" => act_board::finish_handoff(state, id, false),
            "status" => match state.handoffs.iter().find(|row| row.id == id) {
                Some(row) => act_board::handoff_record(row),
                None => json::obj(vec![(
                    "error",
                    json::s(format!("Handoff {id} not found")),
                )]),
            },
            "completion" => {
                let done = state
                    .handoffs
                    .iter()
                    .find(|row| row.id == id)
                    .map(|row| row.status == "completed")
                    .unwrap_or(false);
                json::obj(vec![
                    ("timed_out", json::b(!done)),
                    ("handoff_id", json::i(id)),
                ])
            }
            other => json::obj(vec![(
                "error",
                json::s(format!("Unknown mode: {}", json::py_repr(&json::s(other)))),
            )]),
        })
    }

    fn who(&mut self, args: &Value) -> Value {
        let action = opt_str(args, "action").unwrap_or("status");
        let ttl = opt_f(args, "ttl", 600.0);
        let root = self.project_root.clone();
        let area = opt_str(args, "area").map(|area| act_view::norm_area(area, root.as_deref()));
        act_view::who(
            &mut self.store.state,
            action,
            opt_str(args, "agent_id"),
            area.as_deref(),
            opt_str(args, "to_agent_id"),
            ttl,
            opt_str(args, "note"),
        )
    }

    pub fn dashboard(&mut self) -> Value {
        let _ = self.store.begin();
        let value = act_view::dashboard(&self.store.state);
        self.store.end();
        value
    }

    pub fn cursor(&mut self) -> u64 {
        let _ = self.store.begin();
        let cursor = self.store.event_cursor();
        self.store.end();
        cursor
    }

    fn view<T>(&mut self, body: impl FnOnce(&mut Self) -> T) -> Result<T, String> {
        self.store.begin()?;
        let value = body(self);
        self.store.end();
        Ok(value)
    }

    fn edit<T>(&mut self, body: impl FnOnce(&mut Self) -> T) -> Result<T, String> {
        self.store.begin()?;
        let value = body(self);
        self.store.commit()?;
        self.store.end();
        Ok(value)
    }

    fn blocking(&mut self, tool: &str, args: &Value) -> Result<Value, String> {
        match tool {
            "acquire_lock" => self.acquire_retry(args),
            "get_notifications" => self.wait_notifications(args),
            "wait_for_task" => self.wait_task(args),
            "wait_for_locks" => self.wait_locks(args),
            "await_agent" => self.wait_agent(args),
            "await_subagent_registration" => self.wait_registration(args),
            "await_subagent_stopped" => self.wait_stopped(args),
            "wait_for_broadcast_acks" => self.wait_broadcast(args),
            "wait_for_handoff" => self.wait_handoff_done(args),
            _ => Err(format!("unsupported wait {tool}")),
        }
    }

    fn acquire_retry(&mut self, args: &Value) -> Result<Value, String> {
        let max_retries = opt_i(args, "max_retries").unwrap_or(5);
        let mut backoff = opt_f(args, "backoff_ms", 100.0);
        let timeout_ms = opt_f(args, "timeout_ms", 5000.0);
        let started = std::time::Instant::now();
        let mut attempt = 0_i64;
        loop {
            let current = attempt + 1;
            let result = self.edit(|engine| {
                let root = engine.project_root.clone();
                act_lock::acquire(
                    &mut engine.store.state,
                    need_str(args, "document_path").unwrap_or(""),
                    need_str(args, "agent_id").unwrap_or(""),
                    opt_str(args, "lock_type").unwrap_or("exclusive"),
                    opt_f(args, "ttl", act_lock::default_ttl()),
                    opt_bool(args, "force", false),
                    opt_i(args, "region_start"),
                    opt_i(args, "region_end"),
                    root.as_deref(),
                    current,
                )
            })?;
            if result.get("acquired").and_then(Value::as_bool) == Some(true) {
                return Ok(result);
            }
            if attempt < max_retries {
                let elapsed_ms = started.elapsed().as_secs_f64() * 1000.0;
                let unit = std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .map(|d| (d.subsec_nanos() % 1000) as f64 / 999.0)
                    .unwrap_or(0.5);
                let jitter = backoff * (0.5 + unit);
                if elapsed_ms + jitter <= timeout_ms {
                    std::thread::sleep(std::time::Duration::from_secs_f64(jitter / 1000.0));
                    backoff *= 2.0;
                    attempt += 1;
                    continue;
                }
            }
            return Ok(result);
        }
    }

    fn wait_notifications(&mut self, args: &Value) -> Result<Value, String> {
        if args.get("prune_max_age_seconds").is_some() || args.get("prune_max_entries").is_some() {
            self.edit(|engine| {
                act_lock::prune_notifications(
                    &mut engine.store.state,
                    opt_f_opt(args, "prune_max_age_seconds"),
                    opt_i(args, "prune_max_entries"),
                )
            })?;
        }
        let exclude = opt_str(args, "exclude_agent").map(|s| s.to_string());
        let limit = opt_i(args, "limit").unwrap_or(100);
        let timeout = opt_f(args, "timeout_s", 0.0);
        let poll = opt_f(args, "poll_interval_s", 0.05).max(0.01);
        // Python's hybrid wait spends 0.05 s on its in-process event bus and
        // then gives up before reading the journal when the timeout has
        // already passed. A timeout at or below that window, including a
        // negative one, therefore always times out. This answers at once.
        if timeout <= HYBRID_BUS_WAIT_S {
            return Ok(json::obj(vec![
                ("notifications", json::arr(vec![])),
                ("timed_out", json::b(true)),
            ]));
        }
        let started = std::time::Instant::now();
        let opened = util::now();
        loop {
            let rows = self.view(|engine| {
                notification_window(
                    &engine.store.state,
                    opened,
                    exclude.as_deref(),
                    limit,
                )
            })?;
            if !rows.is_empty() {
                return Ok(json::obj(vec![
                    ("notifications", json::arr(rows)),
                    ("timed_out", json::b(false)),
                ]));
            }
            if !sleep_until(started, timeout, poll) {
                return Ok(json::obj(vec![
                    ("notifications", json::arr(vec![])),
                    ("timed_out", json::b(true)),
                ]));
            }
        }
    }

    fn wait_task(&mut self, args: &Value) -> Result<Value, String> {
        let task_id = need_str(args, "task_id")?.to_string();
        let timeout = opt_f(args, "timeout_s", 60.0);
        let poll = opt_f(args, "poll_interval_s", 0.05).max(0.01);
        let started = std::time::Instant::now();
        loop {
            let status = self.view(|engine| {
                engine
                    .store
                    .state
                    .tasks
                    .iter()
                    .find(|task| task.id == task_id)
                    .map(|task| task.status.clone())
            })?;
            if matches!(status.as_deref(), Some("completed") | Some("failed")) {
                return Ok(json::obj(vec![
                    ("task_id", json::s(&task_id)),
                    ("status", json::s(status.unwrap_or_default())),
                    ("timed_out", json::b(false)),
                    ("waited_s", json::f(waited(started))),
                ]));
            }
            if !sleep_until(started, timeout, poll) {
                return Ok(json::obj(vec![
                    ("task_id", json::s(task_id)),
                    ("status", json::s("timeout")),
                    ("timed_out", json::b(true)),
                    ("timeout_s", json::f(timeout)),
                ]));
            }
        }
    }

    fn wait_locks(&mut self, args: &Value) -> Result<Value, String> {
        let agent = need_str(args, "agent_id")?.to_string();
        let paths = opt_strings(args, "document_paths").unwrap_or_default();
        let timeout = opt_f(args, "timeout_s", 30.0);
        let started = std::time::Instant::now();
        loop {
            let split = self.view(|engine| {
                let root = engine.project_root.clone();
                let mut released = Vec::new();
                let mut waiting = Vec::new();
                for path in &paths {
                    let norm = crate::util::normalize_path(path, root.as_deref());
                    let status = act_lock::status(&engine.store.state, &norm, root.as_deref());
                    let locked = status.get("locked").and_then(Value::as_bool) == Some(true);
                    let holder = status.get("locked_by").and_then(Value::as_str);
                    if !locked || holder == Some(agent.as_str()) {
                        released.push(norm);
                    } else {
                        waiting.push(norm);
                    }
                }
                released.sort();
                waiting.sort();
                released.dedup();
                waiting.dedup();
                (
                    released.into_iter().map(json::s).collect::<Vec<_>>(),
                    waiting.into_iter().map(json::s).collect::<Vec<_>>(),
                )
            })?;
            if split.1.is_empty() || !sleep_until(started, timeout, 0.05) {
                return Ok(json::obj(vec![
                    ("released", json::arr(split.0)),
                    ("timed_out", json::arr(split.1)),
                ]));
            }
        }
    }

    fn wait_agent(&mut self, args: &Value) -> Result<Value, String> {
        let agent_id = need_str(args, "agent_id")?.to_string();
        let timeout = opt_f(args, "timeout_s", 60.0);
        let started = std::time::Instant::now();
        loop {
            let status = self.view(|engine| match engine.store.state.agent(&agent_id) {
                None => Some("not_found".to_string()),
                Some(agent) if agent.status == "stopped" => Some("stopped".to_string()),
                Some(_) => None,
            })?;
            if let Some(status) = status {
                return Ok(json::obj(vec![
                    ("awaited", json::b(true)),
                    ("agent_id", json::s(&agent_id)),
                    ("status", json::s(status)),
                    ("waited_s", json::f(waited(started))),
                ]));
            }
            if !sleep_until(started, timeout, 0.05) {
                return Ok(json::obj(vec![
                    ("awaited", json::b(false)),
                    ("agent_id", json::s(agent_id)),
                    ("status", json::s("timeout")),
                    ("timeout_s", json::f(timeout)),
                ]));
            }
        }
    }

    fn wait_registration(&mut self, args: &Value) -> Result<Value, String> {
        let parent = need_str(args, "parent_agent_id")?.to_string();
        let kind = opt_str(args, "subagent_type").map(|s| s.to_string());
        let timeout = opt_f_opt(args, "timeout").unwrap_or(300.0);
        let started = std::time::Instant::now();
        loop {
            let found = self.view(|engine| {
                engine
                    .store
                    .state
                    .spawns
                    .iter()
                    .find(|row| {
                        row.parent_agent_id == parent
                            && row.status == "registered"
                            && kind
                                .as_ref()
                                .map(|k| row.subagent_type.as_deref() == Some(k))
                                .unwrap_or(true)
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
            })?;
            if let Some(spawn) = found {
                return Ok(json::obj(vec![
                    ("registered", json::b(true)),
                    ("spawn", spawn),
                ]));
            }
            if !sleep_until(started, timeout, 0.05) {
                return Ok(json::obj(vec![
                    ("timed_out", json::b(true)),
                    ("timeout", json::f(timeout)),
                    ("parent_agent_id", json::s(parent)),
                    ("subagent_type", json::opt_string(&kind)),
                ]));
            }
        }
    }

    fn wait_stopped(&mut self, args: &Value) -> Result<Value, String> {
        let child = need_str(args, "child_agent_id")?.to_string();
        let timeout = opt_f_opt(args, "timeout").unwrap_or(30.0);
        let started = std::time::Instant::now();
        loop {
            let stopped = self.view(|engine| {
                engine
                    .store
                    .state
                    .agent(&child)
                    .map(|agent| agent.status == "stopped")
                    .unwrap_or(true)
            })?;
            if stopped {
                return Ok(json::obj(vec![
                    ("stopped", json::b(true)),
                    ("child_agent_id", json::s(&child)),
                ]));
            }
            if !sleep_until(started, timeout, 0.05) {
                return Ok(json::obj(vec![
                    ("timed_out", json::b(true)),
                    ("child_agent_id", json::s(child)),
                    ("timeout", json::f(timeout)),
                    ("escalate", json::b(true)),
                ]));
            }
        }
    }

    fn wait_broadcast(&mut self, args: &Value) -> Result<Value, String> {
        let id = need_i(args, "broadcast_id")?;
        let timeout = opt_f(args, "timeout_s", 30.0);
        let started = std::time::Instant::now();
        loop {
            let status = self.view(|engine| act_board::broadcast_status(&engine.store.state, id))?;
            if status.get("found").and_then(Value::as_bool) != Some(true) {
                return Ok(json::obj(vec![
                    ("timed_out", json::b(true)),
                    ("reason", json::s("not_found")),
                ]));
            }
            let expires = status.get("expires_at").and_then(Value::as_f64).unwrap_or(0.0);
            let acked = status
                .get("acknowledged_by")
                .cloned()
                .unwrap_or(json::arr(vec![]));
            if expires < util::now() {
                return Ok(json::obj(vec![
                    ("timed_out", json::b(true)),
                    ("reason", json::s("expired")),
                    ("acknowledged_by", acked),
                ]));
            }
            let expected = status
                .get("expected_count")
                .and_then(Value::as_i64)
                .unwrap_or(0);
            if expected <= 0 {
                return Ok(json::obj(vec![
                    ("timed_out", json::b(false)),
                    ("acknowledged_by", acked),
                ]));
            }
            let count = acked.as_array().map(|items| items.len() as i64).unwrap_or(0);
            if count >= expected || !sleep_until(started, timeout, 0.05) {
                let pending = status
                    .get("pending_acks")
                    .cloned()
                    .unwrap_or(json::arr(vec![]));
                return Ok(json::obj(vec![
                    ("timed_out", json::b(count < expected)),
                    ("acknowledged_by", acked),
                    ("pending_acks", pending),
                ]));
            }
        }
    }

    fn wait_handoff_done(&mut self, args: &Value) -> Result<Value, String> {
        let id = need_i(args, "handoff_id")?;
        let timeout = opt_f(args, "timeout_s", 30.0);
        let started = std::time::Instant::now();
        loop {
            let done = self.view(|engine| {
                engine
                    .store
                    .state
                    .handoffs
                    .iter()
                    .find(|row| row.id == id)
                    .map(|row| row.status == "completed")
                    .unwrap_or(false)
            })?;
            if done || !sleep_until(started, timeout, 0.05) {
                return Ok(json::obj(vec![
                    ("timed_out", json::b(!done)),
                    ("handoff_id", json::i(id)),
                ]));
            }
        }
    }
}

fn read_only(tool: &str, args: &Value) -> bool {
    match tool {
        "list_agents"
        | "get_agent_relations"
        | "get_lock_status"
        | "list_locks"
        | "get_conflicts"
        | "get_contention_hotspots"
        | "status"
        | "get_agent_status"
        | "get_file_agent_map"
        | "get_agent_tree"
        | "query_tasks"
        | "get_available_tasks"
        | "get_pending_spawns"
        | "is_subagent_stop_requested" => true,
        "get_notifications" => {
            args.get("prune_max_age_seconds").is_none()
                && args.get("prune_max_entries").is_none()
                && opt_f(args, "timeout_s", 0.0) <= 0.0
        }
        "manage_messages" => args.get("action").and_then(Value::as_str) == Some("get"),
        "manage_work_intents" => args.get("action").and_then(Value::as_str) == Some("get"),
        "manage_dependencies" => matches!(
            args.get("mode").and_then(Value::as_str),
            Some("check" | "blockers" | "assert" | "list")
        ),
        "manage_leases" => matches!(
            args.get("action").and_then(Value::as_str),
            Some("leader" | "get")
        ),
        _ => false,
    }
}

fn blocks(tool: &str, args: &Value) -> bool {
    match tool {
        "acquire_lock" => opt_bool(args, "retry", false),
        "get_notifications" => opt_f(args, "timeout_s", 0.0) > 0.0,
        "wait_for_task" | "wait_for_locks" | "await_agent" | "await_subagent_registration"
        | "await_subagent_stopped" | "wait_for_broadcast_acks" => true,
        "wait_for_handoff" => opt_str(args, "mode").unwrap_or("completion") == "completion",
        _ => false,
    }
}

fn who_arguments(args: &Value) -> Result<(), String> {
    if let Some(ttl) = args.get("ttl") {
        if !matches!(ttl, Value::Null) {
            let Some(number) = ttl.as_f64() else {
                return Err(format!(
                    "ttl must be of type 'number' (got '{}')",
                    ttl.py_type_name()
                ));
            };
            if number < 0.0 {
                return Err(format!("{number} is less than the minimum of 0"));
            }
        }
    }
    Ok(())
}

/// How long Python's `_hybrid_wait` blocks on its in-process event bus.
pub const HYBRID_BUS_WAIT_S: f64 = 0.05;

fn notification_window(
    state: &crate::db::State,
    opened_at: f64,
    exclude: Option<&str>,
    limit: i64,
) -> Vec<Value> {
    // Same window as Python's hybrid wait: events from the last second, not older rows.
    let since = opened_at - 1.0;
    let mut rows: Vec<_> = state
        .notifications
        .iter()
        .filter(|row| row.created_at > since)
        .filter(|row| exclude.map(|id| row.agent_id != id).unwrap_or(true))
        .collect();
    rows.sort_by(|a, b| b.id.cmp(&a.id));
    if limit >= 0 {
        rows.truncate(limit as usize);
    }
    rows.iter()
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
        .collect()
}

fn waited(start: std::time::Instant) -> f64 {
    start.elapsed().as_secs_f64()
}

fn sleep_until(start: std::time::Instant, timeout: f64, poll: f64) -> bool {
    let limit = std::time::Duration::from_secs_f64(timeout.max(0.0));
    let elapsed = start.elapsed();
    if elapsed >= limit {
        return false;
    }
    let left = (limit - elapsed).as_secs_f64();
    let slice = poll.min(left).max(0.0);
    if slice > 0.0 {
        std::thread::sleep(std::time::Duration::from_secs_f64(slice));
    }
    start.elapsed() < limit
}

fn need_str<'a>(args: &'a Value, key: &str) -> Result<&'a str, String> {
    args.get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| format!("missing {key}"))
}
fn opt_str<'a>(args: &'a Value, key: &str) -> Option<&'a str> {
    match args.get(key) {
        Some(Value::Null) | None => None,
        Some(other) => other.as_str(),
    }
}
fn opt_bool(args: &Value, key: &str, default: bool) -> bool {
    match args.get(key) {
        Some(Value::Bool(v)) => *v,
        _ => default,
    }
}
fn opt_f(args: &Value, key: &str, default: f64) -> f64 {
    args.get(key).and_then(Value::as_f64).unwrap_or(default)
}
fn opt_value<'a>(args: &'a Value, key: &str) -> Option<&'a Value> {
    match args.get(key) {
        Some(Value::Null) | None => None,
        Some(other) => Some(other),
    }
}
fn opt_f_opt(args: &Value, key: &str) -> Option<f64> {
    match args.get(key) {
        Some(Value::Null) | None => None,
        Some(other) => other.as_f64(),
    }
}
fn opt_i(args: &Value, key: &str) -> Option<i64> {
    match args.get(key) {
        Some(Value::Null) | None => None,
        Some(other) => other.as_i64(),
    }
}
fn need_i(args: &Value, key: &str) -> Result<i64, String> {
    opt_i(args, key).ok_or_else(|| format!("missing {key}"))
}
fn opt_strings(args: &Value, key: &str) -> Option<Vec<String>> {
    match args.get(key) {
        Some(Value::Null) | None => None,
        Some(Value::Array(items)) => Some(
            items
                .iter()
                .filter_map(Value::as_str)
                .map(|s| s.to_string())
                .collect(),
        ),
        _ => None,
    }
}
