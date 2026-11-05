//! MCP tool groups, chosen by an agent in the project's AGENTS.md
//! (`agent-tools` block) or by the operator with `COORDINATIONHUB_TOOLS`
//! and `COORDINATIONHUB_DISABLE`, which win per key.
//!
//! Every MCP tool belongs to one group. `locks`, `tasks` and `messages` are
//! the names the IronAdamant tools share for the features they have in
//! common; the rest are this server's own. `core` is always on. The
//! switches shape only the MCP stdio surface; the CLI is unaffected. With
//! no switch and no block, nothing changes.

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

/// Where a setting came from.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Source {
    Env,
    AgentsMd,
}

/// The tool name used on `agent-tools` lines.
pub const AGENTS_TOOL: &str = "coordinationhub";

/// Why a tool is hidden: which key hid it, and the word to add or remove.
#[derive(Clone)]
struct Hidden {
    tool: &'static str,
    allowlist: bool,
    source: Source,
    word: String,
}

/// Which tools the MCP surface exposes.
pub struct Surface {
    hidden: Vec<Hidden>,
    configured: bool,
    warnings: Vec<String>,
}

/// The two switch values from the environment, already checked.
#[derive(Clone, Default)]
pub struct EnvSwitches {
    tools: Option<Vec<String>>,
    disable: Option<Vec<String>>,
}

impl EnvSwitches {
    /// Read and check `COORDINATIONHUB_TOOLS` and `COORDINATIONHUB_DISABLE`.
    /// `Err` is the one line to print before exiting with code 2.
    pub fn from_env() -> Result<EnvSwitches, String> {
        let tools = std::env::var(TOOLS_VAR).ok();
        let disable = std::env::var(DISABLE_VAR).ok();
        EnvSwitches::parse(tools.as_deref(), disable.as_deref())
    }

    pub fn parse(tools: Option<&str>, disable: Option<&str>) -> Result<EnvSwitches, String> {
        let tools = words(tools);
        let disable = words(disable);
        if let Some(names) = &tools {
            check(names, true).map_err(|problem| problem.env_line(TOOLS_VAR))?;
        }
        if let Some(names) = &disable {
            check(names, false).map_err(|problem| problem.env_line(DISABLE_VAR))?;
        }
        Ok(EnvSwitches { tools, disable })
    }
}

/// What is wrong with a list of names.
enum Problem {
    Unknown(String),
    CoreGroup,
    CoreTool(String),
}

impl Problem {
    fn env_line(&self, var: &str) -> String {
        match self {
            Problem::Unknown(name) => format!(
                "{var}: unknown group \"{name}\"; groups: {}; tools: {}",
                group_names(),
                all_tools().join(", ")
            ),
            Problem::CoreGroup => format!("{var}: group \"core\" is always on and cannot be disabled"),
            Problem::CoreTool(name) => format!(
                "{var}: tool \"{name}\" is in group \"core\", which is always on and cannot be disabled"
            ),
        }
    }

    fn file_line(&self) -> String {
        match self {
            Problem::Unknown(name) => format!(
                "AGENTS.md agent-tools: unknown group \"{name}\" for {AGENTS_TOOL}; groups: {}",
                group_names()
            ),
            Problem::CoreGroup => format!(
                "AGENTS.md agent-tools: group \"core\" is always on and cannot be disabled for {AGENTS_TOOL}"
            ),
            Problem::CoreTool(name) => format!(
                "AGENTS.md agent-tools: tool \"{name}\" is in group \"core\", which is always on and cannot be disabled for {AGENTS_TOOL}"
            ),
        }
    }
}

fn all_tools() -> Vec<&'static str> {
    GROUPS.iter().flat_map(|(_, tools)| tools.iter().copied()).collect()
}

fn group_names() -> String {
    GROUPS.iter().map(|(group, _)| *group).collect::<Vec<_>>().join(", ")
}

/// Check names for an allowlist (`all` accepted) or a disable list (no core).
fn check(names: &[String], allowlist: bool) -> Result<(), Problem> {
    let tools = all_tools();
    for name in names {
        let known = GROUPS.iter().any(|(group, _)| group == name)
            || tools.contains(&name.as_str())
            || (allowlist && name == "all");
        if !known {
            return Err(Problem::Unknown(name.clone()));
        }
    }
    if !allowlist {
        for name in names {
            if name == "core" {
                return Err(Problem::CoreGroup);
            }
            if group_of(name) == Some("core") {
                return Err(Problem::CoreTool(name.clone()));
            }
        }
    }
    Ok(())
}

/// The `tools` and `disable` lines this server reads from an AGENTS.md
/// `agent-tools` block, each checked; a bad line becomes a warning and is
/// left out so the default applies for that key.
#[derive(Default)]
pub struct FileSwitches {
    tools: Option<Vec<String>>,
    disable: Option<Vec<String>>,
    warnings: Vec<String>,
}

impl FileSwitches {
    pub fn parse(markdown: &str) -> FileSwitches {
        let mut out = FileSwitches::default();
        let Some(lines) = agent_tools_block(markdown) else {
            return out;
        };
        let mut tools: Vec<String> = Vec::new();
        let mut disable: Vec<String> = Vec::new();
        let (mut saw_tools, mut saw_disable) = (false, false);
        for raw in lines {
            let line = raw.split('#').next().unwrap_or("").trim();
            if line.is_empty() {
                continue;
            }
            let Some((tool, rest)) = line.split_once(':') else {
                continue;
            };
            if tool.trim() != AGENTS_TOOL {
                continue;
            }
            let rest = rest.trim();
            let (key, list) = rest.split_once(char::is_whitespace).unwrap_or((rest, ""));
            let names: Vec<String> = list
                .split(|ch: char| ch == ',' || ch.is_whitespace())
                .map(|name| name.trim().to_lowercase())
                .filter(|name| !name.is_empty())
                .collect();
            // A bad line is skipped with a warning; good lines still apply.
            match key.to_lowercase().as_str() {
                "tools" => match check(&names, true) {
                    Ok(()) if !names.is_empty() => {
                        saw_tools = true;
                        tools.extend(names);
                    }
                    Ok(()) => {}
                    Err(problem) => out.warnings.push(problem.file_line()),
                },
                "disable" => match check(&names, false) {
                    Ok(()) if !names.is_empty() => {
                        saw_disable = true;
                        disable.extend(names);
                    }
                    Ok(()) => {}
                    Err(problem) => out.warnings.push(problem.file_line()),
                },
                other => out.warnings.push(format!(
                    "AGENTS.md agent-tools: unknown key \"{other}\" for {AGENTS_TOOL}; use \"tools\" or \"disable\""
                )),
            }
        }
        if saw_tools {
            out.tools = Some(tools);
        }
        if saw_disable {
            out.disable = Some(disable);
        }
        out
    }
}

/// The lines of the first fenced code block whose info string is
/// `agent-tools`. Other fenced blocks are skipped whole, so an example
/// inside a longer fence is not read.
pub fn agent_tools_block(markdown: &str) -> Option<Vec<&str>> {
    let mut lines = markdown.lines();
    while let Some(line) = lines.next() {
        let Some((fence_char, fence_len, info)) = fence_open(line) else {
            continue;
        };
        let mut body = Vec::new();
        for inner in lines.by_ref() {
            if fence_close(inner, fence_char, fence_len) {
                break;
            }
            body.push(inner);
        }
        if info == "agent-tools" {
            return Some(body);
        }
    }
    None
}

fn fence_open(line: &str) -> Option<(char, usize, &str)> {
    let indent = line.len() - line.trim_start_matches(' ').len();
    if indent > 3 {
        return None;
    }
    let rest = &line[indent..];
    let fence_char = rest.chars().next().filter(|ch| *ch == '`' || *ch == '~')?;
    let len = rest.chars().take_while(|ch| *ch == fence_char).count();
    if len < 3 {
        return None;
    }
    let info = rest[len..].trim();
    if fence_char == '`' && info.contains('`') {
        return None;
    }
    Some((fence_char, len, info.split_whitespace().next().unwrap_or("")))
}

fn fence_close(line: &str, fence_char: char, fence_len: usize) -> bool {
    let indent = line.len() - line.trim_start_matches(' ').len();
    if indent > 3 {
        return false;
    }
    let rest = line[indent..].trim_end();
    rest.chars().count() >= fence_len && rest.chars().all(|ch| ch == fence_char)
}

impl Surface {
    /// Every tool on: what the server does with no switch set.
    pub fn all() -> Surface {
        Surface { hidden: Vec::new(), configured: false, warnings: Vec::new() }
    }

    /// Environment switches only (no AGENTS.md).
    pub fn parse(tools: Option<&str>, disable: Option<&str>) -> Result<Surface, String> {
        Ok(Surface::resolve(&EnvSwitches::parse(tools, disable)?, None))
    }

    /// Combine the checked environment switches with the project's
    /// AGENTS.md text. Per key, the environment wins.
    pub fn resolve(env: &EnvSwitches, agents_md: Option<&str>) -> Surface {
        let file = agents_md.map(FileSwitches::parse).unwrap_or_default();
        let (tools, tools_from) = match (&env.tools, &file.tools) {
            (Some(names), _) => (Some(names.clone()), Source::Env),
            (None, Some(names)) => (Some(names.clone()), Source::AgentsMd),
            (None, None) => (None, Source::Env),
        };
        let (disable, disable_from) = match (&env.disable, &file.disable) {
            (Some(names), _) => (Some(names.clone()), Source::Env),
            (None, Some(names)) => (Some(names.clone()), Source::AgentsMd),
            (None, None) => (None, Source::Env),
        };
        let configured = tools.is_some() || disable.is_some() || !file.warnings.is_empty();
        let mut hidden: Vec<Hidden> = Vec::new();
        let tools_all = all_tools();
        if let Some(names) = &tools {
            if !names.iter().any(|name| name == "all") {
                for tool in &tools_all {
                    let group = group_of(tool).unwrap_or("core");
                    let listed = group == "core" || names.iter().any(|name| name == group || name == tool);
                    if !listed {
                        hidden.push(Hidden { tool, allowlist: true, source: tools_from, word: group.to_string() });
                    }
                }
            }
        }
        if let Some(names) = &disable {
            for tool in &tools_all {
                if hidden.iter().any(|item| item.tool == *tool) {
                    continue;
                }
                let group = group_of(tool).unwrap_or("core");
                if let Some(name) = names.iter().find(|name| name.as_str() == group || name.as_str() == *tool) {
                    hidden.push(Hidden { tool, allowlist: false, source: disable_from, word: name.clone() });
                }
            }
        }
        Surface { hidden, configured, warnings: file.warnings }
    }

    /// Whether any switch (environment or AGENTS.md) is in effect.
    pub fn configured(&self) -> bool {
        self.configured
    }

    /// Warnings about the AGENTS.md block, one line each.
    pub fn warnings(&self) -> &[String] {
        &self.warnings
    }

    pub fn allows(&self, tool: &str) -> bool {
        !self.hidden.iter().any(|item| item.tool == tool)
    }

    /// The exposed tool names, in group order.
    pub fn exposed_names(&self) -> Vec<&'static str> {
        all_tools().into_iter().filter(|tool| self.allows(tool)).collect()
    }

    /// The error for calling a hidden tool, or `None` when it is exposed.
    pub fn refusal(&self, tool: &str) -> Option<String> {
        let item = self.hidden.iter().find(|item| item.tool == tool)?;
        let group = group_of(tool).unwrap_or("core");
        let word = &item.word;
        Some(match (item.source, item.allowlist) {
            (Source::Env, false) => format!(
                "tool \"{tool}\" is disabled (group \"{group}\") by {DISABLE_VAR}; remove \"{word}\" from it to enable"
            ),
            (Source::Env, true) => format!(
                "tool \"{tool}\" is disabled (group \"{group}\") by {TOOLS_VAR}; add \"{word}\" to it to enable"
            ),
            (Source::AgentsMd, false) => format!(
                "tool \"{tool}\" is disabled (group \"{group}\") by AGENTS.md agent-tools; remove \"{word}\" from the \"{AGENTS_TOOL}: disable\" line to enable"
            ),
            (Source::AgentsMd, true) => format!(
                "tool \"{tool}\" is disabled (group \"{group}\") by AGENTS.md agent-tools; add \"{word}\" to the \"{AGENTS_TOOL}: tools\" line to enable"
            ),
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

    /// Groups with no exposed tool, comma separated.
    pub fn inactive_groups(&self) -> String {
        GROUPS
            .iter()
            .filter(|(_, tools)| !tools.iter().any(|tool| self.allows(tool)))
            .map(|(group, _)| *group)
            .collect::<Vec<_>>()
            .join(", ")
    }

    /// The one line `initialize` ends with: groups on and off, and where
    /// an agent changes them.
    pub fn summary(&self) -> String {
        let off = self.inactive_groups();
        format!(
            "CoordinationHub tool groups on: {}; off: {}. Change them in AGENTS.md with an agent-tools line \"{AGENTS_TOOL}: tools <groups>\" or \"{AGENTS_TOOL}: disable <groups>\".",
            self.active_groups(),
            if off.is_empty() { "none".to_string() } else { off }
        )
    }

    /// `initialize` instructions: warning lines, then the summary line.
    pub fn instructions(&self) -> String {
        let mut lines: Vec<String> = self.warnings.clone();
        lines.push(self.summary());
        lines.join("\n")
    }

    /// Number of exposed tools.
    pub fn exposed(&self) -> usize {
        self.exposed_names().len()
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
