//! MCP tool groups: `COORDINATIONHUB_TOOLS` and `COORDINATIONHUB_DISABLE`.
//!
//! Every MCP tool belongs to one group. `locks`, `tasks` and `messages` are
//! the names the IronAdamant tools share for the features they have in
//! common; the rest are this server's own. `core` is always on. The
//! switches shape only the MCP stdio surface; the CLI is unaffected. With
//! neither variable set, nothing changes.

pub const TOOLS_VAR: &str = "COORDINATIONHUB_TOOLS";
pub const DISABLE_VAR: &str = "COORDINATIONHUB_DISABLE";

/// Group name and its tools, in the order they are reported.
pub const GROUPS: &[(&str, &[&str])] = &[
    (
        "core",
        &["register_agent", "heartbeat", "deregister_agent", "list_agents", "status"],
    ),
    (
        "locks",
        &[
            "acquire_lock",
            "release_lock",
            "refresh_lock",
            "get_lock_status",
            "list_locks",
            "admin_locks",
            "wait_for_locks",
            "get_conflicts",
            "get_contention_hotspots",
            "manage_work_intents",
            "acquire_coordinator_lease",
            "manage_leases",
            "who",
        ],
    ),
    (
        "tasks",
        &[
            "create_task",
            "create_subtask",
            "assign_task",
            "update_task_status",
            "query_tasks",
            "get_available_tasks",
            "wait_for_task",
            "task_failures",
            "manage_dependencies",
        ],
    ),
    (
        "messages",
        &[
            "send_message",
            "manage_messages",
            "broadcast",
            "acknowledge_broadcast",
            "wait_for_broadcast_acks",
            "wait_for_handoff",
            "notify_change",
            "get_notifications",
        ],
    ),
    (
        "spawn",
        &[
            "spawn_subagent",
            "report_subagent_spawned",
            "get_pending_spawns",
            "await_subagent_registration",
            "request_subagent_deregistration",
            "is_subagent_stop_requested",
            "await_subagent_stopped",
            "await_agent",
        ],
    ),
    (
        "agents",
        &[
            "get_agent_relations",
            "get_agent_tree",
            "get_agent_status",
            "update_agent_status",
            "get_file_agent_map",
        ],
    ),
    ("diagnostics", &["scan_project", "load_coordination_spec", "run_assessment"]),
];

/// The group a tool belongs to.
pub fn group_of(tool: &str) -> Option<&'static str> {
    GROUPS
        .iter()
        .find(|(_, tools)| tools.contains(&tool))
        .map(|(group, _)| *group)
}

/// Why a tool is hidden: the variable, and the word to add or remove.
#[derive(Clone)]
struct Hidden {
    tool: &'static str,
    var: &'static str,
    word: String,
}

/// Which tools the MCP surface exposes.
pub struct Surface {
    hidden: Vec<Hidden>,
    configured: bool,
}

impl Surface {
    /// Every tool on: what the server does with neither variable set.
    pub fn all() -> Surface {
        Surface { hidden: Vec::new(), configured: false }
    }

    /// Read the two variables. `Err` is the one line to print before
    /// exiting with code 2.
    pub fn from_env() -> Result<Surface, String> {
        let tools = std::env::var(TOOLS_VAR).ok();
        let disable = std::env::var(DISABLE_VAR).ok();
        Surface::parse(tools.as_deref(), disable.as_deref())
    }

    pub fn parse(tools: Option<&str>, disable: Option<&str>) -> Result<Surface, String> {
        let allow = words(tools);
        let deny = words(disable);
        if allow.is_none() && deny.is_none() {
            return Ok(Surface::all());
        }
        let all_tools: Vec<&'static str> = GROUPS.iter().flat_map(|(_, tools)| tools.iter().copied()).collect();
        let check = |var: &str, names: &[String], allow_all: bool| -> Result<(), String> {
            for name in names {
                let known = GROUPS.iter().any(|(group, _)| group == name)
                    || all_tools.contains(&name.as_str())
                    || (allow_all && name == "all");
                if !known {
                    return Err(unknown(var, name, &all_tools));
                }
            }
            Ok(())
        };
        if let Some(names) = &allow {
            check(TOOLS_VAR, names, true)?;
        }
        if let Some(names) = &deny {
            check(DISABLE_VAR, names, false)?;
        }
        let mut hidden: Vec<Hidden> = Vec::new();
        if let Some(names) = &allow {
            if !names.iter().any(|name| name == "all") {
                for tool in &all_tools {
                    let group = group_of(tool).unwrap_or("core");
                    let listed = group == "core" || names.iter().any(|name| name == group || name == tool);
                    if !listed {
                        hidden.push(Hidden { tool, var: TOOLS_VAR, word: group.to_string() });
                    }
                }
            }
        }
        if let Some(names) = &deny {
            for tool in &all_tools {
                let group = group_of(tool).unwrap_or("core");
                if group == "core" || hidden.iter().any(|item| item.tool == *tool) {
                    continue;
                }
                if let Some(name) = names.iter().find(|name| name.as_str() == group || name.as_str() == *tool) {
                    hidden.push(Hidden { tool, var: DISABLE_VAR, word: name.clone() });
                }
            }
        }
        Ok(Surface { hidden, configured: true })
    }

    /// Whether either variable was set.
    pub fn configured(&self) -> bool {
        self.configured
    }

    pub fn allows(&self, tool: &str) -> bool {
        !self.hidden.iter().any(|item| item.tool == tool)
    }

    /// The error for calling a hidden tool, or `None` when it is exposed.
    pub fn refusal(&self, tool: &str) -> Option<String> {
        let item = self.hidden.iter().find(|item| item.tool == tool)?;
        let group = group_of(tool).unwrap_or("core");
        Some(if item.var == DISABLE_VAR {
            format!(
                "tool \"{tool}\" is disabled (group \"{group}\") by {DISABLE_VAR}; remove \"{}\" from it to enable",
                item.word
            )
        } else {
            format!(
                "tool \"{tool}\" is disabled (group \"{group}\") by {TOOLS_VAR}; add \"{}\" to it to enable",
                item.word
            )
        })
    }

    /// Groups with at least one exposed tool, comma separated.
    pub fn active_groups(&self) -> String {
        GROUPS
            .iter()
            .filter(|(_, tools)| tools.iter().any(|tool| self.allows(tool)))
            .map(|(group, _)| *group)
            .collect::<Vec<_>>()
            .join(", ")
    }

    /// Number of exposed tools.
    pub fn exposed(&self) -> usize {
        GROUPS.iter().map(|(_, tools)| tools.iter().filter(|tool| self.allows(tool)).count()).sum()
    }
}

/// Split a variable into lowercase names; `None` when unset or blank.
fn words(raw: Option<&str>) -> Option<Vec<String>> {
    let names: Vec<String> = raw?
        .split(',')
        .map(|part| part.trim().to_lowercase())
        .filter(|part| !part.is_empty())
        .collect();
    (!names.is_empty()).then_some(names)
}

fn unknown(var: &str, name: &str, all_tools: &[&str]) -> String {
    let groups = GROUPS.iter().map(|(group, _)| *group).collect::<Vec<_>>().join(", ");
    format!("{var}: unknown group \"{name}\"; groups: {groups}; tools: {}", all_tools.join(", "))
}
