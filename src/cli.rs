//! Command line. Flags follow the Python CLI: they belong after the command.

use crate::engine::Engine;
use crate::http;
use crate::json::{self, Value};
use crate::mcp;
use std::path::PathBuf;

pub fn main(argv: &[String]) -> i32 {
    if let Some(command) = argv.first() {
        if argv.iter().skip(1).any(|arg| arg == "--help" || arg == "-h") {
            if let Some(text) = crate::cli_help::command_help(command) {
                print!("{text}");
                return 0;
            }
        }
    }
    if argv.iter().any(|arg| arg == "--help" || arg == "-h") && argv.len() <= 2 {
        print_help();
        return 0;
    }
    if argv.is_empty() {
        print_help();
        return 0;
    }
    let command = argv[0].as_str();
    let rest = &argv[1..];
    match command {
        "help" | "--help" | "-h" => {
            print_help();
            0
        }
        "version" | "--version" => {
            println!("{}", crate::VERSION);
            0
        }
        "serve-mcp" => run_serve_mcp(rest),
        "serve" | "serve-sse" => run_serve(command, rest),
        "call" => run_call(rest),
        "bench" => run_bench(rest),
        "hook" => run_hook(rest),
        "init" => run_init(rest),
        "doctor" => run_doctor(rest),
        "cancel-spawn" => run_cancel_spawn(rest),
        "watch" => run_watch(rest),
        "auto-start-dashboard" => run_auto_dashboard(rest),
        _ => run_tool_command(command, rest),
    }
}

fn print_help() {
    print!("{}", crate::cli_catalog::MAIN_HELP);
}

#[allow(dead_code)]
const COMMANDS: &[&str] = &[
    "serve",
    "serve-mcp",
    "serve-sse",
    "status",
    "register",
    "heartbeat",
    "deregister",
    "list-agents",
    "agent-relations",
    "acquire-lock",
    "release-lock",
    "refresh-lock",
    "lock-status",
    "list-locks",
    "admin-locks",
    "broadcast",
    "acknowledge-broadcast",
    "wait-for-broadcast-acks",
    "acknowledge-handoff",
    "complete-handoff",
    "cancel-handoff",
    "get-handoffs",
    "wait-for-handoff",
    "wait-for-locks",
    "notify-change",
    "get-notifications",
    "get-conflicts",
    "prune-notifications",
    "wait-for-notifications",
    "contention-hotspots",
    "load-spec",
    "validate-spec",
    "scan-project",
    "dashboard",
    "agent-status",
    "agent-tree",
    "assess",
    "doctor",
    "init",
    "auto-start-dashboard",
    "watch",
    "await-agent",
    "send-message",
    "get-messages",
    "mark-messages-read",
    "create-task",
    "assign-task",
    "update-task-status",
    "query-tasks",
    "create-subtask",
    "retry-task",
    "dead-letter-queue",
    "task-failure-history",
    "wait-for-task",
    "get-available-tasks",
    "declare-work-intent",
    "get-work-intents",
    "clear-work-intent",
    "declare-dependency",
    "manage-dependencies",
    "satisfy-dependency",
    "get-all-dependencies",
    "acquire-coordinator-lease",
    "refresh-coordinator-lease",
    "release-coordinator-lease",
    "get-leader",
    "claim-leadership",
    "leader-status",
    "ha-dashboard",
    "spawn-subagent",
    "report-subagent-spawned",
    "list-pending-spawns",
    "cancel-spawn",
    "request-subagent-deregistration",
    "await-subagent-stopped",
    "who",
    "call",
    "bench",
    "hook",
];

struct Flags {
    storage: Option<PathBuf>,
    project: Option<PathBuf>,
    namespace: String,
    json: bool,
    positionals: Vec<String>,
    map: Vec<(String, String)>,
    bools: Vec<String>,
}

fn parse_flags(command: &str, args: &[String]) -> Result<Flags, String> {
    let mut flags = Flags {
        storage: None,
        project: None,
        namespace: std::env::var("COORDINATIONHUB_NAMESPACE").unwrap_or_else(|_| "hub".into()),
        json: false,
        positionals: Vec::new(),
        map: Vec::new(),
        bools: Vec::new(),
    };
    let Some(spec) = crate::cli_catalog::command(command) else {
        permissive_flags(&mut flags, args);
        return Ok(flags);
    };
    let mut unknown = Vec::new();
    let mut index = 0;
    while index < args.len() {
        let arg = &args[index];
        if let Some((name, inline)) = arg.split_once('=') {
            if let Some(opt) = spec.options.iter().find(|opt| opt.names.contains(&name)) {
                if !opt.takes_value {
                    unknown.push(arg.clone());
                } else {
                    remember(&mut flags, opt.dest, name, inline);
                }
                index += 1;
                continue;
            }
        }
        if arg.starts_with('-') && arg != "-" {
            if let Some(opt) = spec.options.iter().find(|opt| opt.names.iter().any(|n| *n == arg)) {
                if opt.takes_value {
                    if index + 1 >= args.len() {
                        unknown.push(arg.clone());
                        index += 1;
                        continue;
                    }
                    index += 1;
                    remember(&mut flags, opt.dest, arg, &args[index]);
                    if opt.repeat {
                        while index + 1 < args.len() && !args[index + 1].starts_with('-') {
                            index += 1;
                            remember(&mut flags, opt.dest, arg, &args[index]);
                        }
                    }
                } else {
                    remember_bool(&mut flags, opt.dest, arg);
                }
            } else {
                unknown.push(arg.clone());
                if index + 1 < args.len() && args[index + 1].starts_with('-') && !is_number(&args[index + 1]) {
                    // a following flag is parsed on the next loop
                } else if index + 1 < args.len() && !spec.options.iter().any(|opt| opt.names.iter().any(|n| *n == args[index + 1].as_str())) {
                    // argparse reports the value of an unknown option too when it is not itself a flag
                    if args[index + 1].starts_with('-') || spec.options.iter().all(|opt| !opt.names.iter().any(|n| *n == args[index + 1].as_str())) {
                        // leave it; the next iteration classifies it
                    }
                }
            }
            index += 1;
            continue;
        }
        flags.positionals.push(arg.clone());
        index += 1;
    }
    if !unknown.is_empty() {
        return Err(format!(
            "{}coordinationhub: error: unrecognized arguments: {}\n",
            crate::cli_catalog::MAIN_USAGE,
            unknown.join(" ")
        ));
    }
    if flags.positionals.len() > spec.max_positionals {
        let extra = flags.positionals[spec.max_positionals..].join(" ");
        return Err(format!(
            "{}coordinationhub: error: unrecognized arguments: {extra}\n",
            crate::cli_catalog::MAIN_USAGE
        ));
    }
    if flags.positionals.len() < spec.required.len() {
        let missing = spec.required[flags.positionals.len()..].join(", ");
        return Err(format!(
            "{}coordinationhub {command}: error: the following arguments are required: {missing}\n",
            spec.usage
        ));
    }
    Ok(flags)
}

fn is_number(text: &str) -> bool {
    let rest = text.strip_prefix('-').unwrap_or(text);
    !rest.is_empty() && rest.chars().all(|ch| ch.is_ascii_digit() || ch == '.')
}

fn remember(flags: &mut Flags, dest: &str, name: &str, value: &str) {
    let key = name.trim_start_matches('-').replace('-', "_");
    if dest == "storage_dir" {
        flags.storage = Some(PathBuf::from(value));
    } else if dest == "project_root" {
        flags.project = Some(PathBuf::from(value));
    } else if dest == "namespace" {
        flags.namespace = value.to_string();
    }
    flags.map.push((dest.to_string(), value.to_string()));
    if key != dest {
        flags.map.push((key, value.to_string()));
    }
}

fn remember_bool(flags: &mut Flags, dest: &str, name: &str) {
    if dest == "json_output" || name == "--json" || name == "-j" {
        flags.json = true;
    }
    flags.bools.push(dest.to_string());
    let key = name.trim_start_matches('-').replace('-', "_");
    if key != dest {
        flags.bools.push(key);
    }
}

fn permissive_flags(flags: &mut Flags, args: &[String]) {
    let mut index = 0;
    while index < args.len() {
        let arg = &args[index];
        if arg == "--json" || arg == "-j" {
            flags.json = true;
        } else if arg == "--storage-dir" {
            index += 1;
            flags.storage = args.get(index).map(PathBuf::from);
        } else if arg == "--project-root" {
            index += 1;
            flags.project = args.get(index).map(PathBuf::from);
        } else if arg == "--namespace" {
            index += 1;
            if let Some(value) = args.get(index) {
                flags.namespace = value.clone();
            }
        } else if arg.starts_with("--") && index + 1 < args.len() && !args[index + 1].starts_with('-') {
            let key = arg.trim_start_matches('-').replace('-', "_");
            index += 1;
            flags.map.push((key, args[index].clone()));
        } else if arg.starts_with("--") {
            flags.bools.push(arg.trim_start_matches('-').replace('-', "_"));
        } else {
            flags.positionals.push(arg.clone());
        }
        index += 1;
    }
}

fn flags_or_exit(command: &str, args: &[String]) -> Result<Flags, i32> {
    parse_flags(command, args).map_err(|err| {
        eprint!("{err}");
        2
    })
}

fn flag<'a>(flags: &'a Flags, key: &str) -> Option<&'a str> {
    flags
        .map
        .iter()
        .find(|(k, _)| k == key)
        .map(|(_, v)| v.as_str())
}

fn has(flags: &Flags, key: &str) -> bool {
    flags.bools.iter().any(|item| item == key)
}

fn run_serve_mcp(args: &[String]) -> i32 {
    let flags = match flags_or_exit("serve-mcp", args) {
        Ok(flags) => flags,
        Err(code) => return code,
    };
    match mcp::serve(flags.storage, flags.project, &flags.namespace) {
        Ok(()) => 0,
        Err(err) => {
            eprintln!("Error: {err}");
            1
        }
    }
}

fn run_serve(command: &str, args: &[String]) -> i32 {
    let flags = match flags_or_exit(command, args) {
        Ok(flags) => flags,
        Err(code) => return code,
    };
    let port = flag(&flags, "port")
        .and_then(|v| v.parse().ok())
        .unwrap_or(if command == "serve-sse" { 9898 } else { 9877 });
    let host = flag(&flags, "host").unwrap_or("127.0.0.1").to_string();
    let no_auth = has(&flags, "no_auth");
    let token = flag(&flags, "auth_token").map(|s| s.to_string());
    let namespace = flags.namespace.clone();
    match http::serve(
        flags.storage,
        flags.project,
        &namespace,
        &host,
        port,
        token,
        no_auth,
    ) {
        Ok(()) => 0,
        Err(err) => {
            eprintln!("Error: {err}");
            1
        }
    }
}

fn run_call(args: &[String]) -> i32 {
    let flags = match flags_or_exit("call", args) {
        Ok(flags) => flags,
        Err(code) => return code,
    };
    let Some(tool) = flags.positionals.first() else {
        eprintln!("Error: usage: coordinationhub call <tool> --args JSON");
        return 1;
    };
    let raw = flag(&flags, "args").unwrap_or("{}");
    let parsed = match json::parse(raw) {
        Ok(value) => value,
        Err(err) => {
            eprintln!("Error: {err}");
            return 1;
        }
    };
    emit_tool(&flags, tool, &parsed)
}

fn emit_tool(flags: &Flags, tool: &str, args: &Value) -> i32 {
    let mut engine = match Engine::open(
        flags.storage.as_deref(),
        flags.project.as_deref(),
        &flags.namespace,
    ) {
        Ok(engine) => engine,
        Err(err) => {
            eprintln!("Error: {err}");
            return 1;
        }
    };
    match engine.call(tool, args) {
        Ok(value) => {
            println!("{}", json::dumps_pretty(&value));
            0
        }
        Err(err) => {
            if flags.json {
                println!(
                    "{}",
                    json::dumps_pretty(&json::obj(vec![("error", json::s(err))]))
                );
            } else {
                eprintln!("Error: {err}");
            }
            1
        }
    }
}

fn run_tool_command(command: &str, args: &[String]) -> i32 {
    let flags = match flags_or_exit(command, args) {
        Ok(flags) => flags,
        Err(code) => return code,
    };
    let built = match command_args(command, &flags) {
        Ok(Some(pair)) => pair,
        Ok(None) => {
            eprintln!("Error: unknown command {command}");
            return 2;
        }
        Err(err) => {
            eprintln!("Error: {err}");
            return 2;
        }
    };
    let (tool, payload, _text) = built;
    if command == "prune-notifications"
        && payload.get("prune_max_age_seconds").is_none()
        && payload.get("prune_max_entries").is_none()
    {
        eprintln!(
            "Error: specify at least one of --max-age or --max-entries.\n       --max-age SECONDS   keep rows newer than N seconds\n       --max-entries N     keep at most N rows (newest wins)"
        );
        return 2;
    }
    let mut engine = match Engine::open(
        flags.storage.as_deref(),
        flags.project.as_deref(),
        &flags.namespace,
    ) {
        Ok(engine) => engine,
        Err(err) => {
            eprintln!("Error: {err}");
            return 1;
        }
    };
    if command == "list-agents" || command == "dashboard" {
        let timeout = flag(&flags, "stale_timeout")
            .and_then(|v| v.parse().ok())
            .unwrap_or(600.0);
        let _ = engine.call(
            "admin_locks",
            &json::obj(vec![
                ("action", json::s("reap_stale")),
                ("timeout", json::f(timeout)),
            ]),
        );
    }
    if command == "prune-notifications" {
        let _ = engine.store.begin();
        let pruned = crate::act_lock::prune_notifications(
            &mut engine.store.state,
            payload
                .get("prune_max_age_seconds")
                .and_then(Value::as_f64),
            payload.get("prune_max_entries").and_then(Value::as_i64),
        );
        let _ = engine.store.commit();
        engine.store.end();
        let shown = json::obj(vec![("pruned", json::i(pruned))]);
        if flags.json {
            println!("{}", json::dumps_pretty(&shown));
        } else {
            println!("Pruned {pruned} notification(s)");
        }
        return 0;
    }
    let result = if command == "validate-spec" {
        Ok(crate::act_view::validate_loaded(engine.graph.as_ref()))
    } else if command == "get-handoffs" {
        let _ = engine.store.begin();
        let value = crate::act_board::handoff_rows(
            &engine.store.state,
            flag(&flags, "status"),
            flag(&flags, "from_agent_id"),
            flag(&flags, "limit")
                .and_then(|v| v.parse().ok())
                .unwrap_or(50),
        );
        engine.store.end();
        Ok(value)
    } else {
        engine.call_cli(&tool, &payload)
    };
    match result {
        Ok(value) => {
            let shown = if command == "list-pending-spawns" {
                json::obj(vec![("spawns", value)])
            } else {
                value
            };
            if flags.json {
                println!("{}", json::dumps_pretty(&shown));
                crate::cli_text::exit_code(command, &shown)
            } else {
                let crashed = crate::cli_text::show(command, &shown, &flags.positionals, &flags.map);
                if crashed != 0 {
                    crashed
                } else {
                    crate::cli_text::exit_code(command, &shown)
                }
            }
        }
        Err(err) => {
            eprintln!("Error: {err}");
            1
        }
    }
}

fn command_args(
    command: &str,
    flags: &Flags,
) -> Result<Option<(String, Value, Option<fn(&Value)>)>, String> {
    let pos = &flags.positionals;
    let pair = match command {
        "status" => (
            "status",
            json::obj(vec![]),
            Some(render_status as fn(&Value)),
        ),
        "register" => (
            "register_agent",
            json::obj(vec![
                ("agent_id", json::s(pos.first().ok_or("agent_id required")?)),
                ("parent_id", json::opt_str(flag(flags, "parent_id"))),
                (
                    "graph_agent_id",
                    json::opt_str(flag(flags, "graph_agent_id")),
                ),
                ("worktree_root", json::opt_str(flag(flags, "worktree_root"))),
            ]),
            Some(render_register as fn(&Value)),
        ),
        "heartbeat" => (
            "heartbeat",
            json::obj(vec![(
                "agent_id",
                json::s(pos.first().ok_or("agent_id required")?),
            )]),
            Some(render_heartbeat as fn(&Value)),
        ),
        "deregister" => (
            "deregister_agent",
            json::obj(vec![(
                "agent_id",
                json::s(pos.first().ok_or("agent_id required")?),
            )]),
            Some(render_deregister as fn(&Value)),
        ),
        "list-agents" => (
            "list_agents",
            json::obj(vec![
                ("active_only", json::b(!has(flags, "all"))),
                (
                    "stale_timeout",
                    json::f(
                        flag(flags, "stale_timeout")
                            .and_then(|v| v.parse().ok())
                            .unwrap_or(600.0),
                    ),
                ),
            ]),
            Some(render_agents as fn(&Value)),
        ),
        "acquire-lock" => (
            "acquire_lock",
            json::obj(vec![
                (
                    "document_path",
                    json::s(pos.first().ok_or("document_path required")?),
                ),
                ("agent_id", json::s(pos.get(1).ok_or("agent_id required")?)),
                (
                    "lock_type",
                    json::s(flag(flags, "lock_type").unwrap_or("exclusive")),
                ),
                (
                    "ttl",
                    json::f(
                        flag(flags, "ttl")
                            .and_then(|v| v.parse().ok())
                            .unwrap_or(300.0),
                    ),
                ),
                ("force", json::b(has(flags, "force"))),
                ("retry", json::b(has(flags, "retry"))),
                (
                    "max_retries",
                    json::i(
                        flag(flags, "max_retries")
                            .and_then(|v| v.parse().ok())
                            .unwrap_or(5),
                    ),
                ),
                (
                    "backoff_ms",
                    json::f(
                        flag(flags, "backoff_ms")
                            .and_then(|v| v.parse().ok())
                            .unwrap_or(100.0),
                    ),
                ),
                (
                    "timeout_ms",
                    json::f(
                        flag(flags, "timeout_ms")
                            .and_then(|v| v.parse().ok())
                            .unwrap_or(5000.0),
                    ),
                ),
                (
                    "region_start",
                    json::opt_i(flag(flags, "region_start").and_then(|v| v.parse().ok())),
                ),
                (
                    "region_end",
                    json::opt_i(flag(flags, "region_end").and_then(|v| v.parse().ok())),
                ),
            ]),
            None,
        ),
        "release-lock" => (
            "release_lock",
            json::obj(vec![
                (
                    "document_path",
                    json::s(pos.first().ok_or("document_path required")?),
                ),
                ("agent_id", json::s(pos.get(1).ok_or("agent_id required")?)),
                (
                    "region_start",
                    json::opt_i(flag(flags, "region_start").and_then(|v| v.parse().ok())),
                ),
                (
                    "region_end",
                    json::opt_i(flag(flags, "region_end").and_then(|v| v.parse().ok())),
                ),
            ]),
            None,
        ),
        "lock-status" => (
            "get_lock_status",
            json::obj(vec![(
                "document_path",
                json::s(pos.first().ok_or("document_path required")?),
            )]),
            None,
        ),
        "list-locks" => (
            "list_locks",
            json::obj(vec![("agent_id", json::opt_str(flag(flags, "agent_id")))]),
            None,
        ),
        "notify-change" => (
            "notify_change",
            json::obj(vec![
                (
                    "document_path",
                    json::s(pos.first().ok_or("document_path required")?),
                ),
                (
                    "change_type",
                    json::s(pos.get(1).ok_or("change_type required")?),
                ),
                ("agent_id", json::s(pos.get(2).ok_or("agent_id required")?)),
            ]),
            None,
        ),
        "get-notifications" => (
            "get_notifications",
            json::obj(vec![
                ("since", json::opt_f(flag(flags, "since").and_then(|v| v.parse().ok()))),
                ("exclude_agent", json::opt_str(flag(flags, "exclude_agent"))),
                (
                    "limit",
                    json::i(
                        flag(flags, "limit")
                            .and_then(|v| v.parse().ok())
                            .unwrap_or(100),
                    ),
                ),
            ]),
            None,
        ),
        "get-conflicts" => (
            "get_conflicts",
            json::obj(vec![
                ("document_path", json::opt_str(flag(flags, "document_path"))),
                ("agent_id", json::opt_str(flag(flags, "agent_id"))),
                (
                    "limit",
                    json::i(
                        flag(flags, "limit")
                            .and_then(|v| v.parse().ok())
                            .unwrap_or(20),
                    ),
                ),
            ]),
            None,
        ),
        "send-message" => (
            "send_message",
            json::obj(vec![
                (
                    "from_agent_id",
                    json::s(pos.first().ok_or("from required")?),
                ),
                ("to_agent_id", json::s(pos.get(1).ok_or("to required")?)),
                ("message_type", json::s(pos.get(2).ok_or("type required")?)),
                ("payload", json::opt_str(flag(flags, "payload"))),
            ]),
            None,
        ),
        "create-task" => (
            "create_task",
            json::obj(vec![
                ("task_id", json::s(pos.first().ok_or("task_id required")?)),
                (
                    "parent_agent_id",
                    json::s(pos.get(1).ok_or("parent required")?),
                ),
                (
                    "description",
                    json::s(pos.get(2).ok_or("description required")?),
                ),
                (
                    "priority",
                    json::i(
                        flag(flags, "priority")
                            .and_then(|v| v.parse().ok())
                            .unwrap_or(0),
                    ),
                ),
                (
                    "depends_on",
                    json::arr(
                        flags
                            .map
                            .iter()
                            .filter(|(key, _)| key == "depends_on")
                            .map(|(_, value)| json::s(value.clone()))
                            .collect(),
                    ),
                ),
            ]),
            None,
        ),
        "who" => {
            let verb = flag(flags, "action").map(|s| s.to_string()).or_else(|| {
                pos.first().and_then(|word| {
                    matches!(word.as_str(), "status" | "claim" | "release" | "handoff")
                        .then(|| word.clone())
                })
            });
            let shifted = if verb.is_some() && flag(flags, "action").is_none() && !pos.is_empty()
            {
                1
            } else {
                0
            };
            (
                "who",
                json::obj(vec![
                    (
                        "action",
                        json::s(verb.as_deref().unwrap_or("status")),
                    ),
                    (
                        "agent_id",
                        json::opt_str(
                            flag(flags, "agent_id")
                                .or_else(|| pos.get(shifted).map(|s| s.as_str())),
                        ),
                    ),
                    (
                        "area",
                        json::opt_str(
                            flag(flags, "area")
                                .or_else(|| pos.get(shifted + 1).map(|s| s.as_str())),
                        ),
                    ),
                    (
                        "to_agent_id",
                        json::opt_str(
                            flag(flags, "to_agent_id")
                                .or_else(|| pos.get(shifted + 2).map(|s| s.as_str())),
                        ),
                    ),
                ]),
                None,
            )
        }
        "agent-tree" => (
            "get_agent_tree",
            json::obj(vec![(
                "agent_id",
                json::opt_str(pos.first().map(|s| s.as_str())),
            )]),
            None,
        ),
        "agent-status" => (
            "get_agent_status",
            json::obj(vec![(
                "agent_id",
                json::s(pos.first().ok_or("agent_id required")?),
            )]),
            None,
        ),
        "dashboard" => ("status", json::obj(vec![]), None),
        _ => {
            return crate::cli_cmds::extra(
                command,
                pos,
                |key| flag(flags, key).map(|s| s.to_string()),
                |key| {
                    flags
                        .map
                        .iter()
                        .filter(|(name, _)| name == key)
                        .map(|(_, value)| value.clone())
                        .collect()
                },
                |key| has(flags, key),
            )
            .map(|found| found.map(|(tool, value)| (tool, value, None)));
        }
    };
    Ok(Some((pair.0.to_string(), pair.1, pair.2)))
}

fn render_status(value: &Value) {
    println!("CoordinationHub Status");
    if let Value::Object(pairs) = value {
        for (key, item) in pairs {
            let label = key
                .split('_')
                .map(|word| {
                    let mut chars = word.chars();
                    match chars.next() {
                        Some(first) => first.to_uppercase().collect::<String>() + chars.as_str(),
                        None => String::new(),
                    }
                })
                .collect::<Vec<_>>()
                .join(" ");
            println!("  {label}: {}", json::dumps(item));
        }
    }
}

fn render_register(value: &Value) {
    let id = value.get("agent_id").and_then(Value::as_str).unwrap_or("");
    println!("Registered: {id}");
    if let Some(parent) = value.get("parent_id").and_then(Value::as_str) {
        println!("  Parent: {parent}");
    }
    println!(
        "  Worktree: {}",
        value
            .get("worktree_root")
            .and_then(Value::as_str)
            .unwrap_or("")
    );
}

fn py_bool(value: bool) -> &'static str {
    if value {
        "True"
    } else {
        "False"
    }
}

fn render_heartbeat(value: &Value) {
    let updated = value.get("updated").and_then(Value::as_bool).unwrap_or(false);
    println!("Heartbeat: updated: {}", py_bool(updated));
}

fn render_deregister(value: &Value) {
    println!(
        "  Children orphaned: {}",
        value
            .get("children_orphaned")
            .and_then(Value::as_i64)
            .unwrap_or(0)
    );
    println!(
        "  Locks released: {}",
        value
            .get("locks_released")
            .and_then(Value::as_i64)
            .unwrap_or(0)
    );
}

fn render_agents(value: &Value) {
    let Some(items) = value.get("agents").and_then(Value::as_array) else {
        println!("No agents registered");
        return;
    };
    if items.is_empty() {
        println!("No agents registered");
        return;
    }
    println!("{} agent(s):", items.len());
    for item in items {
        let id = item.get("agent_id").and_then(Value::as_str).unwrap_or("");
        let status = item.get("status").and_then(Value::as_str).unwrap_or("");
        let stale = item.get("stale").and_then(Value::as_bool).unwrap_or(false);
        let mark = if stale { "!" } else { " " };
        let tag = if stale { " (STALE)" } else { "" };
        println!(" {mark} {id}: {status}{tag}");
    }
}

fn run_init(args: &[String]) -> i32 {
    let flags = match flags_or_exit("init", args) {
        Ok(flags) => flags,
        Err(code) => return code,
    };
    let root = flags.project.clone().or_else(|| {
        crate::util::detect_project_root(
            &std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")),
        )
    });
    let storage = crate::db::resolve_storage(flags.storage.as_deref(), root.as_deref());
    if let Err(err) = std::fs::create_dir_all(&storage) {
        eprintln!("Error: {err}");
        return 1;
    }
    if Engine::open(Some(&storage), root.as_deref(), &flags.namespace).is_err() {
        eprintln!("Error: could not initialise storage");
        return 1;
    }
    println!("Storage directory: {}", storage.display());
    println!("Database initialized.");
    let home = std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .unwrap_or_else(|_| ".".into());
    let hooks = PathBuf::from(home)
        .join(".coordinationhub")
        .join("hooks.json");
    if let Some(parent) = hooks.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let exe = std::env::current_exe()
        .map(|p| p.display().to_string())
        .unwrap_or_else(|_| "coordinationhub".into());
    let body = hook_config(&format!("{exe} hook"));
    if let Err(err) = std::fs::write(&hooks, body) {
        eprintln!("Error: {err}");
        return 1;
    }
    println!("Hooks written to {}", hooks.display());
    println!("\nRunning diagnostics...");
    let checks = doctor_checks(&storage);
    let mut all_ok = true;
    for (name, ok, message) in &checks {
        let icon = if *ok { "OK  " } else { "FAIL" };
        if !ok {
            all_ok = false;
        }
        println!("  [{icon}] {name}: {message}");
    }
    if all_ok {
        println!("\nSetup complete. CoordinationHub is ready.");
    } else {
        println!("\nSetup complete with warnings. Check the failures above.");
    }
    0
}

fn run_cancel_spawn(args: &[String]) -> i32 {
    let flags = match flags_or_exit("cancel-spawn", args) {
        Ok(flags) => flags,
        Err(code) => return code,
    };
    let Some(spawn_id) = flags.positionals.first() else {
        eprintln!("Error: spawn_id required");
        return 2;
    };
    let mut engine = match Engine::open(
        flags.storage.as_deref(),
        flags.project.as_deref(),
        &flags.namespace,
    ) {
        Ok(engine) => engine,
        Err(err) => {
            eprintln!("Error: {err}");
            return 1;
        }
    };
    if engine.store.begin().is_err() {
        eprintln!("Error: could not open the journal");
        return 1;
    }
    let value = crate::act_board::cancel_spawn(&mut engine.store.state, spawn_id);
    if engine.store.commit().is_err() {
        engine.store.end();
        eprintln!("Error: could not write the journal");
        return 1;
    }
    engine.store.end();
    if value.get("not_found").and_then(Value::as_bool) == Some(true) {
        if flags.json {
            println!("{}", json::dumps_pretty(&value));
            return 0;
        }
        eprintln!("Spawn not found or already consumed: {spawn_id}");
        return 3;
    }
    if flags.json {
        println!("{}", json::dumps_pretty(&value));
    } else if value.get("cancelled").and_then(Value::as_bool) == Some(true) {
        println!("Spawn cancelled: {spawn_id}");
    } else {
        eprintln!(
            "Cancel failed: {}",
            value.get("error").and_then(Value::as_str).unwrap_or("unknown")
        );
        return 1;
    }
    0
}

fn run_watch(args: &[String]) -> i32 {
    let flags = match flags_or_exit("watch", args) {
        Ok(flags) => flags,
        Err(code) => return code,
    };
    let interval: f64 = flag(&flags, "interval")
        .or_else(|| flag(&flags, "poll_interval"))
        .and_then(|value| value.parse().ok())
        .unwrap_or(5.0);
    loop {
        print!("\x1b[2J\x1b[H");
        let _ = run_tool_command("agent-tree", args);
        let mut engine = match Engine::open(
            flags.storage.as_deref(),
            flags.project.as_deref(),
            &flags.namespace,
        ) {
            Ok(engine) => engine,
            Err(err) => {
                eprintln!("Error: {err}");
                return 1;
            }
        };
        let status = engine.call("status", &json::obj(vec![])).unwrap_or(json::obj(vec![]));
        println!(
            "\n--- {} active agents | {} locks | {} conflicts | refreshing every {interval}s (Ctrl+C to stop) ---",
            status.get("active_agents").and_then(Value::as_i64).unwrap_or(0),
            status.get("active_locks").and_then(Value::as_i64).unwrap_or(0),
            status.get("recent_conflicts").and_then(Value::as_i64).unwrap_or(0)
        );
        std::thread::sleep(std::time::Duration::from_secs_f64(interval.max(0.2)));
    }
}

fn run_auto_dashboard(args: &[String]) -> i32 {
    let flags = match flags_or_exit("auto-start-dashboard", args) {
        Ok(flags) => flags,
        Err(code) => return code,
    };
    let host = flag(&flags, "host").unwrap_or("127.0.0.1");
    let port: u16 = flag(&flags, "port").and_then(|v| v.parse().ok()).unwrap_or(9898);
    if std::net::TcpStream::connect_timeout(
        &std::net::SocketAddr::from((std::net::Ipv4Addr::LOCALHOST, port)),
        std::time::Duration::from_millis(300),
    )
    .is_ok()
    {
        return 0;
    }
    let exe = std::env::current_exe().unwrap_or_else(|_| PathBuf::from("coordinationhub"));
    let _ = std::process::Command::new(exe)
        .args(["serve-sse", "--host", host, "--port", &port.to_string(), "--no-auth"])
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn();
    let _ = flags;
    0
}

fn hook_config(command: &str) -> String {
    let hook = |timeout: i64, status: Option<&str>| {
        let mut pairs = vec![
            ("type", json::s("command")),
            ("command", json::s(command)),
            ("timeout", json::i(timeout)),
        ];
        if let Some(status) = status {
            pairs.push(("statusMessage", json::s(status)));
        }
        json::obj(pairs)
    };
    let block = |matcher: &str, timeout: i64, status: Option<&str>| {
        json::obj(vec![
            ("matcher", json::s(matcher)),
            ("hooks", json::arr(vec![hook(timeout, status)])),
        ])
    };
    let body = json::obj(vec![(
        "hooks",
        json::obj(vec![
            (
                "SessionStart",
                json::arr(vec![block("", 10, Some("Registering with CoordinationHub"))]),
            ),
            (
                "UserPromptSubmit",
                json::arr(vec![block("", 5, Some("Stamping current task"))]),
            ),
            (
                "PreToolUse",
                json::arr(vec![
                    block("Write|Edit", 5, Some("Acquiring file lock")),
                    block("Agent", 5, Some("Stashing sub-agent task")),
                ]),
            ),
            (
                "PostToolUse",
                json::arr(vec![
                    block("Write|Edit", 5, None),
                    block(
                        "mcp__stele-context__index",
                        5,
                        Some("Bridging Stele index to CoordinationHub"),
                    ),
                    block(
                        "mcp__trammel__claim_step",
                        5,
                        Some("Syncing Trammel step to CoordinationHub"),
                    ),
                ]),
            ),
            (
                "SubagentStart",
                json::arr(vec![block("", 5, Some("Registering subagent"))]),
            ),
            (
                "SubagentStop",
                json::arr(vec![block("", 5, None)]),
            ),
            (
                "SessionEnd",
                json::arr(vec![block("", 10, Some("Releasing CoordinationHub locks"))]),
            ),
        ]),
    )]);
    json::dumps_pretty(&body) + "\n"
}

fn doctor_checks(storage: &std::path::Path) -> Vec<(String, bool, String)> {
    let mut checks = Vec::new();
    checks.push((
        "import".into(),
        true,
        format!("coordinationhub {} binary", crate::VERSION),
    ));
    let home = std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .unwrap_or_else(|_| ".".into());
    let hooks = PathBuf::from(&home).join(".coordinationhub").join("hooks.json");
    let required = [
        "SessionStart",
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "SubagentStart",
        "SubagentStop",
        "SessionEnd",
    ];
    let hooks_text = std::fs::read_to_string(&hooks).unwrap_or_default();
    let hooks_ok = required.iter().all(|name| hooks_text.contains(name))
        && hooks_text.contains("coordinationhub");
    let hooks_msg = if hooks_ok {
        format!("hooks configured correctly at {}", hooks.display())
    } else {
        "no IDE hook config found (run 'coordinationhub init' if using an IDE with hooks)".into()
    };
    checks.push(("hooks_config".into(), true, hooks_msg));
    let journal = storage.join("coordination.jsonl");
    let storage_ok = storage.is_dir() && journal.is_file();
    checks.push((
        "storage_dir".into(),
        storage_ok,
        if storage_ok {
            format!("journal exists at {}", journal.display())
        } else {
            format!("journal not found: {}", journal.display())
        },
    ));
    let readable = std::fs::read_to_string(&journal)
        .map(|text| text.lines().all(|line| line.trim().is_empty() || json::parse(line).is_ok()))
        .unwrap_or(false);
    checks.push((
        "schema_version".into(),
        storage_ok && readable,
        if storage_ok && readable {
            "schema is current".into()
        } else {
            "journal is missing or unreadable".into()
        },
    ));
    let binary_ok = std::env::current_exe().map(|path| path.is_file()).unwrap_or(true);
    checks.push((
        "hook_command".into(),
        binary_ok,
        "hook command is the coordinationhub binary".into(),
    ));
    checks
}

fn run_doctor(args: &[String]) -> i32 {
    let flags = match flags_or_exit("doctor", args) {
        Ok(flags) => flags,
        Err(code) => return code,
    };
    let root = flags.project.clone().or_else(|| {
        crate::util::detect_project_root(
            &std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")),
        )
    });
    let storage = crate::db::resolve_storage(flags.storage.as_deref(), root.as_deref());
    let checks = doctor_checks(&storage);
    let all_ok = checks.iter().all(|(_, ok, _)| *ok);
    if flags.json {
        let items = checks
            .iter()
            .map(|(name, ok, message)| {
                json::obj(vec![
                    ("name", json::s(name)),
                    ("ok", json::b(*ok)),
                    ("message", json::s(message)),
                ])
            })
            .collect();
        println!(
            "{}",
            json::dumps_pretty(&json::obj(vec![
                ("checks", json::arr(items)),
                ("all_ok", json::b(all_ok)),
            ]))
        );
        let _ = all_ok;
        return 0;
    }
    for (name, ok, message) in &checks {
        let icon = if *ok { "OK  " } else { "FAIL" };
        println!("  [{icon}] {name}: {message}");
    }
    let _ = all_ok;
    0
}

fn run_hook(args: &[String]) -> i32 {
    let flags = match flags_or_exit("hook", args) {
        Ok(flags) => flags,
        Err(code) => return code,
    };
    let mut input = String::new();
    use std::io::Read;
    let _ = std::io::stdin().read_to_string(&mut input);
    let event = json::parse(&input).unwrap_or(Value::Object(vec![]));
    let cwd = event
        .get("cwd")
        .and_then(Value::as_str)
        .map(PathBuf::from)
        .or(flags.project.clone());
    let mut engine = match Engine::open(flags.storage.as_deref(), cwd.as_deref(), &flags.namespace) {
        Ok(engine) => engine,
        Err(err) => {
            eprintln!("[CoordinationHub] hook error: {err}");
            return 0;
        }
    };
    if let Some(result) = crate::hooks::handle(&mut engine, &event) {
        println!("{}", json::dumps(&result));
    }
    0
}

fn run_bench(args: &[String]) -> i32 {
    let flags = match flags_or_exit("bench", args) {
        Ok(flags) => flags,
        Err(code) => return code,
    };
    let loops = flag(&flags, "loops")
        .and_then(|v| v.parse().ok())
        .unwrap_or(200_i64);
    let dir = flags
        .storage
        .clone()
        .unwrap_or_else(|| std::env::temp_dir().join(format!("chub-bench-{}", std::process::id())));
    let _ = std::fs::remove_dir_all(&dir);
    let mut engine = match Engine::open(Some(&dir), flags.project.as_deref(), &flags.namespace) {
        Ok(engine) => engine,
        Err(err) => {
            eprintln!("Error: {err}");
            return 1;
        }
    };
    let rows = [
        ("register", bench_register(&mut engine, loops)),
        ("lock_acquire_release", bench_locks(&mut engine, loops)),
        ("contended_locks", bench_contended(&mut engine)),
        ("notification_fanout", bench_notes(&mut engine, loops)),
        ("status", bench_status(&mut engine, loops)),
        ("many_agent_scale", bench_scale(&mut engine)),
    ];
    let mut items = Vec::new();
    for (name, seconds) in rows {
        items.push(json::obj(vec![
            ("name", json::s(name)),
            ("seconds", json::f(seconds)),
        ]));
    }
    println!(
        "{}",
        json::dumps_pretty(&json::obj(vec![("rows", json::arr(items))]))
    );
    let _ = std::fs::remove_dir_all(&dir);
    0
}

fn bench_register(engine: &mut Engine, loops: i64) -> f64 {
    let start = std::time::Instant::now();
    for n in 0..loops {
        let _ = engine.call(
            "register_agent",
            &json::obj(vec![("agent_id", json::s(format!("bench.{n}")))]),
        );
    }
    start.elapsed().as_secs_f64()
}

fn bench_locks(engine: &mut Engine, loops: i64) -> f64 {
    let _ = engine.call(
        "register_agent",
        &json::obj(vec![("agent_id", json::s("lock-agent"))]),
    );
    let start = std::time::Instant::now();
    for n in 0..loops {
        let path = format!("file-{n}.txt");
        let _ = engine.call(
            "acquire_lock",
            &json::obj(vec![
                ("document_path", json::s(&path)),
                ("agent_id", json::s("lock-agent")),
            ]),
        );
        let _ = engine.call(
            "release_lock",
            &json::obj(vec![
                ("document_path", json::s(path)),
                ("agent_id", json::s("lock-agent")),
            ]),
        );
    }
    start.elapsed().as_secs_f64()
}

fn bench_notes(engine: &mut Engine, loops: i64) -> f64 {
    let _ = engine.call(
        "register_agent",
        &json::obj(vec![("agent_id", json::s("note-agent"))]),
    );
    let start = std::time::Instant::now();
    for n in 0..loops {
        let _ = engine.call(
            "notify_change",
            &json::obj(vec![
                ("document_path", json::s(format!("n-{n}"))),
                ("change_type", json::s("modified")),
                ("agent_id", json::s("note-agent")),
            ]),
        );
    }
    let _ = engine.call(
        "get_notifications",
        &json::obj(vec![("limit", json::i(loops))]),
    );
    start.elapsed().as_secs_f64()
}

fn bench_status(engine: &mut Engine, loops: i64) -> f64 {
    let start = std::time::Instant::now();
    for _ in 0..loops {
        let _ = engine.call("status", &json::obj(vec![]));
    }
    start.elapsed().as_secs_f64()
}

fn bench_contended(engine: &mut Engine) -> f64 {
    let _ = engine;
    let storage = std::env::temp_dir().join(format!("chub-contend-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&storage);
    let shared = std::sync::Arc::new(std::sync::Mutex::new(
        Engine::open(Some(&storage), None, "hub").expect("contended engine"),
    ));
    {
        let mut guard = shared.lock().expect("lock");
        for id in 0..4 {
            let _ = guard.call(
                "register_agent",
                &json::obj(vec![("agent_id", json::s(format!("c{id}")))]),
            );
        }
    }
    let start = std::time::Instant::now();
    let mut threads = Vec::new();
    for id in 0..4 {
        let shared = shared.clone();
        threads.push(std::thread::spawn(move || {
            for _ in 0..40 {
                let mut guard = shared.lock().expect("lock");
                let _ = guard.call(
                    "acquire_lock",
                    &json::obj(vec![
                        ("document_path", json::s("contended.txt")),
                        ("agent_id", json::s(format!("c{id}"))),
                    ]),
                );
            }
        }));
    }
    for thread in threads {
        let _ = thread.join();
    }
    let elapsed = start.elapsed().as_secs_f64();
    let _ = std::fs::remove_dir_all(&storage);
    elapsed
}

fn bench_scale(engine: &mut Engine) -> f64 {
    let start = std::time::Instant::now();
    for n in 0..1000 {
        let _ = engine.call(
            "register_agent",
            &json::obj(vec![("agent_id", json::s(format!("scale.{n}")))]),
        );
    }
    let _ = engine.call("status", &json::obj(vec![]));
    let _ = engine.call(
        "list_agents",
        &json::obj(vec![("active_only", json::b(true))]),
    );
    start.elapsed().as_secs_f64()
}
