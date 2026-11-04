//! Command line. Flags follow the Python CLI: they belong after the command.

use crate::engine::Engine;
use crate::http;
use crate::json::{self, Value};
use crate::mcp;
use std::path::PathBuf;

pub fn main(argv: &[String]) -> i32 {
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
        _ => run_tool_command(command, rest),
    }
}

fn print_help() {
    println!("CoordinationHub — live coordination and work ownership for coding agents");
    println!();
    println!("Usage: coordinationhub <command> [flags]");
    println!();
    println!("Commands:");
    for name in COMMANDS {
        println!("  {name}");
    }
    println!();
    println!("Shared flags (after the command):");
    println!("  --storage-dir PATH");
    println!("  --project-root PATH");
    println!("  --namespace NAME");
    println!("  -j, --json");
}

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
    "contention-hotspots",
    "load-spec",
    "scan-project",
    "dashboard",
    "agent-status",
    "agent-tree",
    "assess",
    "doctor",
    "init",
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
    "declare-work-intent",
    "get-work-intents",
    "clear-work-intent",
    "declare-dependency",
    "manage-dependencies",
    "satisfy-dependency",
    "acquire-coordinator-lease",
    "refresh-coordinator-lease",
    "release-coordinator-lease",
    "get-leader",
    "claim-leadership",
    "spawn-subagent",
    "report-subagent-spawned",
    "list-pending-spawns",
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

fn parse_flags(args: &[String]) -> Flags {
    let mut flags = Flags {
        storage: None,
        project: None,
        namespace: std::env::var("COORDINATIONHUB_NAMESPACE").unwrap_or_else(|_| "hub".into()),
        json: false,
        positionals: Vec::new(),
        map: Vec::new(),
        bools: Vec::new(),
    };
    let mut index = 0;
    while index < args.len() {
        let arg = &args[index];
        if arg == "--json" || arg == "-j" {
            flags.json = true;
        } else if let Some(rest) = arg.strip_prefix("--storage-dir=") {
            flags.storage = Some(PathBuf::from(rest));
        } else if arg == "--storage-dir" {
            index += 1;
            flags.storage = args.get(index).map(PathBuf::from);
        } else if let Some(rest) = arg.strip_prefix("--project-root=") {
            flags.project = Some(PathBuf::from(rest));
        } else if arg == "--project-root" {
            index += 1;
            flags.project = args.get(index).map(PathBuf::from);
        } else if arg == "--namespace" {
            index += 1;
            if let Some(value) = args.get(index) {
                flags.namespace = value.clone();
            }
        } else if arg.starts_with("--")
            && index + 1 < args.len()
            && !args[index + 1].starts_with('-')
        {
            let key = arg.trim_start_matches('-').replace('-', "_");
            flags.map.push((key, args[index + 1].clone()));
            index += 1;
        } else if arg.starts_with("--") {
            flags
                .bools
                .push(arg.trim_start_matches('-').replace('-', "_"));
        } else {
            flags.positionals.push(arg.clone());
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

fn run_serve_mcp(args: &[String]) -> i32 {
    let flags = parse_flags(args);
    match mcp::serve(flags.storage, flags.project, &flags.namespace) {
        Ok(()) => 0,
        Err(err) => {
            eprintln!("Error: {err}");
            1
        }
    }
}

fn run_serve(command: &str, args: &[String]) -> i32 {
    let flags = parse_flags(args);
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
    let flags = parse_flags(args);
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
    let flags = parse_flags(args);
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
    let (tool, payload, text) = built;
    if flags.json || text.is_none() {
        return emit_tool(&flags, &tool, &payload);
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
    match engine.call(&tool, &payload) {
        Ok(value) => {
            if command == "heartbeat" {
                let id = flags.positionals.first().map(|s| s.as_str()).unwrap_or("");
                let updated = value.get("updated").and_then(Value::as_bool).unwrap_or(false);
                println!("Heartbeat: {id} — updated: {}", py_bool(updated));
            } else if command == "deregister" {
                let id = flags.positionals.first().map(|s| s.as_str()).unwrap_or("");
                println!("Deregistered: {id}");
                render_deregister(&value);
            } else if command == "agent-tree" {
                if let Some(text) = value.get("text_tree").and_then(Value::as_str) {
                    println!("{text}");
                }
            } else if let Some(render) = text {
                render(&value);
            }
            0
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
            json::obj(vec![(
                "limit",
                json::i(
                    flag(flags, "limit")
                        .and_then(|v| v.parse().ok())
                        .unwrap_or(100),
                ),
            )]),
            None,
        ),
        "get-conflicts" => ("get_conflicts", json::obj(vec![]), None),
        "send-message" => (
            "send_message",
            json::obj(vec![
                (
                    "from_agent_id",
                    json::s(pos.first().ok_or("from required")?),
                ),
                ("to_agent_id", json::s(pos.get(1).ok_or("to required")?)),
                ("message_type", json::s(pos.get(2).ok_or("type required")?)),
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
            ]),
            None,
        ),
        "who" => (
            "who",
            json::obj(vec![
                ("action", json::s(flag(flags, "action").unwrap_or("status"))),
                (
                    "agent_id",
                    json::opt_str(
                        flag(flags, "agent_id").or_else(|| pos.first().map(|s| s.as_str())),
                    ),
                ),
                (
                    "area",
                    json::opt_str(flag(flags, "area").or_else(|| pos.get(1).map(|s| s.as_str()))),
                ),
                ("to_agent_id", json::opt_str(flag(flags, "to_agent_id"))),
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
    let flags = parse_flags(args);
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
    let body = format!("{{\n  \"hooks\": {{\n    \"SessionStart\": [{{\"hooks\": [{{\"command\": \"{exe} hook\"}}]}}]\n  }}\n}}\n");
    if let Err(err) = std::fs::write(&hooks, body) {
        eprintln!("Error: {err}");
        return 1;
    }
    println!("Hooks written to {}", hooks.display());
    println!("\nSetup complete. CoordinationHub is ready.");
    0
}

fn run_doctor(args: &[String]) -> i32 {
    let flags = parse_flags(args);
    let root = flags.project.clone().or_else(|| {
        crate::util::detect_project_root(
            &std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")),
        )
    });
    let storage = crate::db::resolve_storage(flags.storage.as_deref(), root.as_deref());
    let db = storage.join("coordination.jsonl");
    let mut failed = false;
    let mark = |ok: bool| {
        if ok {
            "OK  "
        } else {
            "FAIL"
        }
    };
    println!("  [{}] binary: coordinationhub {}", mark(true), crate::VERSION);
    let home = std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .unwrap_or_else(|_| ".".into());
    let hooks = PathBuf::from(&home).join(".coordinationhub").join("hooks.json");
    let hooks_text = std::fs::read_to_string(&hooks).unwrap_or_default();
    let hooks_ok = ["SessionStart", "PreToolUse", "PostToolUse", "SubagentStart", "SubagentStop", "SessionEnd"]
        .iter()
        .all(|name| hooks_text.contains(name))
        && hooks_text.contains("coordinationhub");
    if !hooks_ok {
        failed = true;
    }
    println!("  [{}] hooks: {}", mark(hooks_ok), hooks.display());
    let dir_ok = storage.is_dir();
    if !dir_ok {
        failed = true;
    }
    println!("  [{}] storage dir: {}", mark(dir_ok), storage.display());
    let db_ok = db.is_file();
    if !db_ok {
        failed = true;
    }
    println!("  [{}] journal: {}", mark(db_ok), db.display());
    let readable = std::fs::read_to_string(&db)
        .map(|text| text.lines().all(|line| line.trim().is_empty() || json::parse(line).is_ok()))
        .unwrap_or(false);
    if db_ok && !readable {
        failed = true;
    }
    println!(
        "  [{}] journal lines: {}",
        mark(!db_ok || readable),
        if readable { "readable" } else { "unreadable" }
    );
    if failed { 1 } else { 0 }
}

fn run_hook(args: &[String]) -> i32 {
    let flags = parse_flags(args);
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
    let flags = parse_flags(args);
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
