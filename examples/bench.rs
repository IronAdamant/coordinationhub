//! Time Python 0.8.0 and Rust through the same MCP stdio server.
//!
//! Usage: cargo run --release --example bench -- [RUST_BINARY] [--python EXE]
//!
//! Neither side is patched. Exits 1 when Rust is not strictly faster on a
//! row. Copy the printed rows into BENCHMARKS.md. Do not invent numbers.
//! COORDINATIONHUB_BENCH_LOOPS (default 100) and COORDINATIONHUB_BENCH_RUNS
//! (default 3) set the sizes; each row is the median of the runs.

use coordinationhub::json::{self, Value};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::time::Instant;

struct Server {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
    n: i64,
}

impl Server {
    fn start(mut command: Command) -> Server {
        let mut child = command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .unwrap_or_else(|err| panic!("cannot start {command:?}: {err}"));
        let stdin = child.stdin.take().unwrap();
        let stdout = BufReader::new(child.stdout.take().unwrap());
        Server { child, stdin, stdout, n: 0 }
    }

    fn call(&mut self, method: &str, params: Option<Value>) {
        self.n += 1;
        let mut pairs = vec![
            ("jsonrpc", json::s("2.0")),
            ("id", json::i(self.n)),
            ("method", json::s(method)),
        ];
        if let Some(params) = params {
            pairs.push(("params", params));
        }
        writeln!(self.stdin, "{}", json::dumps(&json::obj(pairs))).unwrap();
        self.stdin.flush().unwrap();
        let mut line = String::new();
        if self.stdout.read_line(&mut line).unwrap_or(0) == 0 {
            panic!("closed during {method}");
        }
    }

    fn tool(&mut self, name: &str, arguments: Value) {
        self.call(
            "tools/call",
            Some(json::obj(vec![("name", json::s(name)), ("arguments", arguments)])),
        );
    }

    fn initialize(&mut self) {
        self.call(
            "initialize",
            Some(json::obj(vec![
                ("protocolVersion", json::s("2024-11-05")),
                ("capabilities", json::obj(vec![])),
                ("clientInfo", json::obj(vec![("name", json::s("bench")), ("version", json::s("0"))])),
            ])),
        );
    }

    fn close(mut self) {
        drop(self.stdin);
        let _ = self.child.wait();
    }
}

fn agent(id: &str) -> Value {
    json::obj(vec![("agent_id", json::s(id))])
}

fn child(id: &str, parent: &str) -> Value {
    json::obj(vec![("agent_id", json::s(id)), ("parent_id", json::s(parent))])
}

type Work = fn(&mut Server, usize);

/// Time `work` on a fresh server. `setup` runs first and is not timed.
fn once(command: Command, loops: usize, setup: Option<Work>, work: Work) -> f64 {
    let mut server = Server::start(command);
    if let Some(setup) = setup {
        setup(&mut server, loops);
    }
    let start = Instant::now();
    work(&mut server, loops);
    let elapsed = start.elapsed().as_secs_f64();
    server.close();
    elapsed
}

fn median(mut samples: Vec<f64>) -> f64 {
    samples.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let mid = samples.len() / 2;
    if samples.len() % 2 == 1 {
        samples[mid]
    } else {
        (samples[mid - 1] + samples[mid]) / 2.0
    }
}

fn startup(server: &mut Server, _loops: usize) {
    server.initialize();
    server.tool("status", json::obj(vec![]));
}

/// The verify-4 workload: 450 agents registered flat under one parent.
fn status_450_setup(server: &mut Server, _loops: usize) {
    server.initialize();
    server.tool("register_agent", agent("p0"));
    for n in 1..450 {
        server.tool("register_agent", child(&format!("p{n}"), "p0"));
    }
}

fn status_450(server: &mut Server, _loops: usize) {
    for _ in 0..450 {
        server.tool("status", json::obj(vec![]));
    }
}

fn registers(server: &mut Server, loops: usize) {
    server.initialize();
    for n in 0..loops {
        server.tool("register_agent", agent(&format!("a{n}")));
    }
}

fn locks(server: &mut Server, loops: usize) {
    server.initialize();
    server.tool("register_agent", agent("holder"));
    for n in 0..loops {
        server.tool(
            "acquire_lock",
            json::obj(vec![("document_path", json::s(format!("f{n}.txt"))), ("agent_id", json::s("holder"))]),
        );
    }
}

fn tree(server: &mut Server, loops: usize) {
    server.initialize();
    server.tool("register_agent", agent("root"));
    for n in 0..loops {
        server.tool("register_agent", child(&format!("c{n}"), "root"));
    }
    for _ in 0..20 {
        server.tool("get_agent_tree", json::obj(vec![]));
    }
}

fn legacy_ref(root: &Path) -> Option<String> {
    ["python-legacy", "origin/python-legacy"].into_iter().find_map(|reference| {
        Command::new("git")
            .args(["rev-parse", "--verify", reference])
            .current_dir(root)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .ok()
            .filter(|status| status.success())
            .map(|_| reference.to_string())
    })
}

fn extract_legacy(root: &Path, dest: &Path) {
    let Some(reference) = legacy_ref(root) else {
        eprintln!("python-legacy is not available");
        std::process::exit(1);
    };
    let archive = dest.with_extension("tar");
    let ok = Command::new("git")
        .args(["archive", "--format=tar", "-o"])
        .arg(&archive)
        .arg(&reference)
        .current_dir(root)
        .status()
        .map(|status| status.success())
        .unwrap_or(false);
    std::fs::create_dir_all(dest).unwrap();
    let ok = ok
        && Command::new("tar")
            .arg("-xf")
            .arg(&archive)
            .arg("-C")
            .arg(dest)
            .status()
            .map(|status| status.success())
            .unwrap_or(false);
    if !ok {
        eprintln!("could not extract python-legacy");
        std::process::exit(1);
    }
}

fn env_usize(name: &str, default: usize) -> usize {
    std::env::var(name).ok().and_then(|v| v.parse().ok()).unwrap_or(default)
}

fn main() {
    let loops = env_usize("COORDINATIONHUB_BENCH_LOOPS", 100);
    let runs = env_usize("COORDINATIONHUB_BENCH_RUNS", 3);
    let mut args = std::env::args().skip(1);
    let mut binary: Option<PathBuf> = None;
    let mut python = std::env::var("PARITY_PYTHON").unwrap_or_else(|_| {
        if cfg!(windows) { "python".into() } else { "python3".into() }
    });
    while let Some(arg) = args.next() {
        if arg == "--python" {
            python = args.next().expect("--python needs a value");
        } else {
            binary = Some(PathBuf::from(arg));
        }
    }
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let binary = binary.unwrap_or_else(|| {
        root.join("target").join("release").join(format!("coordinationhub{}", std::env::consts::EXE_SUFFIX))
    });
    let tmp = std::env::temp_dir().join(format!(
        "chub-bench-{}-{}",
        std::process::id(),
        std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()
    ));
    std::fs::create_dir_all(&tmp).unwrap();
    let legacy = tmp.join("py");
    extract_legacy(&root, &legacy);
    let mut rows: Vec<Value> = Vec::new();
    let mut all_faster = true;
    let mut counter = 0;
    let mut measure = |name: String, setup: Option<Work>, work: Work| {
        let mut samples_py = Vec::new();
        let mut samples_rs = Vec::new();
        for _ in 0..runs {
            counter += 1;
            let py_dir = tmp.join(format!("s{counter}py"));
            let rs_dir = tmp.join(format!("s{counter}rs"));
            std::fs::create_dir_all(&py_dir).unwrap();
            std::fs::create_dir_all(&rs_dir).unwrap();
            let mut py = Command::new(&python);
            py.args(["-m", "coordinationhub", "serve-mcp", "--storage-dir"]).arg(&py_dir);
            let mut rs = Command::new(&binary);
            rs.args(["serve-mcp", "--storage-dir"]).arg(&rs_dir);
            for command in [&mut py, &mut rs] {
                command
                    .env_remove("COORDINATIONHUB_NOW")
                    .env_remove("COORDINATIONHUB_PID")
                    .env("COORDINATIONHUB_HOUSEKEEPING", "0")
                    .env("PYTHONPATH", &legacy);
            }
            samples_py.push(once(py, loops, setup, work));
            samples_rs.push(once(rs, loops, setup, work));
        }
        let py = median(samples_py);
        let rs = median(samples_rs);
        let faster = rs < py;
        all_faster &= faster;
        let round4 = |x: f64| (x * 10_000.0).round() / 10_000.0;
        rows.push(json::obj(vec![
            ("name", json::s(&name)),
            ("python_s", json::f(round4(py))),
            ("rust_s", json::f(round4(rs))),
            ("rust_faster", json::b(faster)),
        ]));
        println!(
            "{name}: python {py:.4}s rust {rs:.4}s faster={}",
            if faster { "True" } else { "False" }
        );
    };
    measure("startup_and_status".into(), None, startup);
    measure("status_450".into(), Some(status_450_setup), status_450);
    measure(format!("register_x{loops}"), None, registers);
    measure(format!("lock_x{loops}"), None, locks);
    measure(format!("tree_x20_after_{loops}"), None, tree);
    println!(
        "{}",
        json::dumps(&json::obj(vec![
            ("loops", json::i(loops as i64)),
            ("runs", json::i(runs as i64)),
            ("rows", json::arr(rows)),
        ]))
    );
    let _ = std::fs::remove_dir_all(&tmp);
    std::process::exit(if all_faster { 0 } else { 1 });
}
