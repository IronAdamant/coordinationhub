#!/usr/bin/env python3
"""Same-runner benchmark of the Python hub and the Rust hub.

Prints a table and, with --check, exits 1 if any Rust row is slower.
"""

from __future__ import annotations

import json
import os
import socket
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def post(port: int, tool: str, arguments: dict) -> None:
    body = json.dumps({"tool": tool, "arguments": arguments}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/call",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        resp.read()


def wait_health(port: int) -> None:
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as resp:
                if resp.status == 200:
                    return
        except Exception:
            time.sleep(0.05)
    raise SystemExit(f"port {port} not ready")


def python_root() -> Path:
    env = os.environ.get("COORDINATIONHUB_PYTHON_ROOT")
    if env:
        return Path(env)
    if (ROOT / "coordinationhub" / "__init__.py").is_file():
        return ROOT
    cache = ROOT / "parity" / ".cache" / "python-legacy"
    if not (cache / "coordinationhub" / "__init__.py").is_file():
        subprocess.check_call(
            ["git", "worktree", "add", "--detach", str(cache), "origin/python-legacy"],
            cwd=ROOT,
        )
    return cache


def time_call(fn, repeats: int = 7) -> float:
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000.0)
    return statistics.median(samples)


def startup_ms(cmd: list[str], cwd: Path, env: dict) -> float:
    def once():
        subprocess.check_call(cmd, cwd=cwd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    return time_call(once, repeats=5)


def main() -> int:
    check = "--check" in sys.argv
    py_root = python_root()
    rust = os.environ.get("COORDINATIONHUB_RUST_BIN", str(ROOT / "target" / "release" / "coordinationhub"))
    work = Path("/tmp/coordhub-bench")
    subprocess.check_call(["rm", "-rf", str(work)])
    project = work / "project"
    project.mkdir(parents=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(py_root)
    env["COORDINATIONHUB_COORDINATION_URL"] = "http://localhost:9877"
    py_store = work / "py"
    rs_store = work / "rs"
    py_port = free_port()
    rs_port = free_port()
    common = ["--no-auth", "--host", "127.0.0.1", "--project-root", str(project)]
    py_proc = subprocess.Popen(
        [sys.executable, "-m", "coordinationhub", "serve", *common, "--port", str(py_port), "--storage-dir", str(py_store)],
        cwd=project, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    rs_proc = subprocess.Popen(
        [rust, "serve", *common, "--port", str(rs_port), "--storage-dir", str(rs_store)],
        cwd=project, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    rows = []
    try:
        wait_health(py_port)
        wait_health(rs_port)
        py_status_cmd = [sys.executable, "-m", "coordinationhub", "status", "-j", "--storage-dir", str(work / "py-cold"), "--project-root", str(project)]
        rs_status_cmd = [rust, "status", "-j", "--storage-dir", str(work / "rs-cold"), "--project-root", str(project)]
        rows.append(("startup status", startup_ms(py_status_cmd, project, env), startup_ms(rs_status_cmd, project, env)))

        def reg(port, n):
            post(port, "register_agent", {"agent_id": f"agent-{n}"})

        rows.append(("register", time_call(lambda: reg(py_port, "py")), time_call(lambda: reg(rs_port, "rs"))))

        post(py_port, "register_agent", {"agent_id": "lock-py"})
        post(rs_port, "register_agent", {"agent_id": "lock-rs"})

        def lock_cycle(port, agent, i):
            post(port, "acquire_lock", {"document_path": f"f{i}.py", "agent_id": agent})
            post(port, "release_lock", {"document_path": f"f{i}.py", "agent_id": agent})

        i = {"n": 0}

        def next_i():
            i["n"] += 1
            return i["n"]

        rows.append((
            "lock acquire/release",
            time_call(lambda: lock_cycle(py_port, "lock-py", f"py{next_i()}")),
            time_call(lambda: lock_cycle(rs_port, "lock-rs", f"rs{next_i()}")),
        ))

        post(py_port, "register_agent", {"agent_id": "hold-py"})
        post(rs_port, "register_agent", {"agent_id": "hold-rs"})
        post(py_port, "acquire_lock", {"document_path": "held.py", "agent_id": "hold-py"})
        post(rs_port, "acquire_lock", {"document_path": "held.py", "agent_id": "hold-rs"})
        rows.append((
            "contended lock",
            time_call(lambda: post(py_port, "acquire_lock", {"document_path": "held.py", "agent_id": "lock-py"})),
            time_call(lambda: post(rs_port, "acquire_lock", {"document_path": "held.py", "agent_id": "lock-rs"})),
        ))

        def fanout(port, agent):
            for k in range(20):
                post(port, "notify_change", {"document_path": f"n{k}.py", "change_type": "modified", "agent_id": agent})
            post(port, "get_notifications", {"limit": 50})

        rows.append(("notifications fan-out", time_call(lambda: fanout(py_port, "lock-py"), 3), time_call(lambda: fanout(rs_port, "lock-rs"), 3)))

        def batch_status(port):
            for _ in range(25):
                post(port, "status", {})

        def batch_dash(port):
            for _ in range(25):
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/dashboard-data", timeout=10).read()

        rows.append(("status query x25", time_call(lambda: batch_status(py_port), 5), time_call(lambda: batch_status(rs_port), 5)))
        rows.append(("dashboard query x25", time_call(lambda: batch_dash(py_port), 5), time_call(lambda: batch_dash(rs_port), 5)))

        def scale(port, prefix):
            for n in range(40):
                post(port, "register_agent", {"agent_id": f"{prefix}-{n}", "parent_id": "hold-py" if port == py_port else "hold-rs"})
            post(port, "status", {})

        rows.append(("many-agent scale", time_call(lambda: scale(py_port, "py-scale"), 1), time_call(lambda: scale(rs_port, "rs-scale"), 1)))
    finally:
        py_proc.terminate()
        rs_proc.terminate()
        py_proc.wait(timeout=5)
        rs_proc.wait(timeout=5)

    print(f"{'operation':<24} {'python ms':>12} {'rust ms':>12} {'faster':>8}")
    failed = False
    for name, py_ms, rs_ms in rows:
        flag = "rust" if rs_ms < py_ms else "PYTHON"
        if rs_ms >= py_ms:
            failed = True
        print(f"{name:<24} {py_ms:12.3f} {rs_ms:12.3f} {flag:>8}")
    out = ROOT / "parity" / "bench-last.json"
    out.write_text(json.dumps([{"op": n, "python_ms": p, "rust_ms": r} for n, p, r in rows], indent=2) + "\n")
    if check and failed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
