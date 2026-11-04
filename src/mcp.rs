//! Newline JSON-RPC MCP server. Also accepts a Content-Length header frame.

use crate::engine::Engine;
use crate::json::{self, Value};
use crate::validate;
use std::io::{self, BufRead, Write};
use std::path::PathBuf;

pub fn serve(
    storage: Option<PathBuf>,
    project: Option<PathBuf>,
    namespace: &str,
) -> Result<(), String> {
    let engine = std::sync::Arc::new(std::sync::Mutex::new(Engine::open(
        storage.as_deref(),
        project.as_deref(),
        namespace,
    )?));
    crate::house::spawn(engine.clone());
    let stdin = io::stdin();
    let mut reader = stdin.lock();
    let mut stdout = io::stdout();
    loop {
        let message = match read_frame(&mut reader) {
            Ok(Some(text)) => text,
            Ok(None) => break,
            Err(err) => return Err(err),
        };
        if message.trim().is_empty() {
            continue;
        }
        let parsed = match json::parse(&message) {
            Ok(value) => value,
            Err(err) => {
                let _ = write_frame(&mut stdout, &error_response(Value::Null, -32700, &err));
                continue;
            }
        };
        let id = parsed.get("id").cloned().unwrap_or(Value::Null);
        let method = parsed.get("method").and_then(Value::as_str).unwrap_or("");
        let is_note = parsed.get("id").is_none();
        let response = match method {
            "initialize" => Some(ok(id, initialize())),
            "notifications/initialized" | "initialized" => None,
            "tools/list" => {
                let engine = engine.lock().map_err(|err| err.to_string())?;
                Some(ok(id, tools_list(&engine)))
            }
            "tools/call" => {
                let params = parsed
                    .get("params")
                    .cloned()
                    .unwrap_or(Value::Object(vec![]));
                let name = params.get("name").and_then(Value::as_str).unwrap_or("");
                let arguments = params
                    .get("arguments")
                    .cloned()
                    .unwrap_or(Value::Object(vec![]));
                let mut engine = engine.lock().map_err(|err| err.to_string())?;
                Some(ok(id, call_result(&mut engine, name, &arguments)))
            }
            "ping" => Some(ok(id, Value::Object(vec![]))),
            _ if is_note => None,
            _ => Some(error_response(id, -32601, "method not found")),
        };
        if let Some(response) = response {
            write_frame(&mut stdout, &response)?;
        }
    }
    Ok(())
}

fn initialize() -> Value {
    json::obj(vec![
        ("protocolVersion", json::s("2024-11-05")),
        (
            "capabilities",
            json::obj(vec![(
                "tools",
                json::obj(vec![("listChanged", json::b(false))]),
            )]),
        ),
        (
            "serverInfo",
            json::obj(vec![
                ("name", json::s("coordinationhub")),
                ("version", json::s(crate::VERSION)),
            ]),
        ),
    ])
}

pub fn tools_list(engine: &Engine) -> Value {
    let mut tools = Vec::new();
    for (name, schema) in &engine.schema.tools {
        tools.push(tool_entry(name, schema));
    }
    tools.push(who_tool());
    json::obj(vec![("tools", json::arr(tools))])
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
        ("description", json::s("Compact coordination picture, plus claim, release, and handoff of a work area. One call shows who owns which task, file, or area.")),
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

fn call_result(engine: &mut Engine, name: &str, arguments: &Value) -> Value {
    match engine.call(name, arguments) {
        Ok(value) => content(&json::dumps_pretty(&value), false),
        Err(err) => {
            let text = if err.starts_with("Input validation error:") {
                err
            } else {
                format!("Error: {err}")
            };
            content(&text, true)
        }
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
