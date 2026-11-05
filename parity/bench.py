#!/usr/bin/env python3
"""Time Python 0.8.0 and Rust through the same MCP stdio server.

Neither side is patched. Exits 1 when Rust is not strictly faster on a row.
Copy the printed rows into BENCHMARKS.md. Do not invent numbers.
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOOPS = int(os.environ.get("COORDINATIONHUB_BENCH_LOOPS", "100"))
RUNS = int(os.environ.get("COORDINATIONHUB_BENCH_RUNS", "3"))


def extract_legacy(dest: Path) -> None:
    for ref in ("python-legacy", "origin/python-legacy"):
        probe = subprocess.run(
            ["git", "rev-parse", "--verify", ref],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if probe.returncode == 0:
            archive = subprocess.check_output(["git", "archive", ref], cwd=ROOT)
            subprocess.run(["tar", "-x", "-C", str(dest)], input=archive, check=True)
            return
    raise SystemExit("python-legacy is not available")


class Server:
    def __init__(self, argv: list[str], env: dict[str, str]) -> None:
        self.proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env=env,
            text=True,
        )
        self.n = 0

    def call(self, method: str, params: dict | None = None) -> None:
        self.n += 1
        message = {"jsonrpc": "2.0", "id": self.n, "method": method}
        if params is not None:
            message["params"] = params
        assert self.proc.stdin and self.proc.stdout
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()
        if not self.proc.stdout.readline():
            raise RuntimeError(f"closed during {method}")

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        self.proc.wait(timeout=5)


def once(argv: list[str], env: dict, work) -> float:
    server = Server(argv, env)
    try:
        start = time.perf_counter()
        work(server)
        return time.perf_counter() - start
    finally:
        server.close()


def median(samples: list[float]) -> float:
    return statistics.median(samples)


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "target/release/coordinationhub"
    with tempfile.TemporaryDirectory() as tmp:
        legacy = Path(tmp) / "py"
        legacy.mkdir()
        extract_legacy(legacy)
        env = os.environ.copy()
        env.pop("COORDINATIONHUB_NOW", None)
        env.pop("COORDINATIONHUB_PID", None)
        env["COORDINATIONHUB_HOUSEKEEPING"] = "0"
        env["PYTHONPATH"] = str(legacy)
        rows = []

        def measure(name: str, work) -> None:
            samples = {"python": [], "rust": []}
            for _ in range(RUNS):
                py_dir = tempfile.mkdtemp(dir=tmp)
                rs_dir = tempfile.mkdtemp(dir=tmp)
                py_argv = [sys.executable, "-m", "coordinationhub", "serve-mcp", "--storage-dir", py_dir]
                rs_argv = [binary, "serve-mcp", "--storage-dir", rs_dir]
                samples["python"].append(once(py_argv, env, work))
                samples["rust"].append(once(rs_argv, env, work))
            py = median(samples["python"])
            rs = median(samples["rust"])
            rows.append({
                "name": name,
                "python_s": round(py, 4),
                "rust_s": round(rs, 4),
                "rust_faster": rs < py,
            })
            print(f"{name}: python {py:.4f}s rust {rs:.4f}s faster={rs < py}", flush=True)

        def startup(server: Server) -> None:
            server.call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "bench", "version": "0"}})
            server.call("tools/call", {"name": "status", "arguments": {}})

        def registers(server: Server) -> None:
            server.call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "bench", "version": "0"}})
            for n in range(LOOPS):
                server.call("tools/call", {"name": "register_agent", "arguments": {"agent_id": f"a{n}"}})

        def locks(server: Server) -> None:
            server.call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "bench", "version": "0"}})
            server.call("tools/call", {"name": "register_agent", "arguments": {"agent_id": "holder"}})
            for n in range(LOOPS):
                server.call("tools/call", {"name": "acquire_lock", "arguments": {"document_path": f"f{n}.txt", "agent_id": "holder"}})

        def tree(server: Server) -> None:
            server.call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "bench", "version": "0"}})
            server.call("tools/call", {"name": "register_agent", "arguments": {"agent_id": "root"}})
            for n in range(LOOPS):
                server.call("tools/call", {"name": "register_agent", "arguments": {"agent_id": f"c{n}", "parent_id": "root"}})
            for _ in range(20):
                server.call("tools/call", {"name": "get_agent_tree", "arguments": {}})

        def status_450(server: Server) -> None:
            server.call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "bench", "version": "0"}})
            server.call("tools/call", {"name": "register_agent", "arguments": {"agent_id": "p0"}})
            parent = "p0"
            for n in range(1, 450):
                agent = f"p{n}"
                server.call("tools/call", {"name": "register_agent", "arguments": {"agent_id": agent, "parent_id": parent}})
                parent = agent
            for _ in range(450):
                server.call("tools/call", {"name": "status", "arguments": {}})

        measure("startup_and_status", startup)
        measure("status_450", status_450)
        measure(f"register_x{LOOPS}", registers)
        measure(f"lock_x{LOOPS}", locks)
        measure(f"tree_x20_after_{LOOPS}", tree)
        print(json.dumps({"loops": LOOPS, "runs": RUNS, "rows": rows}))
        if any(not row["rust_faster"] for row in rows):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
