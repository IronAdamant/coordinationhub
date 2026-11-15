//! Small HTTP server for the dashboard and POST /call.
//!
//! Every request is read as bytes: the head up to its blank line (capped),
//! then exactly `Content-Length` body bytes (capped), validated as UTF-8
//! once and parsed once. Before anything is served the request must name
//! this server in `Host` and, when it carries an `Origin`, come from this
//! server's own page; `POST /call` must be `application/json`. Those three
//! checks are what keep another web page, or a DNS name rebound to
//! 127.0.0.1, from talking to a hub on the same machine.

use crate::engine::Engine;
use crate::json::{self, Value};
use crate::mcp;
use crate::waits::Waits;
use std::collections::HashMap;
use std::io::{Read, Write};
use std::net::{IpAddr, Shutdown, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

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

/// The largest `/call` body, in bytes. The stdio server uses the same cap
/// for one frame.
pub const MAX_BODY: usize = 1_000_000;

/// The largest request head (request line and headers), in bytes.
pub const MAX_HEAD: usize = 64 * 1024;

/// A whole request must arrive within this long.
const REQUEST_DEADLINE: Duration = Duration::from_secs(30);

/// Connections served at once unless `COORDINATIONHUB_MAX_CONNECTIONS`
/// says otherwise; one more is answered 503.
pub const MAX_CONNECTIONS: usize = 256;

fn max_connections() -> usize {
    std::env::var("COORDINATIONHUB_MAX_CONNECTIONS")
        .ok()
        .and_then(|value| value.trim().parse::<usize>().ok())
        .filter(|limit| *limit > 0)
        .unwrap_or(MAX_CONNECTIONS)
}

/// `host:port` for a URL or a Host header: an IPv6 address in brackets.
fn authority(host: &str, port: u16) -> String {
    if host.contains(':') && !host.starts_with('[') {
        format!("[{host}]:{port}")
    } else {
        format!("{host}:{port}")
    }
}

/// True for a name or address that only this machine can reach.
pub fn is_loopback_host(host: &str) -> bool {
    let bare = host.trim().trim_start_matches('[').trim_end_matches(']');
    if bare.eq_ignore_ascii_case("localhost") {
        return true;
    }
    bare.parse::<IpAddr>().map(|ip| ip.is_loopback()).unwrap_or(false)
}

/// Percent-encodes everything but RFC 3986 unreserved characters, so a
/// token can sit in a URL fragment and on a command line.
fn percent_encode(text: &str) -> String {
    use std::fmt::Write as _;
    let mut out = String::with_capacity(text.len());
    for byte in text.bytes() {
        if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'.' | b'_' | b'~') {
            out.push(byte as char);
        } else {
            let _ = write!(out, "%{byte:02X}");
        }
    }
    out
}

/// The address a browser opens: the dashboard, with the token in the
/// fragment. A fragment is never sent to a server and never logged by one;
/// the page reads it, keeps it for the tab and takes it out of the address
/// bar.
pub fn dashboard_url(host: &str, port: u16, token: Option<&str>) -> String {
    match token {
        Some(token) => format!("http://{}/#token={}", authority(host, port), percent_encode(token)),
        None => format!("http://{}/", authority(host, port)),
    }
}

/// Wait up to five seconds for `/health`, then hand the dashboard URL to
/// the desktop's opener and wait for the opener to exit, so it is not left
/// as a zombie. Failures are silent, as in 0.8.0.
fn open_dashboard(host: &str, port: u16, token: Option<&str>) {
    let deadline = Instant::now() + Duration::from_secs(5);
    let name = authority(host, port);
    while Instant::now() < deadline {
        let up = TcpStream::connect((host, port))
            .and_then(|mut stream| {
                stream.set_read_timeout(Some(Duration::from_millis(300)))?;
                write!(stream, "GET /health HTTP/1.0\r\nHost: {name}\r\n\r\n")?;
                let mut head = [0_u8; 12];
                stream.read_exact(&mut head)?;
                Ok(head.ends_with(b"200"))
            })
            .unwrap_or(false);
        if up {
            break;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    // The URL holds only unreserved characters, ':', '/', '#', '=', '[',
    // ']' and percent escapes: nothing a shell or `start` treats specially.
    let url = dashboard_url(host, port, token);
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
    if let Ok(mut child) = command
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()
    {
        let _ = child.wait();
    }
}

/// Opens (creating it if needed) a log file only its owner can read: mode
/// 0600 on Unix, also when the file already existed with a wider mode. On
/// Windows the file takes the permissions of its folder, which under the
/// user's profile is private to that user.
pub fn open_private_log(path: &Path) -> std::io::Result<std::fs::File> {
    let mut options = std::fs::OpenOptions::new();
    options.create(true).append(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let file = options.open(path)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        file.set_permissions(std::fs::Permissions::from_mode(0o600))?;
    }
    Ok(file)
}

/// `serve` prints the API banner. `serve-sse` prints the dashboard banner
/// and, unless `--no-browser`, opens the dashboard once `/health` answers.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Banner {
    Api,
    Dashboard { open_browser: bool },
}

/// What every connection needs to know about this server.
struct Config {
    token: Option<String>,
    host: String,
    port: u16,
}

/// The auth token: the one given, or 16 bytes from the operating system's
/// random generator as 32 hex characters. There is no fallback: with no
/// randomness the server does not start.
fn make_token(given: Option<String>) -> Result<String, String> {
    match given {
        Some(token) => {
            if token.is_empty() {
                return Err("--auth-token is empty; pass a secret of at least one character, or leave the flag out to have one generated".into());
            }
            if token.chars().any(|ch| ch.is_whitespace() || ch.is_control()) {
                return Err("--auth-token must not contain spaces or control characters: it travels in an Authorization header".into());
            }
            Ok(token)
        }
        None => crate::rand::hex(16).map_err(|err| {
            format!(
                "cannot generate an auth token: the operating system's random generator failed ({err}). Pass --auth-token <secret> to supply one; the server does not start with a guessable token"
            )
        }),
    }
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
    let local_only = is_loopback_host(host);
    if no_auth && !local_only {
        return Err(format!(
            "--no-auth with --host {host} would let anyone who can reach that address call every tool. Keep auth on (drop --no-auth), or bind a loopback address (127.0.0.1, ::1 or localhost)"
        ));
    }
    let token = if no_auth { None } else { Some(make_token(auth_token)?) };
    let engine = Engine::open(storage.as_deref(), project.as_deref(), namespace)?;
    let waits = Arc::new(Waits::new(storage, project, namespace));
    let listener = TcpListener::bind((host, port))
        .map_err(|e| format!("cannot listen on {}: {e}", authority(host, port)))?;
    let port = listener.local_addr().map(|addr| addr.port()).unwrap_or(port);
    if !local_only {
        eprintln!(
            "Warning: listening on {host}, which is not a loopback address: anyone who can reach it and has the token can call every tool, and the token crosses the network unencrypted."
        );
    }
    let name = authority(host, port);
    match banner {
        Banner::Api => {
            println!("Starting CoordinationHub HTTP server on http://{name}");
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
                let (host, token) = (host.to_string(), token.clone());
                std::thread::spawn(move || open_dashboard(&host, port, token.as_deref()));
            }
            println!("Starting SSE dashboard server on {name}");
            println!("Dashboard: {}", dashboard_url(host, port, token.as_deref()));
            println!("SSE stream: http://{name}/events");
            println!("Press Ctrl+C to stop");
        }
    }
    let config = Arc::new(Config { token, host: host.to_string(), port });
    let engine = Arc::new(Mutex::new(engine));
    crate::house::spawn(engine.clone());
    let open = Arc::new(AtomicUsize::new(0));
    let limit = max_connections();
    let turned_away = turn_away(limit);
    for stream in listener.incoming() {
        let Ok(stream) = stream else { continue };
        // One thread per connection, and at most `limit` of them. One
        // more is handed to the single thread that answers 503; when that
        // thread's queue is full too, the connection is just closed.
        if open.fetch_add(1, Ordering::SeqCst) >= limit {
            open.fetch_sub(1, Ordering::SeqCst);
            let _ = turned_away.try_send(stream);
            continue;
        }
        let slot = Slot(open.clone());
        let config = config.clone();
        let engine = engine.clone();
        let waits = waits.clone();
        // A thread that cannot start drops its closure, and the slot with it.
        let _ = std::thread::Builder::new().spawn(move || {
            let _slot = slot;
            let _ = dispatch_connection(stream, engine, waits, &config);
        });
    }
    Ok(())
}

/// Starts the thread that answers `503` to connections over the limit,
/// one at a time, and returns its queue (64 waiting at most). The accept
/// loop never writes to a refused connection itself, so a client that
/// does not read cannot hold up the connections that are served.
fn turn_away(limit: usize) -> std::sync::mpsc::SyncSender<TcpStream> {
    let (queue, refused) = std::sync::mpsc::sync_channel::<TcpStream>(64);
    let body = error_body(&format!("server busy: {limit} connections are open; retry in a moment"));
    let _ = std::thread::Builder::new().spawn(move || {
        for mut stream in refused {
            let _ = stream.set_write_timeout(Some(Duration::from_secs(1)));
            let _ = finish(&mut stream, 503, "application/json", body.as_bytes(), &["Retry-After: 1"]);
        }
    });
    queue
}

/// One of the `limit` connection slots; freed when the connection ends.
struct Slot(Arc<AtomicUsize>);

impl Drop for Slot {
    fn drop(&mut self) {
        self.0.fetch_sub(1, Ordering::SeqCst);
    }
}

/// A parsed request head.
struct Head {
    method: String,
    path: String,
    query: String,
    headers: Vec<(String, String)>,
    /// Body bytes that arrived with the head.
    rest: Vec<u8>,
}

impl Head {
    fn header(&self, name: &str) -> Option<&str> {
        self.headers
            .iter()
            .find(|(key, _)| key == name)
            .map(|(_, value)| value.as_str())
    }
}

/// Why a request could not be read: nothing arrived, or a status and what
/// is wrong.
enum Refusal {
    Closed,
    Status(u16, String),
}

fn refuse<T>(status: u16, why: impl Into<String>) -> Result<T, Refusal> {
    Err(Refusal::Status(status, why.into()))
}

/// One `read` that gives up at `deadline`.
fn read_some(stream: &mut TcpStream, buf: &mut [u8], deadline: Instant) -> Result<usize, Refusal> {
    loop {
        let left = deadline.saturating_duration_since(Instant::now());
        if left.is_zero() {
            return refuse(408, "the request did not arrive within 30 seconds");
        }
        let _ = stream.set_read_timeout(Some(left));
        match stream.read(buf) {
            Ok(n) => return Ok(n),
            Err(err) if err.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(err) if matches!(err.kind(), std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut) => {
                return refuse(408, "the request did not arrive within 30 seconds");
            }
            Err(_) => return Err(Refusal::Closed),
        }
    }
}

/// Reads up to the blank line that ends the head, however the bytes are
/// split across packets, and parses the request line and headers.
fn read_head(stream: &mut TcpStream, deadline: Instant) -> Result<Head, Refusal> {
    let mut buf: Vec<u8> = Vec::with_capacity(2048);
    let mut chunk = [0_u8; 8192];
    let end = loop {
        // The terminator may straddle two reads: look back three bytes.
        let from = buf.len().saturating_sub(3);
        let got = read_some(stream, &mut chunk, deadline)?;
        if got == 0 {
            if buf.is_empty() {
                return Err(Refusal::Closed);
            }
            return refuse(400, "the connection closed before the request head ended (a blank line was expected)");
        }
        buf.extend_from_slice(&chunk[..got]);
        if let Some(at) = buf[from..].windows(4).position(|window| window == b"\r\n\r\n") {
            break from + at;
        }
        if buf.len() > MAX_HEAD {
            return refuse(431, format!("request head exceeds {MAX_HEAD} bytes"));
        }
    };
    if end > MAX_HEAD {
        return refuse(431, format!("request head exceeds {MAX_HEAD} bytes"));
    }
    let rest = buf[end + 4..].to_vec();
    let Ok(text) = std::str::from_utf8(&buf[..end]) else {
        return refuse(400, "the request head is not UTF-8");
    };
    let mut lines = text.split("\r\n");
    let mut parts = lines.next().unwrap_or("").split(' ');
    let (Some(method), Some(target), Some(version), None) = (parts.next(), parts.next(), parts.next(), parts.next())
    else {
        return refuse(400, "malformed request line (expected: METHOD /path HTTP/1.1)");
    };
    if method.is_empty() || !target.starts_with('/') || !version.starts_with("HTTP/1.") {
        return refuse(400, "malformed request line (expected: METHOD /path HTTP/1.1)");
    }
    let (path, query) = target.split_once('?').unwrap_or((target, ""));
    let mut headers: Vec<(String, String)> = Vec::new();
    for line in lines {
        let Some((name, value)) = line.split_once(':') else {
            return refuse(400, "malformed header line (expected: Name: value)");
        };
        let name = name.to_ascii_lowercase();
        if name.is_empty() || name.contains(|ch: char| ch.is_whitespace()) {
            return refuse(400, "malformed header line (expected: Name: value)");
        }
        let value = value.trim().to_string();
        // Two different values for one of these mean two parsers could
        // disagree about where the request ends or who it is for.
        if matches!(name.as_str(), "host" | "content-length" | "authorization" | "origin" | "content-type")
            && headers.iter().any(|(seen, _)| *seen == name)
        {
            return refuse(400, format!("the {name} header appears more than once"));
        }
        headers.push((name, value));
    }
    Ok(Head {
        method: method.to_string(),
        path: path.to_string(),
        query: query.to_string(),
        headers,
        rest,
    })
}

/// Reads exactly the `Content-Length` bytes of the body: no more than
/// `MAX_BODY`, and UTF-8.
fn read_body(stream: &mut TcpStream, head: &mut Head, deadline: Instant) -> Result<String, Refusal> {
    if head.header("transfer-encoding").is_some() {
        return refuse(501, "Transfer-Encoding is not supported; send the body with Content-Length");
    }
    let Some(length) = head.header("content-length") else {
        return refuse(411, "POST /call needs a Content-Length header");
    };
    if length.is_empty() || !length.bytes().all(|byte| byte.is_ascii_digit()) {
        return refuse(400, "Content-Length must be a whole number of bytes");
    }
    let len = match length.parse::<usize>() {
        Ok(len) if len <= MAX_BODY => len,
        _ => return refuse(413, format!("Request body exceeds {MAX_BODY} bytes")),
    };
    let mut body = std::mem::take(&mut head.rest);
    body.truncate(len);
    let mut chunk = [0_u8; 8192];
    while body.len() < len {
        let want = (len - body.len()).min(chunk.len());
        let got = read_some(stream, &mut chunk[..want], deadline)?;
        if got == 0 {
            return refuse(
                400,
                format!("the body ended after {} of the {len} bytes Content-Length announced", body.len()),
            );
        }
        body.extend_from_slice(&chunk[..got]);
    }
    String::from_utf8(body).map_err(|_| Refusal::Status(400, "the request body is not UTF-8".into()))
}

/// Splits a Host header into its name and port.
fn split_host(value: &str) -> Option<(&str, Option<u16>)> {
    let value = value.trim();
    let (name, port) = if value.starts_with('[') {
        let close = value.find(']')?;
        (&value[..=close], &value[close + 1..])
    } else {
        match value.rfind(':') {
            Some(at) => (&value[..at], &value[at..]),
            None => (value, ""),
        }
    };
    if name.is_empty() || (!name.starts_with('[') && name.contains(':')) {
        return None;
    }
    if port.is_empty() {
        return Some((name, None));
    }
    let digits = port.strip_prefix(':')?;
    if digits.is_empty() || !digits.bytes().all(|byte| byte.is_ascii_digit()) {
        return None;
    }
    Some((name, Some(digits.parse().ok()?)))
}

/// True when `value`, a Host header, names this server: the port it
/// listens on (which may be left out only for port 80), and a loopback
/// address, `localhost`, the exact `--host` value, or the address this
/// connection arrived on. Any other name is a page or a DNS record
/// pointing a browser here (DNS rebinding) and is refused.
fn host_allowed(value: &str, config: &Config, arrived_on: Option<IpAddr>) -> bool {
    let Some((name, port)) = split_host(value) else {
        return false;
    };
    if port.unwrap_or(80) != config.port {
        return false;
    }
    let bare = name.trim_start_matches('[').trim_end_matches(']');
    let bound = config.host.trim_start_matches('[').trim_end_matches(']');
    if bare.eq_ignore_ascii_case("localhost") || bare.eq_ignore_ascii_case(bound) {
        return true;
    }
    match bare.parse::<IpAddr>() {
        Ok(ip) => {
            ip.is_loopback()
                || arrived_on
                    .map(|local| local.to_canonical() == ip.to_canonical())
                    .unwrap_or(false)
        }
        Err(_) => false,
    }
}

/// True when `presented` is `expected`, in time that does not depend on
/// where they first differ. (The lengths are not secret.)
pub fn same_secret(presented: &[u8], expected: &[u8]) -> bool {
    let mut diff = presented.len() ^ expected.len();
    for index in 0..presented.len().max(expected.len()) {
        let left = presented.get(index).copied().unwrap_or(0);
        let right = expected.get(index).copied().unwrap_or(0);
        diff |= usize::from(left ^ right);
    }
    std::hint::black_box(diff) == 0
}

/// True when the Authorization header carries `Bearer <expected>`.
fn authorized(header: Option<&str>, expected: &str) -> bool {
    let Some(value) = header else {
        return false;
    };
    let Some((scheme, token)) = value.trim().split_once(' ') else {
        return false;
    };
    scheme.eq_ignore_ascii_case("bearer") && same_secret(token.trim().as_bytes(), expected.as_bytes())
}

fn error_body(why: &str) -> String {
    json::dumps(&json::obj(vec![("error", json::s(why))]))
}

fn dispatch_connection(
    mut stream: TcpStream,
    engine: Arc<Mutex<Engine>>,
    waits: Arc<Waits>,
    config: &Config,
) -> Result<(), String> {
    let _ = stream.set_write_timeout(Some(Duration::from_secs(30)));
    let deadline = Instant::now() + REQUEST_DEADLINE;
    let mut head = match read_head(&mut stream, deadline) {
        Ok(head) => head,
        Err(Refusal::Closed) => return Ok(()),
        Err(Refusal::Status(status, why)) => {
            return finish(&mut stream, status, "application/json", error_body(&why).as_bytes(), &[]);
        }
    };
    // Who is this request for, and where does it come from?
    let arrived_on = stream.local_addr().ok().map(|addr| addr.ip());
    let host = head.header("host").unwrap_or("").to_string();
    if !host_allowed(&host, config, arrived_on) {
        let shown = if is_loopback_host(&config.host) { "127.0.0.1" } else { &config.host };
        let why = format!(
            "Host header {} does not name this server; use http://{}",
            json::dumps(&json::s(&host)),
            authority(shown, config.port)
        );
        return finish(&mut stream, 421, "application/json", error_body(&why).as_bytes(), &[]);
    }
    if let Some(origin) = head.header("origin") {
        if !origin.eq_ignore_ascii_case(&format!("http://{}", host.trim())) {
            let why = "cross-origin request refused: the Origin header is not this server's own address";
            return finish(&mut stream, 403, "application/json", error_body(why).as_bytes(), &[]);
        }
    }
    let open = head.path == "/" || head.path == "/health";
    if let Some(expected) = &config.token {
        if !open && !authorized(head.header("authorization"), expected) {
            return finish(
                &mut stream,
                401,
                "application/json",
                b"{\"error\": \"unauthorized\"}",
                &["WWW-Authenticate: Bearer realm=\"coordinationhub\""],
            );
        }
    }
    if head.method == "GET" && head.path == "/events" {
        let ip = stream
            .peer_addr()
            .map(|addr| addr.ip().to_string())
            .unwrap_or_else(|_| "unknown".into());
        if !sse_admit(&ip) {
            let body = b"{\"error\": \"too many SSE connections\"}";
            return finish(&mut stream, 429, "application/json", body, &[]);
        }
        let last = head.header("last-event-id").map(|value| value.to_string()).or_else(|| {
            head.query
                .split('&')
                .find_map(|pair| pair.strip_prefix("last-event-id=").map(|value| value.to_string()))
        });
        let result = serve_events(stream, engine, last.as_deref());
        sse_release(&ip);
        return result;
    }
    let (status, kind, body, extra) = match answer(&mut stream, &engine, &waits, &mut head, deadline) {
        Ok(reply) => reply,
        Err(Refusal::Closed) => return Ok(()),
        Err(Refusal::Status(status, why)) => {
            (status, "application/json", error_body(&why).into_bytes(), Vec::new())
        }
    };
    finish(&mut stream, status, kind, &body, &extra)
}

fn serve_events(
    mut stream: TcpStream,
    engine: Arc<Mutex<Engine>>,
    last_id: Option<&str>,
) -> Result<(), String> {
    let _ = stream.set_write_timeout(Some(Duration::from_secs(10)));
    let header = "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-store\r\nConnection: keep-alive\r\nX-Accel-Buffering: no\r\nX-Content-Type-Options: nosniff\r\n\r\n";
    stream.write_all(header.as_bytes()).map_err(|e| e.to_string())?;
    let mut seen = last_id.and_then(|value| value.parse::<u64>().ok()).unwrap_or(0);
    let mut primed = false;
    let started = Instant::now();
    let mut quiet_ticks = 0_u32;
    loop {
        if started.elapsed() > Duration::from_secs(600) {
            break;
        }
        // Compare the cursor first: the dashboard is built (and the whole
        // state walked) only when the journal has moved.
        let frame = {
            let mut engine = engine.lock().map_err(|e| e.to_string())?;
            let cursor = engine.cursor();
            if !primed || cursor != seen {
                seen = cursor;
                primed = true;
                Some(format!("id: {cursor}\ndata: {}\n\n", json::dumps(&engine.dashboard())))
            } else {
                None
            }
        };
        match frame {
            Some(frame) => {
                quiet_ticks = 0;
                if stream.write_all(frame.as_bytes()).is_err() {
                    break;
                }
            }
            None => {
                // A comment every two seconds shows the page the stream
                // is alive and shows this loop a closed connection.
                quiet_ticks += 1;
                if quiet_ticks >= 10 {
                    quiet_ticks = 0;
                    if stream.write_all(b": keepalive\n\n").is_err() {
                        break;
                    }
                }
            }
        }
        if stream.flush().is_err() {
            break;
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    Ok(())
}

type Reply = (u16, &'static str, Vec<u8>, Vec<&'static str>);

fn json_reply(status: u16, body: String) -> Result<Reply, Refusal> {
    Ok((status, "application/json", body.into_bytes(), Vec::new()))
}

/// Answers one request that passed the Host, Origin and token checks. The
/// shared engine is locked only while a call uses it; a waiting `/call`
/// runs on its own engine (see `waits`).
fn answer(
    stream: &mut TcpStream,
    shared: &Mutex<Engine>,
    waits: &Waits,
    head: &mut Head,
    deadline: Instant,
) -> Result<Reply, Refusal> {
    let locked = || {
        shared
            .lock()
            .map_err(|_| Refusal::Status(500, "the engine is unavailable".into()))
    };
    match (head.method.as_str(), head.path.as_str()) {
        ("GET", "/") => Ok((
            200,
            "text/html; charset=utf-8",
            DASHBOARD.as_bytes().to_vec(),
            // The page holds no secret. It may run its own inline script
            // and style, talk only to this server, and not be framed.
            vec![
                "Content-Security-Policy: default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
                "X-Frame-Options: DENY",
                "Referrer-Policy: no-referrer",
            ],
        )),
        ("GET", "/health") => json_reply(
            200,
            json::dumps(&json::obj(vec![
                ("status", json::s("ok")),
                ("tools_version", json::s(&locked()?.schema.tools_version)),
            ])),
        ),
        ("GET", "/tools") => json_reply(200, json::dumps(&mcp::schemas_for_http(&*locked()?))),
        ("GET", "/api/dashboard-data") => json_reply(200, json::dumps(&locked()?.dashboard())),
        ("POST", "/call") => {
            let kind = head
                .header("content-type")
                .map(|value| value.split(';').next().unwrap_or("").trim().to_ascii_lowercase())
                .unwrap_or_default();
            if kind != "application/json" {
                return refuse(415, "POST /call needs Content-Type: application/json");
            }
            let text = read_body(stream, head, deadline)?;
            let parsed = match json::parse(&text) {
                Ok(value @ Value::Object(_)) => value,
                Ok(_) => return refuse(400, "Invalid JSON: the request body must be a JSON object"),
                Err(err) => return refuse(400, format!("Invalid JSON: {err}")),
            };
            let tool = parsed.get("tool").and_then(Value::as_str).unwrap_or("");
            if tool.is_empty() {
                return refuse(400, "Missing 'tool' field in request body");
            }
            let arguments = parsed.get("arguments").cloned().unwrap_or(Value::Object(vec![]));
            if !matches!(arguments, Value::Object(_)) {
                return refuse(400, "'arguments' must be a JSON object");
            }
            let outcome = if crate::engine::blocks(tool, &arguments) {
                if !waits.admit() {
                    return refuse(503, Waits::refusal());
                }
                waits.run(tool, &arguments, Arc::new(AtomicBool::new(false)))
            } else {
                locked()?.call(tool, &arguments)
            };
            match outcome {
                Ok(value) => json_reply(200, json::dumps(&json::obj(vec![("result", value)]))),
                Err(err) if err.starts_with("Unknown tool:") => refuse(404, err),
                Err(err) => refuse(400, err),
            }
        }
        _ => refuse(404, format!("Unknown endpoint: {}", head.path)),
    }
}

fn reason(status: u16) -> &'static str {
    match status {
        200 => "OK",
        400 => "Bad Request",
        401 => "Unauthorized",
        403 => "Forbidden",
        404 => "Not Found",
        408 => "Request Timeout",
        411 => "Length Required",
        413 => "Payload Too Large",
        415 => "Unsupported Media Type",
        421 => "Misdirected Request",
        429 => "Too Many Requests",
        431 => "Request Header Fields Too Large",
        500 => "Internal Server Error",
        501 => "Not Implemented",
        503 => "Service Unavailable",
        _ => "Error",
    }
}

fn respond(stream: &mut TcpStream, status: u16, kind: &str, body: &[u8], extra: &[&str]) -> std::io::Result<()> {
    let mut header = format!(
        "HTTP/1.0 {status} {}\r\nContent-Type: {kind}\r\nX-Content-Type-Options: nosniff\r\nCache-Control: no-store\r\n",
        reason(status)
    );
    for line in extra {
        header.push_str(line);
        header.push_str("\r\n");
    }
    header.push_str(&format!("Content-Length: {}\r\nConnection: close\r\n\r\n", body.len()));
    // Head and body in one write, so a client never gets one without
    // the other.
    let mut message = header.into_bytes();
    message.extend_from_slice(body);
    stream.write_all(&message)?;
    stream.flush()
}

/// Sends the response, then lets the client finish talking before the
/// socket closes. Closing with unread request bytes resets the connection,
/// and the client would lose the very answer that says what was wrong.
fn finish(stream: &mut TcpStream, status: u16, kind: &str, body: &[u8], extra: &[&str]) -> Result<(), String> {
    let sent = respond(stream, status, kind, body, extra);
    let _ = stream.shutdown(Shutdown::Write);
    let _ = stream.set_read_timeout(Some(Duration::from_millis(250)));
    let mut sink = [0_u8; 8192];
    let mut drained = 0;
    // At most a megabyte and one second: a client that drips bytes
    // cannot keep the connection.
    let until = Instant::now() + Duration::from_secs(1);
    while drained <= MAX_BODY && Instant::now() < until {
        match stream.read(&mut sink) {
            Ok(0) | Err(_) => break,
            Ok(n) => drained += n,
        }
    }
    sent.map_err(|err| err.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn config(host: &str, port: u16) -> Config {
        Config { token: None, host: host.to_string(), port }
    }

    #[test]
    fn host_must_name_this_server() {
        let local = config("127.0.0.1", 9898);
        let on_loopback = Some("127.0.0.1".parse().unwrap());
        for good in ["127.0.0.1:9898", "localhost:9898", "LocalHost:9898", "[::1]:9898", "127.0.0.2:9898", " 127.0.0.1:9898 "] {
            assert!(host_allowed(good, &local, on_loopback), "{good}");
        }
        for bad in [
            "evil.example",
            "evil.example:9898",
            "127.0.0.1",
            "127.0.0.1:80",
            "127.0.0.1:9899",
            "localhost",
            "localhost.evil.example:9898",
            "127.0.0.1.evil.example:9898",
            "192.168.1.5:9898",
            "::1:9898",
            "[::1]",
            "[::1]9898",
            "127.0.0.1:98980",
            "127.0.0.1:",
            ":9898",
            "",
            "127.0.0.1:9898, evil.example",
        ] {
            assert!(!host_allowed(bad, &local, on_loopback), "{bad}");
        }
        // Port 80 may be left out, as browsers do.
        let plain = config("127.0.0.1", 80);
        assert!(host_allowed("localhost", &plain, on_loopback));
        assert!(host_allowed("127.0.0.1:80", &plain, on_loopback));
        // The exact --host value, whatever it is.
        let named = config("hub.internal", 9898);
        assert!(host_allowed("hub.internal:9898", &named, None));
        assert!(host_allowed("HUB.internal:9898", &named, None));
        assert!(!host_allowed("other.internal:9898", &named, None));
        // A wildcard bind: the address the connection arrived on.
        let wide = config("0.0.0.0", 9898);
        let on_lan: Option<IpAddr> = Some("192.168.1.5".parse().unwrap());
        assert!(host_allowed("192.168.1.5:9898", &wide, on_lan));
        assert!(host_allowed("0.0.0.0:9898", &wide, on_lan));
        assert!(!host_allowed("192.168.1.6:9898", &wide, on_lan));
        assert!(!host_allowed("evil.example:9898", &wide, on_lan));
        let mapped: Option<IpAddr> = Some("::ffff:192.168.1.5".parse().unwrap());
        assert!(host_allowed("192.168.1.5:9898", &wide, mapped));
        // An IPv6 --host is written with brackets in a Host header.
        let six = config("::1", 9898);
        assert!(host_allowed("[::1]:9898", &six, None));
    }

    #[test]
    fn loopback_hosts_are_recognised() {
        for host in ["127.0.0.1", "127.8.9.1", "::1", "[::1]", "localhost", "LOCALHOST"] {
            assert!(is_loopback_host(host), "{host}");
        }
        for host in ["0.0.0.0", "::", "192.168.1.5", "example.com", "localhost.example.com", ""] {
            assert!(!is_loopback_host(host), "{host}");
        }
    }

    #[test]
    fn secrets_compare_by_value() {
        assert!(same_secret(b"maple-token", b"maple-token"));
        assert!(same_secret(b"", b""));
        for wrong in ["maple-toke", "maple-token2", "Maple-token", "", "maple-tokem", "xaple-token"] {
            assert!(!same_secret(wrong.as_bytes(), b"maple-token"), "{wrong}");
        }
        assert!(authorized(Some("Bearer maple-token"), "maple-token"));
        assert!(authorized(Some("bearer maple-token"), "maple-token"));
        assert!(authorized(Some("  Bearer   maple-token  "), "maple-token"));
        for wrong in ["maple-token", "Basic maple-token", "Bearer", "Bearer maple-token x", "Bearermaple-token", ""] {
            assert!(!authorized(Some(wrong), "maple-token"), "{wrong}");
        }
        assert!(!authorized(None, "maple-token"));
    }

    #[test]
    fn generated_tokens_come_from_the_system_generator() {
        let first = make_token(None).unwrap();
        let second = make_token(None).unwrap();
        assert_eq!(first.len(), 32);
        assert!(first.bytes().all(|byte| byte.is_ascii_hexdigit()));
        assert_ne!(first, second);
        // 1.2.0 on Windows: a timestamp, then sixteen zeros.
        assert!(!first.ends_with("0000000000000000"), "{first}");
        assert_ne!(first, "0".repeat(32));
        assert_eq!(make_token(Some("chosen".into())).unwrap(), "chosen");
        assert!(make_token(Some(String::new())).unwrap_err().contains("empty"));
        assert!(make_token(Some("two words".into())).unwrap_err().contains("spaces"));
        assert!(make_token(Some("line\nbreak".into())).is_err());
    }

    #[test]
    fn the_dashboard_url_keeps_the_token_in_the_fragment() {
        assert_eq!(dashboard_url("127.0.0.1", 9898, Some("abc123")), "http://127.0.0.1:9898/#token=abc123");
        assert_eq!(dashboard_url("127.0.0.1", 9898, None), "http://127.0.0.1:9898/");
        assert_eq!(dashboard_url("::1", 9898, Some("t")), "http://[::1]:9898/#token=t");
        // Nothing a shell or `start` treats specially survives.
        let url = dashboard_url("127.0.0.1", 1, Some("a&b|c\"d%e^f<g>"));
        assert_eq!(url, "http://127.0.0.1:1/#token=a%26b%7Cc%22d%25e%5Ef%3Cg%3E");
    }

    #[cfg(unix)]
    #[test]
    fn the_dashboard_log_is_owner_only() {
        use std::os::unix::fs::PermissionsExt;
        let dir = std::env::temp_dir().join(format!("chub-log-{}-{}", std::process::id(), crate::util::now()));
        std::fs::create_dir_all(&dir).unwrap();
        let mode = |path: &Path| std::fs::metadata(path).unwrap().permissions().mode() & 0o777;
        // A new file.
        let fresh = dir.join("dashboard.log");
        drop(open_private_log(&fresh).unwrap());
        assert_eq!(mode(&fresh), 0o600);
        // A file 1.2.0 left readable by everyone.
        let old = dir.join("old.log");
        std::fs::write(&old, "Auth token: left-over\n").unwrap();
        std::fs::set_permissions(&old, std::fs::Permissions::from_mode(0o644)).unwrap();
        let mut file = open_private_log(&old).unwrap();
        file.write_all(b"appended\n").unwrap();
        assert_eq!(mode(&old), 0o600);
        assert_eq!(std::fs::read_to_string(&old).unwrap(), "Auth token: left-over\nappended\n");
        let _ = std::fs::remove_dir_all(&dir);
    }
}
