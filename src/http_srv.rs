//! Small HTTP/1.1 server: dashboard, tool call, and a short SSE snapshot.

use crate::call::CallError;
use crate::call_vis::dashboard;
use crate::hash::sha256_hex;
use crate::hub::Hub;
use crate::json::{self, Json};
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

const DASHBOARD: &str = include_str!("../assets/dashboard.html");

pub struct HttpOpts {
    pub host: String,
    pub port: u16,
    pub auth_token: Option<String>,
}

pub fn serve(hub: Arc<Hub>, opts: HttpOpts) -> std::io::Result<()> {
    let token = match &opts.auth_token {
        Some(t) if t == "disabled" => None,
        Some(t) => Some(t.clone()),
        None => Some(make_token()),
    };
    let listener = TcpListener::bind(format!("{}:{}", opts.host, opts.port))?;
    let addr = listener.local_addr()?;
    println!("Starting CoordinationHub HTTP server on http://{addr}");
    if let Some(t) = &token {
        println!("  Auth token: {t}");
        println!("  Use: Authorization: Bearer {t}");
    } else {
        println!("  Auth: DISABLED (local-trust mode)");
    }
    for conn in listener.incoming() {
        let Ok(stream) = conn else { continue };
        let hub = Arc::clone(&hub);
        let token = token.clone();
        std::thread::spawn(move || {
            let _ = handle(stream, &hub, token.as_deref());
        });
    }
    Ok(())
}

fn make_token() -> String {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    sha256_hex(format!("{}-{now}", std::process::id()).as_bytes())[..32].to_string()
}

fn handle(mut stream: TcpStream, hub: &Hub, token: Option<&str>) -> std::io::Result<()> {
    let mut buf = vec![0u8; 64 * 1024];
    let n = stream.read(&mut buf)?;
    if n == 0 {
        return Ok(());
    }
    let text = String::from_utf8_lossy(&buf[..n]);
    let mut lines = text.split("\r\n");
    let request = lines.next().unwrap_or("");
    let mut parts = request.split_whitespace();
    let method = parts.next().unwrap_or("");
    let path = parts.next().unwrap_or("/").split('?').next().unwrap_or("/");
    let mut auth = String::new();
    let mut content_length = 0usize;
    for line in lines.by_ref() {
        if line.is_empty() {
            break;
        }
        let lower = line.to_ascii_lowercase();
        if let Some(rest) = lower.strip_prefix("authorization:") {
            auth = line[line.len() - rest.len()..].trim().to_string();
        }
        if let Some(rest) = lower.strip_prefix("content-length:") {
            content_length = rest.trim().parse().unwrap_or(0);
        }
    }
    let header_end = text.find("\r\n\r\n").map(|i| i + 4).unwrap_or(text.len());
    let mut body = text[header_end.min(text.len())..].as_bytes().to_vec();
    while body.len() < content_length && body.len() < 1_000_000 {
        let mut extra = [0u8; 8192];
        let k = stream.read(&mut extra)?;
        if k == 0 {
            break;
        }
        body.extend_from_slice(&extra[..k]);
    }
    body.truncate(content_length);
    let open = path == "/" || path == "/health";
    if !open {
        if let Some(expected) = token {
            if auth != format!("Bearer {expected}") {
                return write_json(&mut stream, 401, &Json::object(vec![("error", Json::String("unauthorized".into()))]));
            }
        }
    }
    match (method, path) {
        ("GET", "/") => {
            let page = if let Some(t) = token {
                DASHBOARD.replacen("<head>", &format!("<head>\n<meta name=\"coordhub-token\" content=\"{t}\">"), 1)
            } else {
                DASHBOARD.to_string()
            };
            write_bytes(&mut stream, 200, "text/html; charset=utf-8", page.as_bytes())
        }
        ("GET", "/health") => write_json(
            &mut stream,
            200,
            &Json::object(vec![
                ("status", Json::String("ok".into())),
                ("tools_version", Json::String(crate::validate::tools_version())),
            ]),
        ),
        ("GET", "/tools") => write_json(&mut stream, 200, &crate::call::tool_list()),
        ("GET", "/api/dashboard-data") => {
            let data = hub.transact(|st| dashboard(st));
            write_json(&mut stream, 200, &data)
        }
        ("GET", "/events") => {
            let data = hub.transact(|st| dashboard(st));
            let payload = data.dump();
            let frame = format!("id: 1\ndata: {payload}\n\n");
            let head = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\nConnection: close\r\nContent-Length: {}\r\n\r\n",
                frame.len()
            );
            stream.write_all(head.as_bytes())?;
            stream.write_all(frame.as_bytes())?;
            Ok(())
        }
        ("POST", "/call") => {
            let parsed = json::parse(&String::from_utf8_lossy(&body));
            let Ok(body) = parsed else {
                return write_json(&mut stream, 400, &Json::object(vec![("error", Json::String("Invalid JSON".into()))]));
            };
            let Some(tool) = body.get("tool").and_then(|v| v.as_str()) else {
                return write_json(&mut stream, 400, &Json::object(vec![("error", Json::String("Missing 'tool' field in request body".into()))]));
            };
            let args = body.get("arguments").cloned().unwrap_or(Json::object(vec![]));
            if !matches!(args, Json::Object(_)) {
                return write_json(&mut stream, 400, &Json::object(vec![("error", Json::String("'arguments' must be a JSON object".into()))]));
            }
            match hub.call(tool, &args) {
                Ok(result) => write_json(&mut stream, 200, &Json::object(vec![("result", result)])),
                Err(CallError::Value(msg)) => write_json(&mut stream, 404, &Json::object(vec![("error", Json::String(msg))])),
                Err(CallError::Type(msg)) => write_json(&mut stream, 400, &Json::object(vec![("error", Json::String(msg))])),
            }
        }
        _ => write_json(
            &mut stream,
            404,
            &Json::object(vec![(
                "error",
                Json::String(format!("Unknown endpoint: {path}")),
            )]),
        ),
    }
}

fn write_json(stream: &mut TcpStream, status: u16, body: &Json) -> std::io::Result<()> {
    write_bytes(stream, status, "application/json", body.dump().as_bytes())
}

fn write_bytes(stream: &mut TcpStream, status: u16, ctype: &str, body: &[u8]) -> std::io::Result<()> {
    let reason = match status {
        200 => "OK",
        400 => "Bad Request",
        401 => "Unauthorized",
        404 => "Not Found",
        _ => "Error",
    };
    let head = format!(
        "HTTP/1.1 {status} {reason}\r\nContent-Type: {ctype}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        body.len()
    );
    stream.write_all(head.as_bytes())?;
    stream.write_all(body)?;
    Ok(())
}
