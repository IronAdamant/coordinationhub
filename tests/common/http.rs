//! A `serve` / `serve-sse` child on a port the system picks.
//!
//! Tests pass `--port 0` and read the port from the banner, so two test
//! runs on one machine never fight over a fixed port.
#![allow(dead_code)]

use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{channel, Receiver};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

pub struct HttpServer {
    child: Child,
    pub port: u16,
    lines: Receiver<String>,
    /// Every stdout line read so far, banner first.
    pub seen: Vec<String>,
    errors: Arc<Mutex<String>>,
}

impl HttpServer {
    /// Starts `coordinationhub <command> --port 0 <args>` and waits for the
    /// banner line that names the port.
    pub fn start(command: &str, args: &[&str], env: &[(&str, &str)]) -> HttpServer {
        Self::try_start(command, args, env).unwrap_or_else(|err| panic!("server did not start: {err}"))
    }

    /// As `start`, but a server that exits before its banner is an `Err`
    /// holding what it wrote to stderr.
    pub fn try_start(command: &str, args: &[&str], env: &[(&str, &str)]) -> Result<HttpServer, String> {
        let mut cmd = Command::new(env!("CARGO_BIN_EXE_coordinationhub"));
        cmd.arg(command)
            .args(["--port", "0"])
            .args(args)
            .env("COORDINATIONHUB_HOUSEKEEPING", "0")
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        for (key, value) in env {
            cmd.env(key, value);
        }
        let mut child = cmd.spawn().map_err(|err| err.to_string())?;
        let stdout = child.stdout.take().expect("piped stdout");
        let (sender, lines) = channel();
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines() {
                let Ok(line) = line else { break };
                if sender.send(line).is_err() {
                    break;
                }
            }
        });
        // stderr is collected as it comes, so a warning can be read while
        // the server runs and a refusal after it exits.
        let errors = Arc::new(Mutex::new(String::new()));
        let stderr = child.stderr.take().expect("piped stderr");
        let sink = errors.clone();
        let reading = std::thread::spawn(move || {
            for line in BufReader::new(stderr).lines() {
                let Ok(line) = line else { break };
                let mut text = sink.lock().unwrap();
                text.push_str(&line);
                text.push('\n');
            }
        });
        let mut server = HttpServer { child, port: 0, lines, seen: Vec::new(), errors };
        match server.line_with("Starting ", Duration::from_secs(30)) {
            Some(line) => {
                let digits: String = line
                    .rsplit(':')
                    .next()
                    .unwrap_or("")
                    .chars()
                    .take_while(char::is_ascii_digit)
                    .collect();
                server.port = digits.parse().map_err(|_| format!("no port in banner line: {line}"))?;
                Ok(server)
            }
            None => {
                let _ = server.child.kill();
                let _ = server.child.wait();
                let _ = reading.join();
                Err(server.stderr())
            }
        }
    }

    /// Everything the server has written to stderr so far.
    pub fn stderr(&self) -> String {
        self.errors.lock().unwrap().clone()
    }

    /// Stops the server (as Ctrl+C would) and waits for it to exit.
    pub fn stop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }

    /// The first stdout line containing `needle`, waiting up to `limit`.
    pub fn line_with(&mut self, needle: &str, limit: Duration) -> Option<String> {
        if let Some(line) = self.seen.iter().find(|line| line.contains(needle)) {
            return Some(line.clone());
        }
        let deadline = Instant::now() + limit;
        loop {
            let left = deadline.checked_duration_since(Instant::now())?;
            let line = self.lines.recv_timeout(left).ok()?;
            self.seen.push(line.clone());
            if line.contains(needle) {
                return Some(line);
            }
        }
    }

    /// `127.0.0.1:<port>`, the Host header a local client sends.
    pub fn host(&self) -> String {
        format!("127.0.0.1:{}", self.port)
    }

    pub fn connect(&self) -> TcpStream {
        let stream = TcpStream::connect(("127.0.0.1", self.port)).expect("connect to the test server");
        stream.set_read_timeout(Some(Duration::from_secs(20))).unwrap();
        stream
    }

    /// Sends raw request bytes and returns everything the server answers
    /// until it closes the connection.
    pub fn raw(&self, request: &[u8]) -> String {
        let mut stream = self.connect();
        let _ = stream.write_all(request);
        let mut out = Vec::new();
        let _ = stream.read_to_end(&mut out);
        String::from_utf8_lossy(&out).into_owned()
    }

    /// A GET with the right Host header plus `headers` (each `Name: value`).
    pub fn get(&self, path: &str, headers: &[&str]) -> String {
        let mut request = format!("GET {path} HTTP/1.1\r\nHost: {}\r\n", self.host());
        for header in headers {
            request.push_str(header);
            request.push_str("\r\n");
        }
        request.push_str("\r\n");
        self.raw(request.as_bytes())
    }

    /// A JSON POST to `/call` with the right Host and Content-Type.
    pub fn call(&self, body: &str, headers: &[&str]) -> String {
        let mut request = format!(
            "POST /call HTTP/1.1\r\nHost: {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\n",
            self.host(),
            body.len()
        );
        for header in headers {
            request.push_str(header);
            request.push_str("\r\n");
        }
        request.push_str("\r\n");
        request.push_str(body);
        self.raw(request.as_bytes())
    }

    /// True while the server process has not exited.
    pub fn alive(&mut self) -> bool {
        matches!(self.child.try_wait(), Ok(None))
    }
}

impl Drop for HttpServer {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

/// The status code of a raw HTTP response, 0 when there is none.
pub fn status(response: &str) -> u16 {
    response
        .split_whitespace()
        .nth(1)
        .and_then(|code| code.parse().ok())
        .unwrap_or(0)
}

/// The body of a raw HTTP response.
pub fn body(response: &str) -> &str {
    response.split_once("\r\n\r\n").map(|(_, body)| body).unwrap_or("")
}
