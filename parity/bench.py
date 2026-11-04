#!/usr/bin/env python3
"""Same-runner timings for the Python 0.8.0 tree and the Rust binary.

Prints one JSON object. Exits 1 when any Rust row is not strictly faster.
Numbers come from this process. Do not paste them into BENCHMARKS.md by hand
from memory; run this script and copy the table.
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOOPS = int(os.environ.get("COORDINATIONHUB_BENCH_LOOPS", "200"))
RUNS = int(os.environ.get("COORDINATIONHUB_BENCH_RUNS", "3"))


def extract_legacy(dest: Path) -> None:
    for ref in ("python-legacy", "origin/python-legacy"):
        probe = subprocess.run(["git", "rev-parse", "--verify", ref], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if probe.returncode == 0:
            archive = subprocess.check_output(["git", "archive", ref], cwd=ROOT)
            subprocess.run(["tar", "-x", "-C", str(dest)], input=archive, check=True)
            return
    raise SystemExit("python-legacy is not available")


def median(samples: list[float]) -> float:
    return statistics.median(samples)


def startup(command: list[str], env: dict) -> float:
    samples = []
    for _ in range(7):
        begin = time.perf_counter()
        subprocess.run(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        samples.append(time.perf_counter() - begin)
    return median(samples)


def rust_rows(binary: Path, loops: int) -> dict[str, float]:
    work = Path(tempfile.mkdtemp(prefix="bench-rs-"))
    proc = subprocess.run(
        [str(binary), "bench", "--loops", str(loops), "--storage-dir", str(work)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True,
    )
    payload = json.loads(proc.stdout)
    return {row["name"]: row["seconds"] for row in payload["rows"]}


def python_rows(legacy: Path, loops: int) -> dict[str, float]:
    sys.path.insert(0, str(legacy))
    from coordinationhub.core import CoordinationEngine
    from coordinationhub.dispatch import dispatch_tool

    store = Path(tempfile.mkdtemp(prefix="bench-py-"))
    project = Path(tempfile.mkdtemp(prefix="bench-py-proj-"))
    engine = CoordinationEngine(storage_dir=store, project_root=project, housekeeping=False)
    engine.start()

    def call(tool, arguments):
        return dispatch_tool(engine, tool, arguments)

    def timed(fn) -> float:
        begin = time.perf_counter()
        fn()
        return time.perf_counter() - begin

    rows = {}
    rows["register"] = timed(lambda: [call("register_agent", {"agent_id": f"p.{n}"}) for n in range(loops)])
    rows["lock_acquire_release"] = timed(lambda: [
        (
            call("acquire_lock", {"document_path": f"f{n}.txt", "agent_id": "p.0"}),
            call("release_lock", {"document_path": f"f{n}.txt", "agent_id": "p.0"}),
        )
        for n in range(loops)
    ])
    rows["notification_fanout"] = timed(lambda: [
        call("notify_change", {"document_path": f"n{n}.txt", "change_type": "modified", "agent_id": "p.0"})
        for n in range(loops)
    ])
    rows["status"] = timed(lambda: [call("status", {}) for _ in range(loops)])
    rows["many_agent_scale"] = timed(lambda: (
        [call("register_agent", {"agent_id": f"scale.{n}"}) for n in range(1000)],
        call("status", {}),
        call("list_agents", {"active_only": True}),
    ))

    def contend():
        for name in ("c0", "c1", "c2", "c3"):
            call("register_agent", {"agent_id": name})
        errors = []

        def worker(agent):
            for _ in range(40):
                try:
                    call("acquire_lock", {"document_path": "contended.txt", "agent_id": agent})
                except Exception as exc:
                    errors.append(str(exc))

        threads = [threading.Thread(target=worker, args=(agent,)) for agent in ("c0", "c1", "c2", "c3")]
        begin = time.perf_counter()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        if errors:
            raise SystemExit(errors[0])
        return time.perf_counter() - begin

    rows["contended_locks"] = contend()
    engine.close()
    return rows


def main() -> int:
    binary = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "target" / "release" / "coordinationhub").resolve()
    if not binary.is_file():
        raise SystemExit(f"missing binary {binary}")
    legacy = Path(tempfile.mkdtemp(prefix="bench-legacy-"))
    extract_legacy(legacy)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(legacy)
    py_startup_cmd = [
        sys.executable, "-c",
        "from pathlib import Path\n"
        "from coordinationhub.core import CoordinationEngine\n"
        "e=CoordinationEngine(storage_dir=Path('/tmp/chub-bench-py-start'), project_root=Path('/tmp'), housekeeping=False)\n"
        "e.start(); e.status(); e.close()\n",
    ]
    rs_startup_cmd = [str(binary), "status", "--storage-dir", "/tmp/chub-bench-rs-start", "--project-root", "/tmp", "--json"]
    py_samples = [python_rows(legacy, LOOPS) for _ in range(RUNS)]
    rs_samples = [rust_rows(binary, LOOPS) for _ in range(RUNS)]
    names = ["startup", "register", "lock_acquire_release", "contended_locks", "notification_fanout", "status", "many_agent_scale"]
    table = []
    failed = []
    py_start = startup(py_startup_cmd, env)
    rs_start = startup(rs_startup_cmd, env)
    measured = {"startup": (py_start, rs_start)}
    for name in names:
        if name == "startup":
            py, rs = py_start, rs_start
        else:
            py = median([sample[name] for sample in py_samples])
            rs = median([sample[name] for sample in rs_samples])
        measured[name] = (py, rs)
        faster = rs < py
        if not faster:
            failed.append(name)
        table.append({
            "operation": name,
            "loops": 7 if name == "startup" else (1000 if name == "many_agent_scale" else (160 if name == "contended_locks" else LOOPS)),
            "python_seconds": py,
            "rust_seconds": rs,
            "rust_faster": faster,
        })
    print(json.dumps({"loops": LOOPS, "runs": RUNS, "rows": table, "failed": failed}, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
