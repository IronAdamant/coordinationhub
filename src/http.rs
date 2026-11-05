//! Small HTTP server for the dashboard and POST /call.

use crate::engine::Engine;
use crate::json::{self, Value};
use crate::mcp;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::PathBuf;
use std::collections::HashMap;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Duration;

fn sse_table() -> &'static Mutex<HashMap<String, i32>> {
    static TABLE: OnceLock<Mutex<HashMap<String, i32>>> = OnceLock::new();
    TABLE.get_or_init(|| Mutex::new(HashMap::new()))
}

fn sse_admit(ip: &str) -> bool {
    let Ok(mut table) = sse_table().lock() else {
        return false;
    };
    let count = table.entry(ip.to_string()).or_insert(0);
    if *count >= 4 {
        return false;
    }
    *count += 1;
    true
}

fn sse_release(ip: &str) {
    if let Ok(mut table) = sse_table().lock() {
        let count = table.entry(ip.to_string()).or_insert(0);
        *count -= 1;
        if *count <= 0 {
            table.remove(ip);
        }
    }
}

const DASHBOARD: &str = include_str!("../assets/dashboard.html");

/// Wait up to five seconds for `/health`, then hand the dashboard URL to
/// the desktop's opener. Failures are silent, as in 0.8.0.
fn open_dashboard(host: &str, port: u16) {
    use std::io::{Read, Write};
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
    while std::time::Instant::now() < deadline {
        let up = std::net::TcpStream::connect((host, port))
            .and_then(|mut stream| {
                stream.set_read_timeout(Some(std::time::Duration::from_millis(300)))?;
                write!(stream, "GET /health HTTP/1.0\r\nHost: {host}\r\n\r\n")?;
                let mut head = [0_u8; 12];
                stream.read_exact(&mut head)?;
                Ok(head.ends_with(b"200"))
            })
            .unwrap_or(false);
        if up {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(100));
    }
    let url = format!("http://{host}:{port}/");
    let mut command = if cfg!(windows) {
        let mut cmd = std::process::Command::new("cmd");
        cmd.args(["/C", "start", "", &url]);
        cmd
    } else if cfg!(target_os = "macos") {
        let mut cmd = std::process::Command::new("open");
        cmd.arg(&url);
        cmd
    } else {
        let mut cmd = std::process::Command::new("xdg-open");
        cmd.arg(&url);
        cmd
    };
    let _ = command
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn();
}
const MAX_BODY: usize = 1_000_000;

/// `serve` prints the API banner. `serve-sse` prints the dashboard banner
/// and, unless `--no-browser`, opens the dashboard once `/health` answers.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Banner {
    Api,
    Dashboard { open_browser: bool },
}

#[allow(clippy::too_many_arguments)]
pub fn serve(
    storage: Option<PathBuf>,
    project: Option<PathBuf>,
    namespace: &str,
    host: &str,
    port: u16,
    auth_token: Option<String>,
    no_auth: bool,
    banner: Banner,
) -> Result<(), String> {
    let engine = Engine::open(storage.as_deref(), project.as_deref(), namespace)?;
    let token = if no_auth {
        None
    } else {
        Some(auth_token.unwrap_or_else(random_token))
    };
    let listener = TcpListener::bind((host, port)).map_err(|e| e.to_string())?;
    let port = listener.local_addr().map(|addr| addr.port()).unwrap_or(port);
    match banner {
        Banner::Api => {
            println!("Starting CoordinationHub HTTP server on http://{host}:{port}");
            if let Some(token) = &token {
                println!("  Auth token: {token}");
                println!("  Use: Authorization: Bearer {token}");
            } else {
                println!("  Auth: DISABLED (local-trust mode)");
            }
        }
        Banner::Dashboard { open_browser } => {
            if let Some(token) = &token {
                println!("Auth token: {token}");
                println!("  Use: Authorization: Bearer {token}");
            } else {
                println!("Auth: DISABLED (local-trust mode)");
            }
            if open_browser {
                let host = host.to_string();
                std::thread::spawn(move || open_dashboard(&host, port));
            }
            println!("Starting SSE dashboard server on {host}:{port}");
            println!("Dashboard: http://{host}:{port}/");
            println!("SSE stream: http://{host}:{port}/events");
            println!("Press Ctrl+C to stop");
        }
    }
    let engine = Arc::new(Mutex::new(engine));
    crate::house::spawn(engine.clone());
    for stream in listener.incoming() {
        let Ok(stream) = stream else { continue };
        let token = token.clone();
        let engine = engine.clone();
        std::thread::spawn(move || {
            let _ = dispatch_connection(stream, engine, token);
        });
    }
    Ok(())
}

fn dispatch_connection(
    mut stream: TcpStream,
    engine: Arc<Mutex<Engine>>,
    token: Option<String>,
) -> Result<(), String> {
    let _ = stream.set_read_timeout(Some(Duration::from_secs(30)));
    let mut buf = vec![0_u8; 64 * 1024];
    let n = stream.read(&mut buf).map_err(|e| e.to_string())?;
    if n == 0 {
        return Ok(());
    }
    let text = String::from_utf8_lossy(&buf[..n]).to_string();
    let mut lines = text.split("\r\n");
    let request = lines.next().unwrap_or("");
    let mut parts = request.split_whitespace();
    let method = parts.next().unwrap_or("");
    let raw_path = parts.next().unwrap_or("/");
    let (path, query) = raw_path.split_once('?').unwrap_or((raw_path, ""));
    let mut headers = Vec::new();
    for line in lines.by_ref() {
        if line.is_empty() {
            break;
        }
        if let Some((name, value)) = line.split_once(':') {
            headers.push((name.trim().to_ascii_lowercase(), value.trim().to_string()));
        }
    }
    if method == "GET" && path == "/events" {
        if let Some(expected) = &token {
            let presented = headers.iter().find(|(name, _)| name == "authorization");
            let ok = presented
                .map(|(_, value)| {
                    value
                        .strip_prefix("Bearer ")
                        .map(|item| item.trim() == expected)
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
        let ip = stream
            .peer_addr()
            .map(|addr| addr.ip().to_string())
            .unwrap_or_else(|_| "unknown".into());
        if !sse_admit(&ip) {
            let body = b"{\"error\": \"too many SSE connections\"}";
            return write_raw(&mut stream, 429, "application/json", body, None);
        }
        let last = headers
            .iter()
            .find(|(name, _)| name == "last-event-id")
            .map(|(_, value)| value.clone())
            .or_else(|| {
                query.split('&').find_map(|pair| {
                    pair.strip_prefix("last-event-id=")
                        .map(|value| value.to_string())
                })
            });
        let result = serve_events(stream, engine, last.as_deref());
        sse_release(&ip);
        return result;
    }
    let mut engine = engine.lock().map_err(|e| e.to_string())?;
    handle_locked(
        &mut stream,
        &mut engine,
        token.as_deref(),
        method,
        path,
        &headers,
        &text,
    )
}

fn serve_events(
    mut stream: TcpStream,
    engine: Arc<Mutex<Engine>>,
    last_id: Option<&str>,
) -> Result<(), String> {
    let _ = stream.set_write_timeout(Some(Duration::from_secs(10)));
    let header = "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\nConnection: keep-alive\r\nX-Accel-Buffering: no\r\n\r\n";
    stream.write_all(header.as_bytes()).map_err(|e| e.to_string())?;
    let mut seen = last_id.and_then(|value| value.parse::<u64>().ok()).unwrap_or(0);
    let mut primed = false;
    let started = std::time::Instant::now();
    loop {
        if started.elapsed() > Duration::from_secs(600) {
            break;
        }
        let (cursor, payload) = {
            let mut engine = engine.lock().map_err(|e| e.to_string())?;
            (engine.cursor(), json::dumps(&engine.dashboard()))
        };
        if !primed || cursor != seen {
            let frame = format!("id: {cursor}\ndata: {payload}\n\n");
            if stream.write_all(frame.as_bytes()).is_err() {
                break;
            }
            seen = cursor;
            primed = true;
        } else if stream.write_all(b": keepalive\n\n").is_err() {
            break;
        }
        if stream.flush().is_err() {
            break;
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    Ok(())
}

fn handle_locked(
    stream: &mut TcpStream,
    engine: &mut Engine,
    token: Option<&str>,
    method: &str,
    path: &str,
    headers: &[(String, String)],
    text: &str,
) -> Result<(), String> {
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
                    stream,
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
        return write_html(stream, html.as_bytes());
    }
    if method == "GET" && path == "/health" {
        let body = json::dumps(&json::obj(vec![
            ("status", json::s("ok")),
            ("tools_version", json::s(&engine.schema.tools_version)),
        ]));
        return write_json(stream, 200, body.as_bytes());
    }
    if method == "GET" && path == "/tools" {
        let body = json::dumps(&mcp::schemas_for_http(engine));
        return write_json(stream, 200, body.as_bytes());
    }
    if method == "GET" && path == "/api/dashboard-data" {
        let body = json::dumps(&engine.dashboard());
        return write_json(stream, 200, body.as_bytes());
    }
    if method == "POST" && path == "/call" {
        let len = header("content-length")
            .and_then(|v| v.parse::<usize>().ok())
            .unwrap_or(0);
        if len > MAX_BODY {
            let body = format!("{{\"error\": \"Request body exceeds {MAX_BODY} bytes\"}}");
            return write_json(stream, 413, body.as_bytes());
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
                return write_json(stream, 400, b"{\"error\": \"Invalid JSON\"}");
            }
        };
        let tool = parsed.get("tool").and_then(Value::as_str).unwrap_or("");
        if tool.is_empty() {
            return write_json(
                stream,
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
                stream,
                400,
                b"{\"error\": \"'arguments' must be a JSON object\"}",
            );
        }
        match engine.call(tool, &arguments) {
            Ok(value) => {
                let body = json::dumps(&json::obj(vec![("result", value)]));
                write_json(stream, 200, body.as_bytes())
            }
            Err(err) if err.starts_with("Unknown tool:") || err.contains(": ") => {
                let status = if err.starts_with("Unknown tool:") {
                    404
                } else {
                    400
                };
                let body = json::dumps(&json::obj(vec![("error", json::s(err))]));
                write_json(stream, status, body.as_bytes())
            }
            Err(err) => {
                let body = json::dumps(&json::obj(vec![("error", json::s(err))]));
                write_json(stream, 400, body.as_bytes())
            }
        }
    } else {
        let body = json::dumps(&json::obj(vec![(
            "error",
            json::s(format!("Unknown endpoint: {path}")),
        )]));
        write_json(stream, 404, body.as_bytes())
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
        429 => "Too Many Requests",
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
