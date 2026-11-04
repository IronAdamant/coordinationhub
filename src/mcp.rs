//! MCP stdio transport. Framing is `Content-Length` then a JSON-RPC body.
//! No third-party MCP crate: the messages are the same shape clients already send.

use crate::call::CallError;
use crate::hub::Hub;
use crate::json::{self, Json};
use std::io::{self, Read, Write};

pub fn serve_stdio(hub: &Hub) -> io::Result<()> {
    let stdin = io::stdin();
    let mut reader = stdin.lock();
    let mut stdout = io::stdout().lock();
    loop {
        let Some(body) = read_frame(&mut reader)? else {
            break;
        };
        let Ok(msg) = json::parse(&body) else {
            continue;
        };
        if msg.get("id").is_none() {
            continue;
        }
        let id = msg.get("id").cloned().unwrap_or(Json::Null);
        let method = msg.get("method").and_then(|v| v.as_str()).unwrap_or("");
        let params = msg.get("params").cloned().unwrap_or(Json::object(vec![]));
        let result = match method {
            "initialize" => Json::object(vec![
                ("protocolVersion", Json::String("2024-11-05".into())),
                ("capabilities", Json::object(vec![("tools", Json::object(vec![]))])),
                (
                    "serverInfo",
                    Json::object(vec![
                        ("name", Json::String("coordinationhub".into())),
                        ("version", Json::String(crate::VERSION.into())),
                    ]),
                ),
            ]),
            "tools/list" => {
                let listed = crate::call::tool_list();
                let tools = listed.get("tools").cloned().unwrap_or(Json::Array(vec![]));
                let mapped = match tools {
                    Json::Array(items) => Json::Array(
                        items
                            .into_iter()
                            .map(|t| {
                                let name = t.get("name").cloned().unwrap_or(Json::String(String::new()));
                                let description = t.get("description").cloned().unwrap_or(Json::String(String::new()));
                                let schema = t.get("parameters").cloned().unwrap_or(Json::object(vec![]));
                                Json::object(vec![
                                    ("name", name),
                                    ("description", description),
                                    ("inputSchema", schema),
                                ])
                            })
                            .collect(),
                    ),
                    other => other,
                };
                Json::object(vec![("tools", mapped)])
            }
            "tools/call" => {
                let name = params.get("name").and_then(|v| v.as_str()).unwrap_or("");
                let arguments = params
                    .get("arguments")
                    .cloned()
                    .unwrap_or(Json::object(vec![]));
                match hub.call(name, &arguments) {
                    Ok(value) => Json::object(vec![
                        (
                            "content",
                            Json::Array(vec![Json::object(vec![
                                ("type", Json::String("text".into())),
                                ("text", Json::String(value.dump_pretty())),
                            ])]),
                        ),
                        ("isError", Json::Bool(false)),
                    ]),
                    Err(err) => {
                        let text = match err {
                            CallError::Value(m) | CallError::Type(m) => m,
                        };
                        Json::object(vec![
                            (
                                "content",
                                Json::Array(vec![Json::object(vec![
                                    ("type", Json::String("text".into())),
                                    ("text", Json::String(format!("Error: {text}"))),
                                ])]),
                            ),
                            ("isError", Json::Bool(true)),
                        ])
                    }
                }
            }
            "ping" => Json::object(vec![]),
            _ => {
                write_frame(
                    &mut stdout,
                    &Json::object(vec![
                        ("jsonrpc", Json::String("2.0".into())),
                        ("id", id),
                        (
                            "error",
                            Json::object(vec![
                                ("code", Json::Int(-32601)),
                                ("message", Json::String(format!("Method not found: {method}"))),
                            ]),
                        ),
                    ]),
                )?;
                continue;
            }
        };
        write_frame(
            &mut stdout,
            &Json::object(vec![
                ("jsonrpc", Json::String("2.0".into())),
                ("id", id),
                ("result", result),
            ]),
        )?;
    }
    Ok(())
}

fn read_frame(reader: &mut impl Read) -> io::Result<Option<String>> {
    let mut header = Vec::new();
    let mut byte = [0u8; 1];
    loop {
        if reader.read(&mut byte)? == 0 {
            return Ok(None);
        }
        header.push(byte[0]);
        if header.ends_with(b"\r\n\r\n") {
            break;
        }
        if header.len() > 8192 {
            return Ok(None);
        }
    }
    let text = String::from_utf8_lossy(&header);
    let mut len = 0usize;
    for line in text.split("\r\n") {
        let lower = line.to_ascii_lowercase();
        if let Some(rest) = lower.strip_prefix("content-length:") {
            len = rest.trim().parse().unwrap_or(0);
        }
    }
    let mut body = vec![0u8; len];
    reader.read_exact(&mut body)?;
    Ok(Some(String::from_utf8_lossy(&body).to_string()))
}

fn write_frame(out: &mut impl Write, msg: &Json) -> io::Result<()> {
    let body = msg.dump();
    write!(out, "Content-Length: {}\r\n\r\n{}", body.len(), body)?;
    out.flush()
}
