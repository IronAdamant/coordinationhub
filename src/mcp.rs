//! Newline JSON-RPC MCP server. Also accepts a Content-Length header frame.

use crate::engine::Engine;
use crate::groups::{EnvSwitches, Surface};
use crate::json::{self, Value};
use crate::validate;
use crate::waits::Waits;
use std::io::{self, BufRead, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

/// The project's AGENTS.md, watched by size and modification time so an
/// agent can change the `agent-tools` block mid-session.
struct AgentsFile {
    path: PathBuf,
    stamp: Option<(std::time::SystemTime, u64)>,
}

impl AgentsFile {
    fn new(root: &Path) -> AgentsFile {
        let path = root.join("AGENTS.md");
        let stamp = stamp(&path);
        AgentsFile { path, stamp }
    }

    fn text(&self) -> Option<String> {
        std::fs::read_to_string(&self.path).ok()
    }

    /// True when the file appeared, vanished, or changed size or time.
    fn changed(&mut self) -> bool {
        let now = stamp(&self.path);
        if now == self.stamp {
            return false;
        }
        self.stamp = now;
        true
    }
}

fn stamp(path: &Path) -> Option<(std::time::SystemTime, u64)> {
    let meta = std::fs::metadata(path).ok()?;
    Some((meta.modified().ok()?, meta.len()))
}

pub fn serve(
    storage: Option<PathBuf>,
    project: Option<PathBuf>,
    namespace: &str,
    switches: EnvSwitches,
) -> Result<(), String> {
    let opened = Engine::open(storage.as_deref(), project.as_deref(), namespace)?;
    let root = opened
        .project_root
        .clone()
        .or_else(|| std::env::current_dir().ok())
        .unwrap_or_else(|| PathBuf::from("."));
    let mut agents = AgentsFile::new(&root);
    let mut surface = Surface::resolve(&switches, agents.text().as_deref());
    let mut initialized = false;
    let waits = Arc::new(Waits::new(storage, project, namespace));
    let engine = Arc::new(Mutex::new(opened));
    crate::house::spawn(engine.clone());
    let stdin = io::stdin();
    let mut reader = stdin.lock();
    let out: Shared = Arc::new(Mutex::new(io::stdout()));
    // Waits in flight, by request id (as JSON text), with their cancel flags.
    let pending: Pending = Arc::new(Mutex::new(Vec::new()));
    let mut threads: Vec<std::thread::JoinHandle<()>> = Vec::new();
    loop {
        let message = match read_frame(&mut reader) {
            Ok(Some(text)) => text,
            Ok(None) => break,
            Err(err) => return Err(err),
        };
        threads.retain(|thread| !thread.is_finished());
        if message.trim().is_empty() {
            continue;
        }
        let parsed = match json::parse(&message) {
            Ok(value) => value,
            Err(err) => {
                let _ = send(&out, &error_response(Value::Null, -32700, &err));
                continue;
            }
        };
        let id = parsed.get("id").cloned().unwrap_or(Value::Null);
        let method = parsed.get("method").and_then(Value::as_str).unwrap_or("");
        let is_note = parsed.get("id").is_none();
        // Pick up an edited agent-tools block before answering; tell the
        // client to refetch the list when the exposed tools changed.
        if initialized && agents.changed() {
            let next = Surface::resolve(&switches, agents.text().as_deref());
            let moved = next.exposed_names() != surface.exposed_names();
            surface = next;
            if moved {
                send(
                    &out,
                    &json::obj(vec![
                        ("jsonrpc", json::s("2.0")),
                        ("method", json::s("notifications/tools/list_changed")),
                    ]),
                )?;
            }
        }
        if method == "initialize" {
            initialized = true;
        }
        let response = match method {
            "initialize" => Some(ok(id, initialize(&surface))),
            "notifications/initialized" | "initialized" => None,
            "notifications/cancelled" => {
                let target = parsed
                    .get("params")
                    .and_then(|params| params.get("requestId"))
                    .map(json::dumps);
                if let (Some(target), Ok(pending)) = (target, pending.lock()) {
                    for (key, flag) in pending.iter() {
                        if *key == target {
                            flag.store(true, Ordering::SeqCst);
                        }
                    }
                }
                None
            }
            "tools/list" => {
                let engine = engine.lock().map_err(|err| err.to_string())?;
                Some(ok(id, tools_list_for(&engine, &surface)))
            }
            "tools/call" => {
                let params = parsed
                    .get("params")
                    .cloned()
                    .unwrap_or(Value::Object(vec![]));
                let name = params.get("name").and_then(Value::as_str).unwrap_or("").to_string();
                let arguments = params
                    .get("arguments")
                    .cloned()
                    .unwrap_or(Value::Object(vec![]));
                match surface.refusal(&name) {
                    Some(refusal) => Some(ok(id, content(&refusal, true))),
                    None if crate::engine::blocks(&name, &arguments) => {
                        if waits.admit() {
                            threads.push(spawn_wait(&waits, &out, &pending, id, name, arguments));
                            None
                        } else {
                            Some(ok(id, content(&Waits::refusal(), true)))
                        }
                    }
                    None => {
                        let mut engine = engine.lock().map_err(|err| err.to_string())?;
                        let result = reply(engine.call(&name, &arguments));
                        Some(ok(id, if name == "status" && surface.configured() {
                            with_groups(result, &surface)
                        } else {
                            result
                        }))
                    }
                }
            }
            "ping" => Some(ok(id, Value::Object(vec![]))),
            _ if is_note => None,
            _ => Some(error_response(id, -32601, "method not found")),
        };
        if let Some(response) = response {
            send(&out, &response)?;
        }
    }
    // The client closed stdin: let the waits in flight answer (each ends by
    // its capped timeout) before the process exits.
    for thread in threads {
        let _ = thread.join();
    }
    Ok(())
}

type Shared = Arc<Mutex<io::Stdout>>;
type Pending = Arc<Mutex<Vec<(String, Arc<AtomicBool>)>>>;

/// Runs a waiting call on its own thread and engine, so the read loop keeps
/// answering other calls. A cancelled call gets no response, as MCP asks.
fn spawn_wait(
    waits: &Arc<Waits>,
    out: &Shared,
    pending: &Pending,
    id: Value,
    name: String,
    arguments: Value,
) -> std::thread::JoinHandle<()> {
    let flag = Arc::new(AtomicBool::new(false));
    let key = json::dumps(&id);
    if let Ok(mut pending) = pending.lock() {
        pending.push((key.clone(), flag.clone()));
    }
    let (waits, out, pending) = (waits.clone(), out.clone(), pending.clone());
    std::thread::spawn(move || {
        let result = reply(waits.run(&name, &arguments, flag.clone()));
        if let Ok(mut pending) = pending.lock() {
            pending.retain(|(item, held)| !(item == &key && Arc::ptr_eq(held, &flag)));
        }
        if !flag.load(Ordering::SeqCst) {
            let _ = send(&out, &ok(id, result));
        }
    })
}

fn send(out: &Shared, value: &Value) -> Result<(), String> {
    let mut stdout = out.lock().map_err(|err| err.to_string())?;
    write_frame(&mut *stdout, value)
}

fn initialize(surface: &Surface) -> Value {
    let mut result = json::obj(vec![
        ("protocolVersion", json::s("2024-11-05")),
        (
            "capabilities",
            json::obj(vec![(
                "tools",
                json::obj(vec![("listChanged", json::b(true))]),
            )]),
        ),
        (
            "serverInfo",
            json::obj(vec![
                ("name", json::s("coordinationhub")),
                ("version", json::s(crate::VERSION)),
            ]),
        ),
    ]);
    if surface.configured() {
        if let Value::Object(pairs) = &mut result {
            pairs.push(("instructions".into(), json::s(surface.instructions())));
        }
    }
    result
}

/// Every tool, as listed with neither group switch set.
pub fn tools_list(engine: &Engine) -> Value {
    tools_list_for(engine, &Surface::all())
}

/// The tools `surface` exposes, in the default order.
pub fn tools_list_for(engine: &Engine, surface: &Surface) -> Value {
    let mut tools = Vec::new();
    for (name, schema) in &engine.schema.tools {
        if surface.allows(name) {
            tools.push(tool_entry(name, schema));
        }
    }
    if surface.allows("who") {
        tools.push(who_tool());
    }
    json::obj(vec![("tools", json::arr(tools))])
}

/// `status` with the switches set: the exposed tool count and the active
/// groups on one line.
fn with_groups(result: Value, surface: &Surface) -> Value {
    let (text, is_error) = match (
        result.get("content").and_then(Value::as_array).and_then(|items| items.first()).and_then(|item| item.get("text")).and_then(Value::as_str),
        result.get("isError").and_then(Value::as_bool),
    ) {
        (Some(text), Some(false)) => (text.to_string(), false),
        _ => return result,
    };
    let Ok(Value::Object(mut pairs)) = json::parse(&text) else {
        return result;
    };
    for (key, value) in pairs.iter_mut() {
        if key == "tools" {
            *value = json::i(surface.exposed() as i64);
        }
    }
    pairs.push(("tool_groups".into(), json::s(surface.active_groups())));
    if !surface.warnings().is_empty() {
        pairs.push(("tool_groups_warning".into(), json::s(surface.warnings().join("\n"))));
    }
    content(&json::dumps_tight(&Value::Object(pairs)), is_error)
}

fn tool_entry(name: &str, schema: &Value) -> Value {
    json::obj(vec![
        ("name", json::s(name)),
        (
            "description",
            schema
                .get("description")
                .cloned()
                .unwrap_or_else(|| json::s("")),
        ),
        (
            "inputSchema",
            schema
                .get("parameters")
                .cloned()
                .unwrap_or_else(|| json::obj(vec![("type", json::s("object"))])),
        ),
    ])
}

fn who_tool() -> Value {
    json::obj(vec![
        ("name", json::s("who")),
        ("description", json::s("Start here when you join or pick new work: one compact call shows who owns which task, file lock and work area. claim reserves an area (a directory, feature or task id) so others keep out of it; release and handoff end or pass it on.")),
        ("inputSchema", json::obj(vec![
            ("type", json::s("object")),
            ("properties", json::obj(vec![
                ("action", json::obj(vec![("type", json::s("string")), ("enum", json::arr(vec![json::s("status"), json::s("claim"), json::s("release"), json::s("handoff")])), ("description", json::s("status (default), claim, release, or handoff"))])),
                ("agent_id", json::obj(vec![("type", json::s("string")), ("description", json::s("Agent acting or being described"))])),
                ("area", json::obj(vec![("type", json::s("string")), ("description", json::s("Task id, path, or area name"))])),
                ("to_agent_id", json::obj(vec![("type", json::s("string")), ("description", json::s("Recipient for handoff"))])),
                ("ttl", json::obj(vec![("type", json::s("number")), ("description", json::s("Claim lifetime in seconds. Heartbeats refresh it. Default 600."))])),
                ("note", json::obj(vec![("type", json::s("string")), ("description", json::s("Short note stored with the claim"))])),
            ])),
        ])),
    ])
}

fn reply(result: Result<Value, String>) -> Value {
    match result {
        // Compact JSON: agents parse it, and indentation only costs tokens.
        Ok(value) => content(&json::dumps_tight(&value), false),
        Err(err) if err.starts_with("Input validation error:") => content(&err, true),
        Err(err) => content(&format!("Error: {err}"), true),
    }
}

fn content(text: &str, is_error: bool) -> Value {
    json::obj(vec![
        (
            "content",
            json::arr(vec![json::obj(vec![
                ("type", json::s("text")),
                ("text", json::s(text)),
            ])]),
        ),
        ("isError", json::b(is_error)),
    ])
}

fn ok(id: Value, result: Value) -> Value {
    json::obj(vec![
        ("jsonrpc", json::s("2.0")),
        ("id", id),
        ("result", result),
    ])
}

fn error_response(id: Value, code: i64, message: &str) -> Value {
    json::obj(vec![
        ("jsonrpc", json::s("2.0")),
        ("id", id),
        (
            "error",
            json::obj(vec![("code", json::i(code)), ("message", json::s(message))]),
        ),
    ])
}

fn read_frame(reader: &mut impl BufRead) -> Result<Option<String>, String> {
    let mut header = String::new();
    if reader.read_line(&mut header).map_err(|e| e.to_string())? == 0 {
        return Ok(None);
    }
    if header.to_ascii_lowercase().starts_with("content-length:") {
        let len: usize = header
            .split(':')
            .nth(1)
            .unwrap_or("0")
            .trim()
            .parse()
            .map_err(|_| "bad content length".to_string())?;
        let mut blank = String::new();
        reader.read_line(&mut blank).map_err(|e| e.to_string())?;
        let mut buf = vec![0_u8; len];
        std::io::Read::read_exact(reader, &mut buf).map_err(|e| e.to_string())?;
        return Ok(Some(String::from_utf8_lossy(&buf).into_owned()));
    }
    Ok(Some(header))
}

fn write_frame(out: &mut impl Write, value: &Value) -> Result<(), String> {
    out.write_all(json::dumps(value).as_bytes())
        .map_err(|e| e.to_string())?;
    out.write_all(b"\n").map_err(|e| e.to_string())?;
    out.flush().map_err(|e| e.to_string())
}

pub fn schemas_for_http(engine: &Engine) -> Value {
    let tools: Vec<Value> = engine
        .schema
        .tools
        .iter()
        .map(|(_, schema)| schema.clone())
        .collect();
    json::obj(vec![
        ("tools", json::arr(tools)),
        ("tools_version", json::s(&engine.schema.tools_version)),
    ])
}

pub fn legacy_tool_names() -> Vec<String> {
    validate::load_schema()
        .tools
        .into_iter()
        .map(|(name, _)| name)
        .collect()
}
