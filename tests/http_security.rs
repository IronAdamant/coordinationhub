//! 1.3.0, the HTTP server: requests read as bytes, Host / Origin /
//! Content-Type checks, the auth token, and a dashboard page that never
//! holds the token and still gets its data. Each is listed in MIGRATION.md.

mod common;
#[path = "common/http.rs"]
mod http_server;

use common::Hub;
use coordinationhub::json::{self, Value};
use http_server::{body, status, HttpServer};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::Shutdown;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

const TOKEN: &str = "maple-token";

fn dirs(hub: &Hub) -> [String; 4] {
    [
        "--storage-dir".into(),
        hub.store.display().to_string(),
        "--project-root".into(),
        hub.project.display().to_string(),
    ]
}

/// `serve` with a fixed token.
fn api(hub: &Hub) -> HttpServer {
    let dirs = dirs(hub);
    let mut args: Vec<&str> = dirs.iter().map(String::as_str).collect();
    args.extend(["--auth-token", TOKEN]);
    HttpServer::start("serve", &args, &[])
}

/// `serve --no-auth`.
fn open_api(hub: &Hub) -> HttpServer {
    let dirs = dirs(hub);
    let mut args: Vec<&str> = dirs.iter().map(String::as_str).collect();
    args.push("--no-auth");
    HttpServer::start("serve", &args, &[])
}

/// `serve-sse --no-browser` with a generated token; returns the token.
fn dashboard(hub: &Hub) -> (HttpServer, String) {
    let dirs = dirs(hub);
    let mut args: Vec<&str> = dirs.iter().map(String::as_str).collect();
    args.push("--no-browser");
    let mut server = HttpServer::start("serve-sse", &args, &[]);
    let token = server
        .seen
        .iter()
        .find_map(|line| line.strip_prefix("Auth token: "))
        .expect("the banner names the token")
        .to_string();
    server.line_with("Press Ctrl+C", Duration::from_secs(10));
    (server, token)
}

fn bearer() -> String {
    format!("Authorization: Bearer {TOKEN}")
}

/// The JSON body of a response.
fn reply(response: &str) -> Value {
    json::parse(body(response)).unwrap_or_else(|err| panic!("{err}: {response}"))
}

fn error_of(response: &str) -> String {
    reply(response).get("error").and_then(Value::as_str).unwrap_or("").to_string()
}

fn agents(server: &HttpServer, auth: &[&str]) -> Vec<String> {
    let response = server.call(r#"{"tool":"list_agents","arguments":{}}"#, auth);
    assert_eq!(status(&response), 200, "{response}");
    reply(&response)
        .get("result")
        .and_then(|result| result.get("agents"))
        .and_then(Value::as_array)
        .map(|rows| {
            rows.iter()
                .filter_map(|row| row.get("agent_id").and_then(Value::as_str))
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default()
}

// ---------------------------------------------------------------- A6

/// 1.2.0 served `Host: evil.example`: a DNS name rebound to 127.0.0.1 could
/// read the page, and with --no-auth call every tool.
#[test]
fn the_host_header_must_name_this_server() {
    let hub = Hub::new("host");
    let server = api(&hub);
    let auth = bearer();
    for path in ["/", "/health", "/tools", "/api/dashboard-data", "/events"] {
        let response = server.raw(format!("GET {path} HTTP/1.1\r\nHost: evil.example\r\n{auth}\r\n\r\n").as_bytes());
        assert_eq!(status(&response), 421, "{path}: {response}");
        assert!(error_of(&response).contains("Host header \"evil.example\" does not name this server"), "{response}");
    }
    let call = r#"{"tool":"register_agent","arguments":{"agent_id":"rebound"}}"#;
    let response = server.raw(
        format!(
            "POST /call HTTP/1.1\r\nHost: evil.example:{}\r\n{auth}\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{call}",
            server.port,
            call.len()
        )
        .as_bytes(),
    );
    assert_eq!(status(&response), 421, "{response}");
    // Wrong port, no port, no Host at all, two Hosts.
    for host in [format!("Host: 127.0.0.1:{}\r\n", server.port + 1), "Host: 127.0.0.1\r\n".into(), String::new()] {
        let response = server.raw(format!("GET /health HTTP/1.1\r\n{host}\r\n").as_bytes());
        assert_eq!(status(&response), 421, "{host:?}: {response}");
    }
    let twice = format!("GET /health HTTP/1.1\r\nHost: 127.0.0.1:{0}\r\nHost: evil.example:{0}\r\n\r\n", server.port);
    assert_eq!(status(&server.raw(twice.as_bytes())), 400);
    // The names a local client uses are served.
    for host in ["127.0.0.1", "localhost", "LOCALHOST", "[::1]"] {
        let response = server.raw(format!("GET /health HTTP/1.1\r\nHost: {host}:{}\r\n\r\n", server.port).as_bytes());
        assert_eq!(status(&response), 200, "{host}: {response}");
    }
    assert!(agents(&server, &[&auth]).is_empty(), "a refused request ran");
}

/// A web page can send `text/plain` to another origin without asking
/// (no preflight). `/call` therefore takes `application/json` only.
#[test]
fn call_needs_a_json_content_type() {
    let hub = Hub::new("ctype");
    let server = open_api(&hub);
    let call = r#"{"tool":"register_agent","arguments":{"agent_id":"plain"}}"#;
    for kind in ["Content-Type: text/plain\r\n", "Content-Type: application/x-www-form-urlencoded\r\n", "Content-Type: application/jsonx\r\n", ""] {
        let response = server.raw(
            format!(
                "POST /call HTTP/1.1\r\nHost: {}\r\n{kind}Content-Length: {}\r\n\r\n{call}",
                server.host(),
                call.len()
            )
            .as_bytes(),
        );
        assert_eq!(status(&response), 415, "{kind:?}: {response}");
        assert_eq!(error_of(&response), "POST /call needs Content-Type: application/json");
    }
    assert!(agents(&server, &[]).is_empty(), "a refused request ran");
    for kind in ["application/json", "application/json; charset=utf-8", "Application/JSON"] {
        let call = r#"{"tool":"status"}"#;
        let response = server.raw(
            format!(
                "POST /call HTTP/1.1\r\nHost: {}\r\nContent-Type: {kind}\r\nContent-Length: {}\r\n\r\n{call}",
                server.host(),
                call.len()
            )
            .as_bytes(),
        );
        assert_eq!(status(&response), 200, "{kind}: {response}");
    }
}

#[test]
fn a_request_from_another_origin_is_refused() {
    let hub = Hub::new("origin");
    let server = open_api(&hub);
    let call = r#"{"tool":"register_agent","arguments":{"agent_id":"crossed"}}"#;
    for origin in ["http://evil.example", "https://evil.example", "null", "http://127.0.0.1", "http://127.0.0.1:1"] {
        let header = format!("Origin: {origin}");
        let response = server.call(call, &[&header]);
        assert_eq!(status(&response), 403, "{origin}: {response}");
        assert!(error_of(&response).contains("cross-origin request refused"), "{response}");
        assert_eq!(status(&server.get("/api/dashboard-data", &[&header])), 403, "{origin}");
        assert_eq!(status(&server.get("/", &[&header])), 403, "{origin}");
    }
    assert!(agents(&server, &[]).is_empty(), "a refused request ran");
    // The page's own origin is served.
    let own = format!("Origin: http://{}", server.host());
    assert_eq!(status(&server.call(call, &[&own])), 200);
    assert_eq!(agents(&server, &[]), ["crossed"]);
    // The live repro against 1.2.0: Host evil.example, text/plain, no auth.
    let response = server.raw(
        format!(
            "POST /call HTTP/1.1\r\nHost: evil.example\r\nOrigin: http://evil.example\r\nContent-Type: text/plain\r\nContent-Length: {}\r\n\r\n{call}",
            call.len()
        )
        .as_bytes(),
    );
    assert_eq!(status(&response), 421, "{response}");
}

#[test]
fn no_auth_needs_a_loopback_host() {
    let hub = Hub::new("noauth");
    let dirs = dirs(&hub);
    for host in ["0.0.0.0", "::", "192.0.2.7", "example.com"] {
        let mut args: Vec<&str> = dirs.iter().map(String::as_str).collect();
        args.extend(["--no-auth", "--host", host]);
        let refused = HttpServer::try_start("serve", &args, &[]).err().expect("the server started");
        assert!(refused.contains(&format!("--no-auth with --host {host}")), "{refused}");
        assert!(refused.contains("loopback"), "{refused}");
    }
    // An empty or unusable token is refused as well.
    for (token, why) in [("", "--auth-token is empty"), ("two words", "spaces or control characters")] {
        let mut args: Vec<&str> = dirs.iter().map(String::as_str).collect();
        args.extend(["--auth-token", token]);
        let refused = HttpServer::try_start("serve", &args, &[]).err().expect("the server started");
        assert!(refused.contains(why), "{refused}");
    }
    // Loopback with --no-auth still starts, with no warning.
    let server = open_api(&hub);
    assert_eq!(status(&server.get("/health", &[])), 200);
    assert_eq!(server.stderr(), "");
}

/// A non-loopback `--host` with auth starts and warns once. (Linux only:
/// elsewhere a wildcard bind can raise a firewall prompt on the desktop.)
#[cfg(target_os = "linux")]
#[test]
fn a_non_loopback_host_with_auth_warns_once() {
    let hub = Hub::new("wide");
    let dirs = dirs(&hub);
    let mut args: Vec<&str> = dirs.iter().map(String::as_str).collect();
    args.extend(["--auth-token", TOKEN, "--host", "0.0.0.0"]);
    let server = HttpServer::start("serve", &args, &[]);
    let auth = bearer();
    assert_eq!(status(&server.get("/tools", &[&auth])), 200);
    let warnings = server.stderr();
    assert_eq!(warnings.lines().count(), 1, "{warnings}");
    assert!(warnings.starts_with("Warning: listening on 0.0.0.0, which is not a loopback address"), "{warnings}");
    // The exact --host value is a valid Host; another name is not.
    let exact = format!("GET /health HTTP/1.1\r\nHost: 0.0.0.0:{}\r\n\r\n", server.port);
    assert_eq!(status(&server.raw(exact.as_bytes())), 200);
    let other = format!("GET /health HTTP/1.1\r\nHost: evil.example:{}\r\n\r\n", server.port);
    assert_eq!(status(&server.raw(other.as_bytes())), 421);
}

/// 1.2.0 started a thread for every connection with no limit.
#[test]
fn connections_are_capped() {
    let hub = Hub::new("cap");
    let dirs = dirs(&hub);
    let mut args: Vec<&str> = dirs.iter().map(String::as_str).collect();
    args.push("--no-auth");
    let server = HttpServer::start("serve", &args, &[("COORDINATIONHUB_MAX_CONNECTIONS", "3")]);
    // Three connections that send nothing hold the three slots.
    let idle: Vec<_> = (0..3).map(|_| server.connect()).collect();
    std::thread::sleep(Duration::from_millis(300));
    let response = server.get("/health", &[]);
    assert_eq!(status(&response), 503, "{response}");
    assert!(response.contains("Retry-After: 1"), "{response}");
    assert!(error_of(&response).contains("server busy: 3 connections are open"), "{response}");
    // Closing one frees its slot.
    drop(idle);
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        if status(&server.get("/health", &[])) == 200 {
            break;
        }
        assert!(Instant::now() < deadline, "the slots were never freed");
        std::thread::sleep(Duration::from_millis(100));
    }
}

/// One local client used to be able to hold every slot: 260 sockets that
/// each sent one byte kept `/health` at 503 for 30 seconds. A head now has
/// five seconds to arrive, so the slots come back by themselves.
#[test]
fn slow_heads_give_their_slots_back_within_seconds() {
    let hub = Hub::new("slow-heads");
    let dirs = dirs(&hub);
    let mut args: Vec<&str> = dirs.iter().map(String::as_str).collect();
    args.push("--no-auth");
    let server = HttpServer::start("serve", &args, &[("COORDINATIONHUB_MAX_CONNECTIONS", "3")]);
    let started = Instant::now();
    let mut slow: Vec<_> = (0..3)
        .map(|_| {
            let mut stream = server.connect();
            stream.write_all(b"G").unwrap();
            stream
        })
        .collect();
    std::thread::sleep(Duration::from_millis(300));
    assert_eq!(status(&server.get("/health", &[])), 503, "the slots are not all held");
    // The slow sockets stay open and silent. The server lets go of them.
    loop {
        if status(&server.get("/health", &[])) == 200 {
            break;
        }
        assert!(
            started.elapsed() < Duration::from_secs(12),
            "/health was still refused {:?} after the slow sockets connected",
            started.elapsed()
        );
        std::thread::sleep(Duration::from_millis(200));
    }
    assert!(started.elapsed() >= Duration::from_secs(4), "the slots came back before the head deadline");
    // The dashboard page is served again too, and each slow socket was
    // told why it was dropped.
    assert_eq!(status(&server.get("/", &[])), 200);
    for stream in &mut slow {
        let mut response = String::new();
        let _ = stream.read_to_string(&mut response);
        assert_eq!(status(&response), 408, "{response}");
        assert!(error_of(&response).contains("did not arrive within 5 seconds"), "{response}");
    }
}

/// A head whose lines end with a bare line feed never contains the blank
/// CRLF line the server waits for; it used to get no reply and hold its
/// connection until the deadline. It is answered 400 at once.
#[test]
fn a_head_with_bare_line_feeds_is_refused_at_once() {
    let hub = Hub::new("bare-lf");
    let server = open_api(&hub);
    for request in [
        format!("GET /health HTTP/1.1\nHost: {}\n\n", server.host()),
        format!("GET /health HTTP/1.1\r\nHost: {}\n\r\n", server.host()),
        "GET /health HTTP/1.1\n".to_string(),
    ] {
        let started = Instant::now();
        let response = server.raw(request.as_bytes());
        assert_eq!(status(&response), 400, "{request:?}: {response}");
        assert!(error_of(&response).contains("bare line feed"), "{response}");
        assert!(started.elapsed() < Duration::from_secs(3), "{request:?} took {:?}", started.elapsed());
    }
    // A line feed inside a body is not the head's business.
    let body = "{\"tool\":\"status\",\n\"arguments\":{}}";
    assert_eq!(status(&server.call(body, &[])), 200);
}

// ---------------------------------------------------------------- A5

fn post_head(server: &HttpServer, length: usize) -> String {
    format!(
        "POST /call HTTP/1.1\r\nHost: {}\r\nContent-Type: application/json\r\nContent-Length: {length}\r\n\r\n",
        server.host()
    )
}

/// A whole POST /call request for `body`.
fn post(server: &HttpServer, body: &str) -> String {
    format!("{}{body}", post_head(server, body.len()))
}

/// 1.2.0 took the body to be the text after the first blank line of one
/// 64 KB read, so a body with a blank line in it was cut and one that
/// arrived in two packets could be corrupted.
#[test]
fn bodies_are_read_as_bytes_to_their_exact_length() {
    let hub = Hub::new("bodies");
    let server = open_api(&hub);
    // A blank line inside the body.
    let spaced = "{\"tool\":\"register_agent\",\r\n\r\n\"arguments\":{\"agent_id\":\"blank-line\"}}";
    let response = server.call(spaced, &[]);
    assert_eq!(status(&response), 200, "{response}");
    // A multi-byte character split across two writes, and the head too.
    let name = "crew-\u{1f419}-\u{e9}";
    let text = format!(r#"{{"tool":"register_agent","arguments":{{"agent_id":"{name}"}}}}"#);
    let head = post_head(&server, text.len());
    let cut = text.find('\u{1f419}').unwrap() + 2;
    let mut stream = server.connect();
    for part in [&head.as_bytes()[..25], &head.as_bytes()[25..], &text.as_bytes()[..cut], &text.as_bytes()[cut..]] {
        stream.write_all(part).unwrap();
        stream.flush().unwrap();
        std::thread::sleep(Duration::from_millis(120));
    }
    let mut response = String::new();
    stream.read_to_string(&mut response).unwrap();
    assert_eq!(status(&response), 200, "{response}");
    let registered = reply(&response);
    assert_eq!(
        registered.get("result").and_then(|result| result.get("agent_id")).and_then(Value::as_str),
        Some(name),
        "{response}"
    );
    // Close to the size cap, in one body.
    let padded = format!(r#"{{"tool":"heartbeat",{}"arguments":{{"agent_id":"blank-line"}}}}"#, " ".repeat(900_000));
    let response = server.call(&padded, &[]);
    assert_eq!(status(&response), 200, "{}", &response[..response.len().min(300)]);
    // Bytes after Content-Length are not part of the body.
    let exact = r#"{"tool":"heartbeat","arguments":{"agent_id":"blank-line"}}"#;
    let response = server.raw(format!("{}{exact}trailing junk", post_head(&server, exact.len())).as_bytes());
    assert_eq!(status(&response), 200, "{response}");
    let mut names = agents(&server, &[]);
    names.sort();
    assert_eq!(names, ["blank-line", name]);
}

#[test]
fn malformed_requests_get_a_status_that_says_why() {
    let hub = Hub::new("malformed");
    let server = open_api(&hub);
    let call = r#"{"tool":"status"}"#;
    let host = server.host();
    let cases: Vec<(String, u16, &str)> = vec![
        // No Content-Length.
        (
            format!("POST /call HTTP/1.1\r\nHost: {host}\r\nContent-Type: application/json\r\n\r\n{call}"),
            411,
            "needs a Content-Length",
        ),
        (
            format!("POST /call HTTP/1.1\r\nHost: {host}\r\nContent-Type: application/json\r\nContent-Length: ten\r\n\r\n{call}"),
            400,
            "Content-Length must be a whole number",
        ),
        (
            format!("POST /call HTTP/1.1\r\nHost: {host}\r\nContent-Type: application/json\r\nContent-Length: 17\r\nContent-Length: 3\r\n\r\n{call}"),
            400,
            "content-length header appears more than once",
        ),
        // Larger than the cap: refused from the header alone.
        (
            format!("POST /call HTTP/1.1\r\nHost: {host}\r\nContent-Type: application/json\r\nContent-Length: 1000001\r\n\r\n"),
            413,
            "Request body exceeds 1000000 bytes",
        ),
        (
            format!("POST /call HTTP/1.1\r\nHost: {host}\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n"),
            501,
            "Transfer-Encoding is not supported",
        ),
        (format!("GET\r\nHost: {host}\r\n\r\n"), 400, "malformed request line"),
        (format!("GET /health HTTP/1.1\r\nHost: {host}\r\nno colon here\r\n\r\n"), 400, "malformed header line"),
        (format!("GET /nowhere HTTP/1.1\r\nHost: {host}\r\n\r\n"), 404, "Unknown endpoint: /nowhere"),
        (post(&server, "[1, 2, 3]"), 400, "Invalid JSON: the request body must be a JSON object"),
        (post(&server, "{\"a\":}"), 400, "Invalid JSON: unexpected JSON byte '}'"),
        (post(&server, "{\"tool\": \"\"}"), 400, "Missing 'tool' field"),
        (post(&server, r#"{"tool":"status","arguments":[1,2]}"#), 400, "'arguments' must be a JSON object"),
        (post(&server, r#"{"tool":"no_such_tool_at_all"}"#), 404, "Unknown tool:"),
    ];
    for (request, code, why) in cases {
        let response = server.raw(request.as_bytes());
        assert_eq!(status(&response), code, "{request:?}: {response}");
        assert!(error_of(&response).contains(why), "{request:?}: {response}");
    }
    // A body shorter than Content-Length, then the client stops sending.
    let mut stream = server.connect();
    stream.write_all(format!("{}{{\"tool\":", post_head(&server, 500)).as_bytes()).unwrap();
    stream.shutdown(Shutdown::Write).unwrap();
    let mut response = String::new();
    stream.read_to_string(&mut response).unwrap();
    assert_eq!(status(&response), 400, "{response}");
    assert!(error_of(&response).contains("the body ended after 8 of the 500 bytes"), "{response}");
    // A body that is not UTF-8.
    let mut request = post_head(&server, 4).into_bytes();
    request.extend([b'"', 0xff, 0xfe, b'"']);
    let response = server.raw(&request);
    assert_eq!(status(&response), 400, "{response}");
    assert_eq!(error_of(&response), "the request body is not UTF-8");
    // A head past 64 KiB.
    let huge = format!("GET /health HTTP/1.1\r\nHost: {host}\r\nX-Padding: {}\r\n\r\n", "a".repeat(70_000));
    let response = server.raw(huge.as_bytes());
    assert_eq!(status(&response), 431, "{response}");
    // And after all of that the server still answers.
    assert_eq!(status(&server.get("/health", &[])), 200);
}

// ---------------------------------------------------------------- A1, A2 over HTTP

/// 1.2.0: an authenticated 20 KB `/call` nested 10,000 deep killed the
/// whole `serve-sse` process.
#[test]
fn a_deeply_nested_call_is_a_400_and_the_server_lives() {
    let hub = Hub::new("deep-http");
    let mut server = api(&hub);
    let auth = bearer();
    for depth in [129, 10_000, 100_000] {
        let nested = format!("{}{}", "[".repeat(depth), "]".repeat(depth));
        let response = server.call(&format!(r#"{{"tool":"status","arguments":{{"x":{nested}}}}}"#), &[&auth]);
        assert_eq!(status(&response), 400, "depth {depth}: {response}");
        assert!(error_of(&response).contains("nested deeper than 128 levels"), "{response}");
        assert!(server.alive(), "the server died at depth {depth}");
    }
    // The deepest body that parses goes through the schema check and the
    // writers on a connection thread (a 2 MB stack).
    let register = server.call(r#"{"tool":"register_agent","arguments":{"agent_id":"a"}}"#, &[&auth]);
    assert_eq!(status(&register), 200);
    // body(1) > arguments(2) > payload: 125 objects (3 to 127), one array (128).
    let nested = format!("{}[]{}", r#"{"k":"#.repeat(125), "}".repeat(125));
    let deep = format!(
        r#"{{"tool":"send_message","arguments":{{"from_agent_id":"a","to_agent_id":"a","message_type":"deep","payload":{nested}}}}}"#
    );
    let response = server.call(&deep, &[&auth]);
    assert_eq!(status(&response), 200, "{}", &response[..response.len().min(400)]);
    // Numbers that are not finite are refused here too.
    let response = server.call(
        r#"{"tool":"acquire_lock","arguments":{"document_path":"x.txt","agent_id":"a","ttl":1e400}}"#,
        &[&auth],
    );
    assert_eq!(status(&response), 400, "{response}");
    assert!(error_of(&response).contains("too large for a 64-bit float"), "{response}");
    let response = server.call(
        r#"{"tool":"acquire_lock","arguments":{"document_path":"x.txt","agent_id":"a","ttl":1e300}}"#,
        &[&auth],
    );
    assert_eq!(status(&response), 400, "{response}");
    assert!(error_of(&response).contains("ttl must be a finite number between -1e15 and 1e15"), "{response}");
    assert!(server.alive());
}

// ---------------------------------------------------------------- A7

/// 1.2.0 on Windows: the token was the clock (`4458f13d2b2be818` followed
/// by sixteen zeros).
#[test]
fn the_generated_token_is_random_and_required() {
    let hub = Hub::new("token");
    let (first, token) = dashboard(&hub);
    let (_second, other) = dashboard(&Hub::new("token-2"));
    for token in [&token, &other] {
        assert_eq!(token.len(), 32, "{token}");
        assert!(token.bytes().all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f')), "{token}");
        assert!(!token.ends_with("00000000"), "the tail of {token} is not random");
        assert!(!token.starts_with("00000000"), "the head of {token} is not random");
    }
    assert_ne!(token, other);
    // Two tokens made in the same instant share no half (a clock would).
    assert_ne!(token[..16], other[..16]);
    assert_ne!(token[16..], other[16..]);
    // Every protected path needs exactly this token.
    for path in ["/tools", "/api/dashboard-data", "/events"] {
        assert_eq!(status(&first.get(path, &[])), 401, "{path}");
        for wrong in [
            format!("Authorization: Bearer {other}"),
            format!("Authorization: Bearer {}", &token[..31]),
            format!("Authorization: Bearer {token}0"),
            format!("Authorization: Basic {token}"),
            format!("Authorization: {token}"),
            "Authorization: Bearer".to_string(),
        ] {
            let response = first.get(path, &[&wrong]);
            assert_eq!(status(&response), 401, "{path} with {wrong}: {response}");
            assert!(response.contains("WWW-Authenticate: Bearer"), "{response}");
        }
    }
    for right in [format!("Authorization: Bearer {token}"), format!("authorization: bearer {token}")] {
        assert_eq!(status(&first.get("/tools", &[&right])), 200, "{right}");
        assert_eq!(status(&first.get("/api/dashboard-data", &[&right])), 200, "{right}");
    }
    // /health stays open and says nothing secret.
    let health = first.get("/health", &[]);
    assert_eq!(status(&health), 200);
    assert!(!health.contains(&token));
}

// ---------------------------------------------------------------- A8

/// 1.2.0: `curl /` with no credentials returned the token in a meta tag,
/// and the page had lost its doctype.
#[test]
fn the_page_never_holds_the_token() {
    let hub = Hub::new("page");
    let (mut server, token) = dashboard(&hub);
    let response = server.get("/", &[]);
    assert_eq!(status(&response), 200, "{response}");
    let page = body(&response).replace("\r\n", "\n");
    assert!(page.starts_with("<!DOCTYPE html>\n<html lang=\"en\">"), "{}", &page[..page.len().min(80)]);
    assert!(page.trim_end().ends_with("</html>"));
    assert!(!response.contains(&token), "the page or its headers hold the token");
    assert!(!page.contains("coordhub-token\" content"), "the token meta tag is back");
    assert_eq!(page, PAGE.replace("\r\n", "\n"), "the page is served as it is in assets/");
    for header in [
        "Content-Type: text/html; charset=utf-8",
        "Content-Security-Policy: default-src 'none'",
        "connect-src 'self'",
        "frame-ancestors 'none'",
        "X-Content-Type-Options: nosniff",
        "Cache-Control: no-store",
        "Referrer-Policy: no-referrer",
    ] {
        assert!(response.contains(header), "missing {header}: {}", &response[..response.len().min(700)]);
    }
    // The banner gives the URL to open: the token is in the fragment.
    let line = server.line_with("Dashboard: ", Duration::from_secs(5)).unwrap();
    assert_eq!(line, format!("Dashboard: http://127.0.0.1:{}/#token={token}", server.port));
    // With --no-auth there is no token and no fragment.
    let dirs = dirs(&Hub::new("page-open"));
    let mut args: Vec<&str> = dirs.iter().map(String::as_str).collect();
    args.extend(["--no-browser", "--no-auth"]);
    let mut open = HttpServer::start("serve-sse", &args, &[]);
    let line = open.line_with("Dashboard: ", Duration::from_secs(5)).unwrap();
    assert_eq!(line, format!("Dashboard: http://127.0.0.1:{}/", open.port));
    assert_eq!(status(&open.get("/api/dashboard-data", &[])), 200);
}

/// The event stream: one frame at once, a keepalive comment while nothing
/// changes, a new frame when something does.
#[test]
fn the_event_stream_sends_data_then_keepalives_then_changes() {
    let hub = Hub::new("events");
    let (server, token) = dashboard(&hub);
    let auth = format!("Authorization: Bearer {token}");
    let mut stream = server.connect();
    stream
        .write_all(format!("GET /events HTTP/1.1\r\nHost: {}\r\n{auth}\r\n\r\n", server.host()).as_bytes())
        .unwrap();
    let mut reader = BufReader::new(stream);
    let mut read_until = |needle: &str| -> String {
        let deadline = Instant::now() + Duration::from_secs(20);
        let mut seen = String::new();
        while Instant::now() < deadline {
            let mut line = String::new();
            if reader.read_line(&mut line).unwrap_or(0) == 0 {
                break;
            }
            seen.push_str(&line);
            if line.contains(needle) {
                return seen;
            }
        }
        panic!("no {needle:?} on the stream; got: {seen}");
    };
    let head = read_until("data: {");
    assert!(head.starts_with("HTTP/1.1 200 OK"), "{head}");
    assert!(head.contains("Content-Type: text/event-stream"), "{head}");
    assert!(!head.contains("fresh-agent"));
    read_until(": keepalive");
    let registered = server.call(r#"{"tool":"register_agent","arguments":{"agent_id":"fresh-agent"}}"#, &[&auth]);
    assert_eq!(status(&registered), 200, "{registered}");
    let frame = read_until("fresh-agent");
    assert!(frame.contains("data: {"), "{frame}");
}

// --- the page itself

const PAGE: &str = include_str!("../assets/dashboard.html");

fn luminance(hex: &str) -> f64 {
    let channel = |at: usize| {
        let value = u8::from_str_radix(&hex[at..at + 2], 16).unwrap() as f64 / 255.0;
        if value <= 0.03928 {
            value / 12.92
        } else {
            ((value + 0.055) / 1.055).powf(2.4)
        }
    };
    0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5)
}

/// WCAG contrast ratio of two `#rrggbb` colours.
fn contrast(one: &str, other: &str) -> f64 {
    let (a, b) = (luminance(one), luminance(other));
    (a.max(b) + 0.05) / (a.min(b) + 0.05)
}

/// Every `#rrggbb` that follows `marker` in the page.
fn colours_after(marker: &str) -> Vec<String> {
    let mut found = Vec::new();
    let mut rest = PAGE;
    while let Some(at) = rest.find(marker) {
        let before = rest[..at].chars().next_back();
        rest = &rest[at + marker.len()..];
        // `color:` only, not `background-color:` or `border-color:`.
        if before == Some('-') {
            continue;
        }
        let value = rest.trim_start();
        if value.starts_with('#') && value.len() >= 7 && value[1..7].bytes().all(|byte| byte.is_ascii_hexdigit()) {
            let colour = value[..7].to_ascii_lowercase();
            if !found.contains(&colour) {
                found.push(colour);
            }
        }
    }
    found
}

#[test]
fn the_page_is_readable_and_labelled() {
    // The two greys the audit measured, and why they failed.
    assert!(contrast("#4b5563", "#1a1d27") < 2.3);
    assert!(contrast("#6b7280", "#1a1d27") < 3.5);
    for old in ["#4b5563", "#6b7280"] {
        assert!(!PAGE.contains(old), "{old} is still used");
    }
    // Text colours: the CSS `color:` values and the fills of SVG text.
    let mut text = colours_after("color:");
    text.extend(colours_after("fill=\""));
    assert!(text.len() >= 6, "{text:?}");
    // The surfaces text sits on: the page, a panel, a card.
    let surfaces = ["#0f1117", "#1a1d27", "#1e212d"];
    for colour in &text {
        if surfaces.contains(&colour.as_str()) {
            continue; // dark text, used on the amber banner only (below)
        }
        for surface in surfaces {
            let ratio = contrast(colour, surface);
            assert!(ratio >= 4.5, "{colour} on {surface} is {ratio:.2}:1");
        }
    }
    // Badges, chips and the banner have surfaces of their own.
    for (colour, surface) in [
        ("#94a3b8", "#1e293b"),
        ("#4ade80", "#1c2b1e"),
        ("#f87171", "#2d1e1e"),
        ("#9ca3af", "#2a2d3a"),
        ("#94a3b8", "#1e212d"),
        ("#1a1d27", "#fbbf24"),
    ] {
        assert!(PAGE.contains(colour) && PAGE.contains(surface), "{colour} on {surface} is not in the page");
        assert!(contrast(colour, surface) >= 4.5, "{colour} on {surface}");
    }
    // No font below 12 px, in CSS or in SVG attributes.
    let mut rest = PAGE;
    while let Some(at) = rest.find("font-size") {
        rest = &rest[at + "font-size".len()..];
        let digits: String = rest
            .trim_start_matches([':', '=', '"', ' '])
            .chars()
            .take_while(|ch| ch.is_ascii_digit() || *ch == '.')
            .collect();
        let size: f64 = digits.parse().unwrap_or_else(|_| panic!("font-size without a number: {}", &rest[..20]));
        assert!(size >= 12.0, "a {size} px font");
    }
    // Landmarks and names.
    assert!(PAGE.contains("<main class=\"dashboard\">") && PAGE.contains("</main>"));
    for label in ["aria-label=\"Zoom out\"", "aria-label=\"Fit tree to view\"", "aria-label=\"Zoom in\""] {
        assert_eq!(PAGE.matches(label).count(), 1, "{label}");
    }
    assert!(PAGE.contains("id=\"notice\" role=\"status\""));
    // One self-contained file: no scripts, styles, fonts or images from
    // anywhere else.
    for outside in ["src=", "href=", "url(", "@import", "//cdn", "http://", "https://"] {
        assert!(!PAGE.contains(outside), "the page loads something: {outside}");
    }
    // How it talks to the server.
    assert!(!PAGE.contains("new EventSource"), "EventSource cannot send the token");
    for needed in ["'Bearer ' + token", "sessionStorage", "replaceState", "getReader()", "/dashboard-data", "'/events'"] {
        assert!(PAGE.contains(needed), "the page script lost {needed}");
    }
}

/// `node`, when it is installed and new enough to have `fetch` and
/// streams (18+). The page test is skipped without it; CI has it.
fn node() -> Option<&'static str> {
    let out = Command::new("node").arg("--version").output().ok()?;
    let version = String::from_utf8_lossy(&out.stdout);
    let major: u32 = version.trim().trim_start_matches('v').split('.').next()?.parse().ok()?;
    (major >= 18).then_some("node")
}

/// Runs the page's own script (tests/common/dashboard_harness.js) against
/// `server` and feeds each state snapshot to `check` until it says stop
/// (true) or `seconds` pass. Returns whether `check` was satisfied.
fn run_page(host: &str, fragment: &str, seconds: u32, mut check: impl FnMut(&Value) -> bool) -> bool {
    let harness = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/common/dashboard_harness.js");
    let mut child = Command::new("node")
        .arg(harness)
        .arg(format!("http://{host}"))
        .arg(if fragment.is_empty() { "-" } else { fragment })
        .arg(seconds.to_string())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .unwrap();
    let mut satisfied = false;
    for line in BufReader::new(child.stdout.take().unwrap()).lines() {
        let Ok(line) = line else { break };
        let snapshot = json::parse(&line).unwrap_or_else(|err| panic!("{err}: {line}"));
        if check(&snapshot) {
            satisfied = true;
            break;
        }
    }
    let _ = child.kill();
    let _ = child.wait();
    satisfied
}

fn text_of<'a>(snapshot: &'a Value, key: &str) -> &'a str {
    snapshot.get(key).and_then(Value::as_str).unwrap_or("")
}

/// 1.2.0: with default auth the dashboard never showed data, because the
/// page sent no credentials. This runs the page's script for real.
#[test]
fn the_page_shows_data_with_auth_on() {
    if node().is_none() {
        eprintln!("skipped: node 18+ is not installed, so the page script cannot be run here");
        return;
    }
    let hub = Hub::new("page-live");
    assert_eq!(hub.run(&["register", "pilot"]).code, 0);
    assert_eq!(hub.run(&["create-task", "task-7", "pilot", "wire the dashboard"]).code, 0);
    let (mut server, token) = dashboard(&hub);
    let host = server.host();

    // Opened with the URL from the banner: live data, the token out of
    // the address bar and kept for the tab, a bearer header on every call.
    let fragment = format!("#token={token}");
    let mut last = Value::Null;
    let live = run_page(&host, &fragment, 20, |snapshot| {
        last = snapshot.clone();
        text_of(snapshot, "timestamp").contains("(live)") && text_of(snapshot, "tasks").contains("task-7")
    });
    assert!(live, "the page never showed live data: {}", json::dumps(&last));
    assert_eq!(last.get("noticeHidden"), Some(&json::b(true)), "{}", json::dumps(&last));
    assert_eq!(text_of(&last, "hash"), "", "the token stayed in the address bar");
    assert_eq!(text_of(&last, "stored"), token);
    let requests = last.get("requests").and_then(Value::as_array).unwrap();
    assert!(!requests.is_empty());
    for request in requests {
        assert_eq!(text_of(request, "authorization"), format!("Bearer {token}"), "{}", json::dumps(request));
    }

    // Opened without the token: no data, and a banner that says what to do.
    let mut last = Value::Null;
    let told = run_page(&host, "", 20, |snapshot| {
        last = snapshot.clone();
        text_of(snapshot, "notice").contains("needs its access token")
    });
    assert!(told, "no token banner: {}", json::dumps(&last));
    assert!(text_of(&last, "notice").contains("serve-sse"), "{}", json::dumps(&last));
    assert_eq!(text_of(&last, "tasks"), "");
    assert_eq!(last.get("noticeHidden"), Some(&json::b(false)));

    // A wrong token is told apart from a missing one.
    let mut last = Value::Null;
    let told = run_page(&host, "#token=0123456789abcdef0123456789abcdef", 20, |snapshot| {
        last = snapshot.clone();
        text_of(snapshot, "notice").contains("refused this page")
    });
    assert!(told, "no refused-token banner: {}", json::dumps(&last));

    // The server goes away while the page is open: a visible banner with
    // the time of the last update, and the old data still on the page.
    let mut stopped = false;
    let mut last = Value::Null;
    let stale = run_page(&host, &fragment, 40, |snapshot| {
        last = snapshot.clone();
        if !stopped && text_of(snapshot, "tasks").contains("task-7") {
            server.stop();
            stopped = true;
        }
        stopped && text_of(snapshot, "notice").contains("Not connected to the server")
    });
    assert!(stale, "no stale banner: {}", json::dumps(&last));
    let notice = text_of(&last, "notice");
    assert!(notice.contains("was last updated at") && notice.contains("may be out of date"), "{notice}");
    assert!(text_of(&last, "timestamp").contains("(stale)"), "{}", json::dumps(&last));
    assert!(text_of(&last, "tasks").contains("task-7"));
}

/// With `--no-auth` the page works with no token at all.
#[test]
fn the_page_shows_data_with_auth_off() {
    if node().is_none() {
        eprintln!("skipped: node 18+ is not installed, so the page script cannot be run here");
        return;
    }
    let hub = Hub::new("page-open-live");
    assert_eq!(hub.run(&["register", "pilot"]).code, 0);
    assert_eq!(hub.run(&["acquire-lock", "notes.md", "pilot"]).code, 0);
    let dirs = dirs(&hub);
    let mut args: Vec<&str> = dirs.iter().map(String::as_str).collect();
    args.extend(["--no-browser", "--no-auth"]);
    let server = HttpServer::start("serve-sse", &args, &[]);
    let mut last = Value::Null;
    let live = run_page(&server.host(), "", 20, |snapshot| {
        last = snapshot.clone();
        text_of(snapshot, "timestamp").contains("(live)") && text_of(snapshot, "locks").contains("notes.md")
    });
    assert!(live, "the page never showed live data: {}", json::dumps(&last));
    assert_eq!(last.get("noticeHidden"), Some(&json::b(true)));
    for request in last.get("requests").and_then(Value::as_array).unwrap() {
        assert_eq!(request.get("authorization"), Some(&Value::Null));
    }
}
