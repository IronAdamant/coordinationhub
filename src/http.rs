//! Small HTTP server for the dashboard and POST /call.

use crate::engine::Engine;
use crate::json::{self, Value};
use crate::mcp;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::PathBuf;

const DASHBOARD: &str = include_str!("../assets/dashboard.html");
const MAX_BODY: usize = 1_000_000;

pub fn serve(
    storage: Option<PathBuf>,
    project: Option<PathBuf>,
    namespace: &str,
    host: &str,
    port: u16,
    auth_token: Option<String>,
    no_auth: bool,
) -> Result<(), String> {
    let engine = Engine::open(storage.as_deref(), project.as_deref(), namespace)?;
    let token = if no_auth {
        None
    } else {
        Some(auth_token.unwrap_or_else(random_token))
    };
    let listener = TcpListener::bind((host, port)).map_err(|e| e.to_string())?;
    let bound = listener.local_addr().map_err(|e| e.to_string())?;
    println!("Starting CoordinationHub HTTP server on http://{bound}");
    if let Some(token) = &token {
        println!("  Auth token: {token}");
        println!("  Use: Authorization: Bearer {token}");
    } else {
        println!("  Auth: DISABLED (local-trust mode)");
    }
    let engine = std::sync::Mutex::new(engine);
    for stream in listener.incoming() {
        let Ok(stream) = stream else { continue };
        let token = token.clone();
        let mut engine = engine.lock().map_err(|e| e.to_string())?;
        let _ = handle(stream, &mut engine, token.as_deref());
    }
    Ok(())
}

fn handle(mut stream: TcpStream, engine: &mut Engine, token: Option<&str>) -> Result<(), String> {
    let mut buf = vec![0_u8; 64 * 1024];
    let n = stream.read(&mut buf).map_err(|e| e.to_string())?;
    if n == 0 {
        return Ok(());
    }
    let text = String::from_utf8_lossy(&buf[..n]);
    let mut lines = text.split("\r\n");
    let request = lines.next().unwrap_or("");
    let mut parts = request.split_whitespace();
    let method = parts.next().unwrap_or("");
    let path = parts.next().unwrap_or("/");
    let path = path.split('?').next().unwrap_or(path);
    let mut headers = Vec::new();
    for line in lines.by_ref() {
        if line.is_empty() {
            break;
        }
        if let Some((name, value)) = line.split_once(':') {
            headers.push((name.trim().to_ascii_lowercase(), value.trim().to_string()));
        }
    }
    let header = |name: &str| {
        headers
            .iter()
            .find(|(k, _)| k == name)
            .map(|(_, v)| v.as_str())
    };
    let open = path == "/" || path == "/health";
    if let Some(expected) = token {
        if !open {
            let ok = header("authorization")
                .map(|value| {
                    value
                        .strip_prefix("Bearer ")
                        .map(|t| t.trim() == expected)
                        .unwrap_or(false)
                })
                .unwrap_or(false);
            if !ok {
                return write_raw(
                    &mut stream,
                    401,
                    "application/json",
                    b"{\"error\": \"unauthorized\"}",
                    Some("Bearer realm=\"coordinationhub\""),
                );
            }
        }
    }
    if method == "GET" && path == "/" {
        let meta = format!(
            "<meta name=\"coordhub-token\" content=\"{}\">",
            token.unwrap_or("")
        );
        let html = if let Some(rest) = DASHBOARD.split_once("<head>") {
            format!("<head>\n{meta}{}", rest.1)
        } else {
            format!("{meta}{DASHBOARD}")
        };
        return write_html(&mut stream, html.as_bytes());
    }
    if method == "GET" && path == "/health" {
        let body = json::dumps(&json::obj(vec![
            ("status", json::s("ok")),
            ("tools_version", json::s(&engine.schema.tools_version)),
        ]));
        return write_json(&mut stream, 200, body.as_bytes());
    }
    if method == "GET" && path == "/tools" {
        let body = json::dumps(&mcp::schemas_for_http(engine));
        return write_json(&mut stream, 200, body.as_bytes());
    }
    if method == "GET" && path == "/api/dashboard-data" {
        let body = json::dumps(&engine.dashboard());
        return write_json(&mut stream, 200, body.as_bytes());
    }
    if method == "GET" && path == "/events" {
        let payload = json::dumps(&engine.dashboard());
        let event = format!("event: dashboard\ndata: {payload}\n\n");
        let header = format!(
            "HTTP/1.0 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\nConnection: close\r\nContent-Length: {}\r\n\r\n",
            event.len()
        );
        stream.write_all(header.as_bytes()).ok();
        stream.write_all(event.as_bytes()).ok();
        return Ok(());
    }
    if method == "POST" && path == "/call" {
        let len = header("content-length")
            .and_then(|v| v.parse::<usize>().ok())
            .unwrap_or(0);
        if len > MAX_BODY {
            let body = format!("{{\"error\": \"Request body exceeds {MAX_BODY} bytes\"}}");
            return write_json(&mut stream, 413, body.as_bytes());
        }
        let already = text.split("\r\n\r\n").nth(1).unwrap_or("").as_bytes();
        let mut body = already.to_vec();
        while body.len() < len && body.len() < MAX_BODY {
            let mut extra = [0_u8; 8192];
            let got = stream.read(&mut extra).unwrap_or(0);
            if got == 0 {
                break;
            }
            body.extend_from_slice(&extra[..got]);
        }
        body.truncate(len);
        let parsed = match std::str::from_utf8(&body)
            .ok()
            .and_then(|text| json::parse(text).ok())
        {
            Some(Value::Object(_)) => {
                json::parse(std::str::from_utf8(&body).unwrap_or("")).unwrap_or(Value::Null)
            }
            _ => {
                return write_json(&mut stream, 400, b"{\"error\": \"Invalid JSON\"}");
            }
        };
        let tool = parsed.get("tool").and_then(Value::as_str).unwrap_or("");
        if tool.is_empty() {
            return write_json(
                &mut stream,
                400,
                b"{\"error\": \"Missing 'tool' field in request body\"}",
            );
        }
        let arguments = parsed
            .get("arguments")
            .cloned()
            .unwrap_or(Value::Object(vec![]));
        if !matches!(arguments, Value::Object(_)) {
            return write_json(
                &mut stream,
                400,
                b"{\"error\": \"'arguments' must be a JSON object\"}",
            );
        }
        match engine.call(tool, &arguments) {
            Ok(value) => {
                let body = json::dumps(&json::obj(vec![("result", value)]));
                write_json(&mut stream, 200, body.as_bytes())
            }
            Err(err) if err.starts_with("Unknown tool:") || err.contains(": ") => {
                let status = if err.starts_with("Unknown tool:") {
                    404
                } else {
                    400
                };
                let body = json::dumps(&json::obj(vec![("error", json::s(err))]));
                write_json(&mut stream, status, body.as_bytes())
            }
            Err(err) => {
                let body = json::dumps(&json::obj(vec![("error", json::s(err))]));
                write_json(&mut stream, 400, body.as_bytes())
            }
        }
    } else {
        let body = json::dumps(&json::obj(vec![(
            "error",
            json::s(format!("Unknown endpoint: {path}")),
        )]));
        write_json(&mut stream, 404, body.as_bytes())
    }
}

fn write_html(stream: &mut TcpStream, body: &[u8]) -> Result<(), String> {
    let header = format!(
        "HTTP/1.0 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nX-Content-Type-Options: nosniff\r\nContent-Length: {}\r\n\r\n",
        body.len()
    );
    stream.write_all(header.as_bytes()).ok();
    stream.write_all(body).ok();
    Ok(())
}

fn write_json(stream: &mut TcpStream, status: u16, body: &[u8]) -> Result<(), String> {
    write_raw(stream, status, "application/json", body, None)
}

fn write_raw(
    stream: &mut TcpStream,
    status: u16,
    kind: &str,
    body: &[u8],
    www: Option<&str>,
) -> Result<(), String> {
    let reason = match status {
        200 => "OK",
        400 => "Bad Request",
        401 => "Unauthorized",
        404 => "Not Found",
        413 => "Payload Too Large",
        _ => "Error",
    };
    let extra = www
        .map(|v| format!("WWW-Authenticate: {v}\r\n"))
        .unwrap_or_default();
    let header = format!(
        "HTTP/1.0 {status} {reason}\r\nContent-Type: {kind}\r\n{extra}Content-Length: {}\r\n\r\n",
        body.len()
    );
    stream.write_all(header.as_bytes()).ok();
    stream.write_all(body).ok();
    Ok(())
}

fn random_token() -> String {
    let mut bytes = [0_u8; 16];
    if let Ok(mut file) = std::fs::File::open("/dev/urandom") {
        use std::io::Read;
        let _ = file.read_exact(&mut bytes);
    } else {
        let ticks = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(1);
        bytes = (ticks as u128).to_le_bytes();
    }
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}
