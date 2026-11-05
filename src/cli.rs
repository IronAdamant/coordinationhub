//! Command line. Flags follow the Python CLI: they belong after the command.

use crate::engine::Engine;
use crate::http;
use crate::json::{self, Value};
use crate::mcp;
use std::path::PathBuf;

pub fn main(argv: &[String]) -> i32 {
    // `hook` is this program's IDE hook entry point (see MIGRATION.md).
    if argv.first().map(String::as_str) == Some("hook") {
        return run_hook(hook_flags(&argv[1..]));
    }
    let parsed = match crate::argparse::parse(argv) {
        Ok(parsed) => parsed,
        Err(exit) => {
            print!("{}", exit.stdout);
            eprint!("{}", exit.stderr);
            return exit.code;
        }
    };
    let Some(command) = parsed.command else {
        print!("{}", crate::argparse::format_top_help());
        return 0;
    };
    let flags = Flags::from_parsed(&parsed);
    match command.name {
        "serve-mcp" => run_serve_mcp(flags),
        "serve" | "serve-sse" => run_serve(command.name, flags),
        "init" => run_init(flags),
        "doctor" => run_doctor(flags),
        "cancel-spawn" => run_cancel_spawn(flags),
        "watch" => run_watch(flags),
        "auto-start-dashboard" => run_auto_dashboard(flags),
        name => run_tool_command(name, flags),
    }
}

#[derive(Clone)]
struct Flags {
    storage: Option<PathBuf>,
    project: Option<PathBuf>,
    namespace: String,
    json: bool,
    positionals: Vec<String>,
    map: Vec<(String, String)>,
    bools: Vec<String>,
}

impl Flags {
    fn from_parsed(parsed: &crate::argparse::Parsed) -> Flags {
        let mut flags = Flags {
            storage: parsed.value("storage_dir").map(PathBuf::from),
            project: parsed.value("project_root").map(PathBuf::from),
            namespace: parsed.value("namespace").unwrap_or("hub").to_string(),
            json: parsed.flag("json_output"),
            positionals: parsed.positionals.clone(),
            map: Vec::new(),
            bools: Vec::new(),
        };
        let args = parsed.command.map(|cmd| cmd.args).unwrap_or(&[]);
        let aliases = |dest: &str| -> Vec<String> {
            let mut keys = vec![dest.to_string()];
            if let Some(arg) = args.iter().find(|arg| arg.dest == dest) {
                for name in arg.flags {
                    let key = name.trim_start_matches('-').replace('-', "_");
                    if !keys.contains(&key) {
                        keys.push(key);
                    }
                }
            }
            keys
        };
        for (dest, values) in &parsed.values {
            for key in aliases(dest) {
                for value in values {
                    flags.map.push((key.clone(), value.clone()));
                }
            }
        }
        for dest in &parsed.bools {
            flags.bools.extend(aliases(dest));
        }
        flags
    }
}

/// The hook reads its event from stdin; it only takes the storage flags.
fn hook_flags(args: &[String]) -> Flags {
    let mut flags = Flags {
        storage: None,
        project: None,
        namespace: "hub".into(),
        json: false,
        positionals: Vec::new(),
        map: Vec::new(),
        bools: Vec::new(),
    };
    let mut index = 0;
    while index < args.len() {
        let value = args.get(index + 1).cloned();
        match args[index].as_str() {
            "--storage-dir" => {
                flags.storage = value.map(PathBuf::from);
                index += 1;
            }
            "--project-root" => {
                flags.project = value.map(PathBuf::from);
                index += 1;
            }
            "--namespace" => {
                if let Some(value) = value {
                    flags.namespace = value;
                }
                index += 1;
            }
            _ => {}
        }
        index += 1;
    }
    flags
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

fn run_serve_mcp(flags: Flags) -> i32 {
    // As in 0.8.0: the flags win, then COORDINATIONHUB_STORAGE_DIR and
    // COORDINATIONHUB_PROJECT_ROOT (handy in MCP client configs).
    let from_env = |name: &str| {
        std::env::var(name)
            .ok()
            .filter(|value| !value.is_empty())
            .map(PathBuf::from)
    };
    let storage = flags.storage.clone().or_else(|| from_env("COORDINATIONHUB_STORAGE_DIR"));
    let project = flags.project.clone().or_else(|| from_env("COORDINATIONHUB_PROJECT_ROOT"));
    // COORDINATIONHUB_TOOLS / COORDINATIONHUB_DISABLE: a bad name stops the
    // server with one line and exit code 2, so the client shows it failed.
    let surface = match crate::groups::Surface::from_env() {
        Ok(surface) => surface,
        Err(line) => {
            eprintln!("{line}");
            return 2;
        }
    };
    match mcp::serve(storage, project, &flags.namespace, surface) {
        Ok(()) => 0,
        Err(err) => {
            eprintln!("Error: {err}");
            1
        }
    }
}

fn run_serve(command: &str, flags: Flags) -> i32 {
    let port = flag(&flags, "port")
        .and_then(|v| v.parse().ok())
        .unwrap_or(if command == "serve-sse" { 9898 } else { 9877 });
    let host = flag(&flags, "host").unwrap_or("127.0.0.1").to_string();
    let no_auth = has(&flags, "no_auth");
    let token = flag(&flags, "auth_token").map(|s| s.to_string());
    let namespace = flags.namespace.clone();
    let banner = if command == "serve-sse" {
        http::Banner::Dashboard {
            open_browser: !has(&flags, "no_browser"),
        }
    } else {
        http::Banner::Api
    };
    match http::serve(
        flags.storage,
        flags.project,
        &namespace,
        &host,
        port,
        token,
        no_auth,
        banner,
    ) {
        Ok(()) => 0,
        Err(err) => {
            eprintln!("Error: {err}");
            1
        }
    }
}

fn run_tool_command(command: &str, flags: Flags) -> i32 {
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
    let wait_expired = command == "wait-for-notifications"
        && payload.get("timeout_s").and_then(Value::as_f64).unwrap_or(30.0)
            <= crate::engine::HYBRID_BUS_WAIT_S;
    let result = if wait_expired {
        // The command always waits. A timeout inside the event-bus window
        // (including a negative one) has expired before the journal is read.
        Ok(json::obj(vec![
            ("notifications", json::arr(vec![])),
            ("timed_out", json::b(true)),
        ]))
    } else if command == "validate-spec" {
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
            // As in 0.8.0's cli.main: JSON callers get the error on stdout.
            if flags.json {
                println!("{}", json::dumps_pretty(&json::obj(vec![("error", json::s(err))])));
            } else {
                eprintln!("Error: {err}");
            }
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

fn run_init(flags: Flags) -> i32 {
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
    if root.is_some() || flags.storage.is_some() {
        println!("Storage directory: {}", storage.display());
    } else {
        println!("Storage directory: {} (no git project detected)", storage.display());
    }
    if Engine::open(Some(&storage), root.as_deref(), &flags.namespace).is_err() {
        eprintln!("Error: could not initialise storage at {}", storage.display());
        return 1;
    }
    println!("Database initialized.");
    let hooks = crate::util::home_dir().join(".coordinationhub").join("hooks.json");
    if let Some(parent) = hooks.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let exe = std::env::current_exe()
        .map(|p| p.display().to_string())
        .unwrap_or_else(|_| "coordinationhub".into());
    let command = format!("{exe} hook");
    if let Err(err) = std::fs::write(&hooks, hook_config(&command)) {
        eprintln!("Error: {err}");
        return 1;
    }
    println!("Hooks written to {}", hooks.display());
    println!("  Hook command: {command}");
    println!("\nRunning diagnostics...");
    let checks = doctor_checks(&storage);
    let all_ok = checks.iter().all(|(_, ok, _)| *ok);
    for (name, ok, message) in &checks {
        println!("  [{}] {name}: {message}", if *ok { "OK  " } else { "FAIL" });
    }
    if has(&flags, "auto_dashboard") {
        println!("\nNote: --auto-dashboard is deprecated (Claude Code integration removed).");
        println!("  The dashboard can still be started manually with:");
        println!("    {exe} serve-sse --host 127.0.0.1 --port 9898");
    }
    if has(&flags, "monitor_skill") {
        let dir = crate::util::home_dir()
            .join(".coordinationhub")
            .join("skills")
            .join("coordinationhub-monitor");
        let target = dir.join("SKILL.md");
        let text = MONITOR_SKILL.replace("\r\n", "\n");
        if let Err(err) = std::fs::create_dir_all(&dir).and_then(|_| std::fs::write(&target, text)) {
            eprintln!("Error: {err}");
            return 1;
        }
        println!("\nMonitor skill installed.");
        println!("  Location: {}", target.display());
        println!("  Invoke by asking an LLM to 'watch the swarm' or 'monitor the agents'.");
        println!("  The skill instructs the LLM to poll http://127.0.0.1:9898/api/dashboard-data");
        println!("  every 30 s and surface boundary crossings, blocked tasks, and stale agents.");
        println!("  Install path is vendor-neutral; copy into your IDE's skills directory if needed.");
    }
    if all_ok {
        println!("\nSetup complete. CoordinationHub is ready.");
    } else {
        println!("\nSetup complete with warnings. Check the failures above.");
    }
    0
}

/// The coordinationhub-monitor skill text shipped with 0.8.0.
const MONITOR_SKILL: &str = include_str!("../assets/monitor_skill.md");

fn run_cancel_spawn(flags: Flags) -> i32 {
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

fn run_watch(flags: Flags) -> i32 {
    let interval: f64 = flag(&flags, "interval")
        .or_else(|| flag(&flags, "poll_interval"))
        .and_then(|value| value.parse().ok())
        .unwrap_or(5.0);
    loop {
        print!("\x1b[2J\x1b[H");
        let _ = run_tool_command("agent-tree", flags.clone());
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

fn run_auto_dashboard(flags: Flags) -> i32 {
    // 0.8.0: exit quietly when host:port answers; otherwise start
    // `serve-sse --no-browser` detached, logging to
    // ~/.coordinationhub/dashboard.log. Auth stays on.
    use std::net::ToSocketAddrs;
    let host = flag(&flags, "host").unwrap_or("127.0.0.1");
    let port: u16 = flag(&flags, "port").and_then(|v| v.parse().ok()).unwrap_or(9898);
    let reachable = (host, port)
        .to_socket_addrs()
        .map(|addrs| {
            addrs.into_iter().any(|addr| {
                std::net::TcpStream::connect_timeout(&addr, std::time::Duration::from_millis(300)).is_ok()
            })
        })
        .unwrap_or(false);
    if reachable {
        return 0;
    }
    let log_dir = crate::util::home_dir().join(".coordinationhub");
    if std::fs::create_dir_all(&log_dir).is_err() {
        return 0;
    }
    let Ok(log) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(log_dir.join("dashboard.log"))
    else {
        return 0;
    };
    let Ok(log_err) = log.try_clone() else {
        return 0;
    };
    let exe = std::env::current_exe().unwrap_or_else(|_| PathBuf::from("coordinationhub"));
    let mut command = std::process::Command::new(exe);
    command.args(["serve-sse", "--no-browser", "--host", host, "--port", &port.to_string()]);
    if let Some(dir) = &flags.storage {
        command.arg("--storage-dir").arg(dir);
    }
    if let Some(dir) = &flags.project {
        command.arg("--project-root").arg(dir);
    }
    let _ = command
        .stdin(std::process::Stdio::null())
        .stdout(log)
        .stderr(log_err)
        .spawn();
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

/// 0.8.0's `_check_hooks_config`: the neutral hooks file, then Claude
/// Code's settings, each needing all seven events and a coordinationhub
/// command.
fn hooks_config_check() -> (bool, String) {
    let home = crate::util::home_dir();
    let neutral = home.join(".coordinationhub").join("hooks.json");
    let claude = home.join(".claude").join("settings.json");
    let required = [
        "SessionStart",
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "SubagentStart",
        "SubagentStop",
        "SessionEnd",
    ];
    for path in [&neutral, &claude] {
        let Ok(text) = std::fs::read_to_string(path) else {
            continue;
        };
        let Ok(settings) = json::parse(&text) else {
            continue;
        };
        let Some(Value::Object(hooks)) = settings.get("hooks") else {
            continue;
        };
        if hooks.is_empty() || !required.iter().all(|name| hooks.iter().any(|(key, _)| key == name)) {
            continue;
        }
        let mentions = hooks.iter().any(|(_, matchers)| {
            matchers.as_array().unwrap_or(&[]).iter().any(|block| {
                block
                    .get("hooks")
                    .and_then(Value::as_array)
                    .unwrap_or(&[])
                    .iter()
                    .any(|hook| {
                        hook.get("command")
                            .and_then(Value::as_str)
                            .is_some_and(|cmd| cmd.contains("coordinationhub"))
                    })
            })
        });
        if mentions {
            return (true, format!("hooks configured correctly at {}", path.display()));
        }
    }
    (
        true,
        "no IDE hook config found (run 'coordinationhub init' if using an IDE with hooks)".into(),
    )
}

fn doctor_checks(storage: &std::path::Path) -> Vec<(String, bool, String)> {
    let mut checks = Vec::new();
    checks.push((
        "import".into(),
        true,
        format!("coordinationhub {} binary", crate::VERSION),
    ));
    let (hooks_ok, hooks_msg) = hooks_config_check();
    checks.push(("hooks_config".into(), hooks_ok, hooks_msg));
    let journal = storage.join("coordination.jsonl");
    let (storage_ok, storage_msg) = if !storage.is_dir() {
        (false, format!("storage directory not found: {}", storage.display()))
    } else if !journal.is_file() {
        (false, format!("journal not found: {} (run 'coordinationhub init')", journal.display()))
    } else {
        (true, format!("journal exists at {}", journal.display()))
    };
    checks.push(("storage_dir".into(), storage_ok, storage_msg));
    let schema = if !journal.is_file() {
        (false, "journal not found (run a session first or use 'init')".to_string())
    } else {
        match std::fs::read_to_string(&journal) {
            Err(err) => (false, format!("journal unreadable: {err}")),
            Ok(text) => match text
                .lines()
                .enumerate()
                .filter(|(_, line)| !line.trim().is_empty())
                .find_map(|(index, line)| json::parse(line).err().map(|err| (index + 1, err)))
            {
                Some((line, err)) => (false, format!("journal line {line} does not parse: {err}")),
                None => (true, "journal parses; schema is current".to_string()),
            },
        }
    };
    checks.push(("schema_version".into(), schema.0, schema.1));
    let binary_ok = std::env::current_exe().map(|path| path.is_file()).unwrap_or(true);
    checks.push((
        "hook_command".into(),
        binary_ok,
        "hook command is the coordinationhub binary".into(),
    ));
    checks
}

fn run_doctor(flags: Flags) -> i32 {
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
        return 0;
    }
    for (name, ok, message) in &checks {
        println!("  [{}] {name}: {message}", if *ok { "OK  " } else { "FAIL" });
    }
    if all_ok {
        println!("\nAll checks passed.");
    } else {
        println!("\nSome checks failed. Run 'coordinationhub init' to fix setup issues.");
    }
    0
}

fn run_hook(flags: Flags) -> i32 {
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
