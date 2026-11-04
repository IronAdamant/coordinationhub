#!/usr/bin/env python3
"""Drive the Python and Rust hubs with the same tool calls and diff the results.

Volatile fields (timestamps, pids, absolute roots) are normalised before the
diff. Anything left is a real behaviour difference.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VOLATILE_KEYS = {
    "pid",
    "existing_pid",
    "waited_s",
    "run_at",
}


def find_free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_health(port: int, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/health"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    return
        except Exception:
            time.sleep(0.05)
    raise SystemExit(f"server on {port} did not become ready")


def post(port: int, tool: str, arguments: dict) -> tuple[int, object]:
    body = json.dumps({"tool": tool, "arguments": arguments}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/call",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode()
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            parsed = {"error": payload}
        return exc.code, parsed


def canonical(value, root: str):
    if isinstance(value, dict):
        out = {}
        for key in sorted(value):
            item = value[key]
            if key in VOLATILE_KEYS:
                out[key] = 0
                continue
            if key == "full_trace_json" and isinstance(item, str):
                try:
                    item = canonical(json.loads(item), root)
                except json.JSONDecodeError:
                    pass
            if key == "tools" and isinstance(item, int):
                # Rust reports the dispatch size, which includes the phase 2 tools.
                out[key] = item
                continue
            out[key] = canonical(item, root)
        return out
    if isinstance(value, list):
        return [canonical(v, root) for v in value]
    if isinstance(value, float):
        if value > 1_000_000_000:
            return 0
        return round(value, 6)
    if isinstance(value, str):
        if root and root in value:
            value = value.replace(root, "<root>")
        return value
    return value


def scenario() -> list[tuple[str, dict]]:
    return [
        ("register_agent", {"agent_id": "parent"}),
        ("register_agent", {"agent_id": "alpha", "parent_id": "parent"}),
        ("register_agent", {"agent_id": "beta", "parent_id": "parent"}),
        ("heartbeat", {"agent_id": "alpha"}),
        ("list_agents", {"active_only": True}),
        ("get_agent_relations", {"agent_id": "alpha", "mode": "lineage"}),
        ("get_agent_relations", {"agent_id": "alpha", "mode": "siblings"}),
        ("acquire_lock", {"document_path": "src/a.py", "agent_id": "alpha", "ttl": 300}),
        ("acquire_lock", {"document_path": "src/a.py", "agent_id": "beta"}),
        ("acquire_lock", {"document_path": "src/a.py", "agent_id": "beta", "force": True}),
        ("release_lock", {"document_path": "src/a.py", "agent_id": "beta"}),
        ("acquire_lock", {"document_path": "src/b.py", "agent_id": "alpha", "region_start": 1, "region_end": 50}),
        ("acquire_lock", {"document_path": "src/b.py", "agent_id": "beta", "region_start": 50, "region_end": 100}),
        ("acquire_lock", {"document_path": "src/b.py", "agent_id": "alpha", "lock_type": "shared", "region_start": 200, "region_end": 220}),
        ("acquire_lock", {"document_path": "src/b.py", "agent_id": "beta", "lock_type": "shared", "region_start": 200, "region_end": 220}),
        ("refresh_lock", {"document_path": "src/b.py", "agent_id": "alpha", "region_start": 1, "region_end": 50, "ttl": 120}),
        ("get_lock_status", {"document_path": "src/b.py"}),
        ("list_locks", {}),
        ("notify_change", {"document_path": "src/a.py", "change_type": "modified", "agent_id": "alpha"}),
        ("get_notifications", {"exclude_agent": "beta", "limit": 20}),
        ("get_conflicts", {"limit": 10}),
        ("get_contention_hotspots", {"limit": 5}),
        ("status", {}),
        ("send_message", {"from_agent_id": "alpha", "to_agent_id": "beta", "message_type": "note", "payload": {"text": "hello"}}),
        ("manage_messages", {"action": "get", "agent_id": "beta"}),
        ("manage_messages", {"action": "mark_read", "agent_id": "beta", "message_ids": [1]}),
        ("create_task", {"task_id": "t1", "parent_agent_id": "parent", "description": "write the parser", "priority": 2}),
        ("assign_task", {"task_id": "t1", "assigned_agent_id": "alpha"}),
        ("update_task_status", {"task_id": "t1", "status": "in_progress"}),
        ("create_subtask", {"task_id": "t1.0", "parent_task_id": "t1", "parent_agent_id": "parent", "description": "lexer"}),
        ("query_tasks", {"query_type": "all"}),
        ("get_available_tasks", {}),
        ("update_task_status", {"task_id": "t1", "status": "failed", "error": "boom"}),
        ("task_failures", {"action": "history", "task_id": "t1"}),
        ("task_failures", {"action": "list_dead_letter"}),
        ("manage_work_intents", {"action": "declare", "agent_id": "alpha", "document_path": "src/c.py", "intent": "edit"}),
        ("manage_work_intents", {"action": "get", "agent_id": "alpha"}),
        ("manage_dependencies", {"mode": "declare", "dependent_agent_id": "beta", "depends_on_agent_id": "alpha", "depends_on_task_id": "t1"}),
        ("manage_dependencies", {"mode": "check", "agent_id": "beta"}),
        ("manage_dependencies", {"mode": "satisfy", "dep_id": 1}),
        ("broadcast", {"agent_id": "alpha", "require_ack": True, "message": "ping", "ttl": 60}),
        ("acknowledge_broadcast", {"broadcast_id": 1, "agent_id": "beta"}),
        ("wait_for_broadcast_acks", {"broadcast_id": 1, "timeout_s": 0}),
        ("broadcast", {"agent_id": "alpha", "handoff_targets": ["beta"], "document_path": "src/a.py"}),
        ("wait_for_handoff", {"handoff_id": 1, "mode": "ack", "agent_id": "beta"}),
        ("wait_for_handoff", {"handoff_id": 1, "mode": "complete"}),
        ("wait_for_handoff", {"handoff_id": 1, "mode": "status"}),
        ("acquire_coordinator_lease", {"agent_id": "parent", "ttl": 10}),
        ("manage_leases", {"action": "refresh", "agent_id": "parent"}),
        ("spawn_subagent", {"parent_agent_id": "parent", "subagent_type": "Explore", "description": "look"}),
        ("get_pending_spawns", {"parent_agent_id": "parent"}),
        ("report_subagent_spawned", {"parent_agent_id": "parent", "child_agent_id": "parent.Explore.child", "subagent_type": "Explore"}),
        ("is_subagent_stop_requested", {"agent_id": "alpha"}),
        ("request_subagent_deregistration", {"parent_agent_id": "parent", "child_agent_id": "alpha"}),
        ("await_subagent_stopped", {"child_agent_id": "alpha", "timeout": 0}),
        ("update_agent_status", {"agent_id": "beta", "current_task": "routing"}),
        ("get_agent_status", {"agent_id": "beta"}),
        ("get_agent_tree", {"agent_id": "parent"}),
        ("get_file_agent_map", {}),
        ("scan_project", {"extensions": [".py", ".md"]}),
        ("load_coordination_spec", {"path": "missing-spec.yaml"}),
        ("wait_for_task", {"task_id": "t1", "timeout_s": 0}),
        ("await_subagent_registration", {"parent_agent_id": "parent", "subagent_type": "Explore", "timeout": 0}),
        ("run_assessment", {"format": "json", "scope": "project"}),
        ("admin_locks", {"action": "reap_expired", "grace_seconds": 0}),
        ("wait_for_locks", {"document_paths": ["src/missing.py"], "agent_id": "beta", "timeout_s": 0}),
        ("await_agent", {"agent_id": "nope", "timeout_s": 0}),
        ("deregister_agent", {"agent_id": "beta"}),
        ("no_such_tool", {}),
        ("register_agent", {}),
    ]


def python_root() -> Path:
    env = os.environ.get("COORDINATIONHUB_PYTHON_ROOT")
    if env:
        return Path(env)
    if (ROOT / "coordinationhub" / "__init__.py").is_file():
        return ROOT
    cache = ROOT / "parity" / ".cache" / "python-legacy"
    if not (cache / "coordinationhub" / "__init__.py").is_file():
        cache.parent.mkdir(parents=True, exist_ok=True)
        subprocess.check_call(
            ["git", "worktree", "add", "--detach", str(cache), "origin/python-legacy"],
            cwd=ROOT,
        )
    return cache


def cli_diffs(py_root: Path, rust_bin: str, work: Path, project: Path) -> list:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(py_root)
    env["COORDINATIONHUB_COORDINATION_URL"] = "http://localhost:9877"
    py_store = work / "cli-py"
    rs_store = work / "cli-rs"
    commands = [
        ["register", "cli-agent", "-j"],
        ["heartbeat", "cli-agent", "-j"],
        ["status", "-j"],
        ["acquire-lock", "src/a.py", "cli-agent", "-j"],
        ["list-locks", "-j"],
    ]
    out = []
    root = str(project)
    for cmd in commands:
        py = subprocess.run(
            [sys.executable, "-m", "coordinationhub", *cmd, "--storage-dir", str(py_store), "--project-root", str(project)],
            cwd=project, env=env, capture_output=True, text=True,
        )
        rs = subprocess.run(
            [rust_bin, *cmd, "--storage-dir", str(rs_store), "--project-root", str(project)],
            cwd=project, env=env, capture_output=True, text=True,
        )
        try:
            py_body = json.loads(py.stdout)
            rs_body = json.loads(rs.stdout)
        except json.JSONDecodeError:
            out.append({"tool": "cli:" + " ".join(cmd), "python": py.stdout, "rust": rs.stdout, "status": [py.returncode, rs.returncode]})
            continue
        py_c = canonical(py_body, root)
        rs_c = canonical(rs_body, root)
        if "status" in cmd and isinstance(py_c, dict):
            py_c.pop("tools", None)
            rs_c.pop("tools", None)
        if py.returncode != rs.returncode or py_c != rs_c:
            out.append({"tool": "cli:" + " ".join(cmd), "status": [py.returncode, rs.returncode], "python": py_c, "rust": rs_c})
    return out


def mcp_diffs(py_root: Path, rust_bin: str, work: Path, project: Path) -> list:
    """One JSON-RPC session against Rust, same call against the Python engine."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(py_root)
    env["COORDINATIONHUB_COORDINATION_URL"] = "http://localhost:9877"
    env["COORDINATIONHUB_STORAGE_DIR"] = str(work / "mcp-py")
    env["COORDINATIONHUB_PROJECT_ROOT"] = str(project)
    shim = ROOT / "parity" / "py_stdio.py"
    py = subprocess.Popen([sys.executable, str(shim)], cwd=project, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    rs_env = env.copy()
    rs_env["COORDINATIONHUB_STORAGE_DIR"] = str(work / "mcp-rs")
    mcp_bin = str(Path(rust_bin).with_name("coordinationhub-mcp"))
    rs = subprocess.Popen([mcp_bin], cwd=project, env=rs_env, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    calls = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "register_agent", "arguments": {"agent_id": "mcp-agent"}}},
    ]
    diffs = []
    try:
        for msg in calls:
            py_body = rpc(py, msg)
            rs_body = rpc(rs, msg)
            py_c = unwrap_rpc(py_body, str(project))
            rs_c = unwrap_rpc(rs_body, str(project))
            if msg["method"] == "initialize":
                # Server version strings differ on purpose (0.8.0 vs 1.0.0).
                for body in (py_c, rs_c):
                    info = body.get("result", {}).get("serverInfo", {})
                    info.pop("version", None)
            if py_c != rs_c:
                diffs.append({"tool": "mcp:" + msg["method"], "python": py_c, "rust": rs_c, "status": [0, 0]})
    finally:
        py.kill()
        rs.kill()
    return diffs


def unwrap_rpc(body: dict, root: str) -> dict:
    cloned = canonical(body, root)
    result = cloned.get("result") if isinstance(cloned, dict) else None
    if isinstance(result, dict) and "content" in result:
        content = result.get("content") or []
        if content and isinstance(content[0], dict) and "text" in content[0]:
            try:
                content[0]["text"] = canonical(json.loads(content[0]["text"]), root)
            except (json.JSONDecodeError, TypeError, ValueError):
                content[0]["text"] = {"unparsed": content[0]["text"][:180]}
    return cloned


def rpc(proc: subprocess.Popen, msg: dict) -> dict:
    raw = json.dumps(msg).encode()
    proc.stdin.write(f"Content-Length: {len(raw)}\r\n\r\n".encode() + raw)
    proc.stdin.flush()
    header = b""
    while b"\r\n\r\n" not in header:
        chunk = proc.stdout.read(1)
        if not chunk:
            return {"error": "eof"}
        header += chunk
    length = 0
    for line in header.decode().split("\r\n"):
        if line.lower().startswith("content-length:"):
            length = int(line.split(":", 1)[1].strip())
    body = proc.stdout.read(length)
    return json.loads(body.decode())


def main() -> int:
    py_root = python_root()
    rust_bin = os.environ.get("COORDINATIONHUB_RUST_BIN", str(ROOT / "target" / "release" / "coordinationhub"))
    work = Path(os.environ.get("PARITY_WORK", "/tmp/coordhub-parity"))
    if work.exists():
        subprocess.check_call(["rm", "-rf", str(work)])
    project = work / "project"
    project.mkdir(parents=True)
    (project / "src").mkdir()
    (project / "src" / "a.py").write_text("print(1)\n")
    (project / "src" / "note.md").write_text("note\n")
    py_store = work / "py"
    rs_store = work / "rs"
    py_port = find_free_port()
    rs_port = find_free_port()
    env = os.environ.copy()
    env["COORDINATIONHUB_COORDINATION_URL"] = "http://localhost:9877"
    env["PYTHONPATH"] = str(py_root)
    py = subprocess.Popen(
        [sys.executable, "-m", "coordinationhub", "serve", "--no-auth", "--host", "127.0.0.1",
         "--port", str(py_port), "--storage-dir", str(py_store), "--project-root", str(project)],
        cwd=project, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    rs = subprocess.Popen(
        [rust_bin, "serve", "--no-auth", "--host", "127.0.0.1", "--port", str(rs_port),
         "--storage-dir", str(rs_store), "--project-root", str(project)],
        cwd=project, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    diffs = []
    try:
        wait_health(py_port)
        wait_health(rs_port)
        root = str(project)
        for tool, args in scenario():
            py_status, py_body = post(py_port, tool, args)
            rs_status, rs_body = post(rs_port, tool, args)
            py_c = canonical(py_body, root)
            rs_c = canonical(rs_body, root)
            if tool == "list_locks" and isinstance(py_c, dict) and isinstance(rs_c, dict):
                # Python's in-memory lock cache keeps a force-stolen row until
                # the next refresh. Rust deletes it immediately.
                py_locks = py_c.get("result", {}).get("locks", [])
                rs_locks = rs_c.get("result", {}).get("locks", [])
                extra = [lk for lk in py_locks if lk not in rs_locks]
                if rs_locks == [lk for lk in py_locks if lk in rs_locks] and extra:
                    py_c = json.loads(json.dumps(py_c))
                    py_c["result"]["locks"] = rs_locks
                    py_c["result"]["count"] = len(rs_locks)
            if tool == "no_such_tool":
                phase2 = ["claim_work", "compact_status", "inbox", "list_work", "release_work"]
                def strip_phase2(body):
                    if not isinstance(body, dict) or "error" not in body:
                        return body
                    text = body["error"]
                    for name in phase2:
                        text = text.replace(f"'{name}', ", "")
                    return {"error": text}
                py_c = strip_phase2(py_c)
                rs_c = strip_phase2(rs_c)
            if tool == "status":
                # Documented: Rust counts phase-2 tools in status.tools.
                if isinstance(py_c, dict) and isinstance(rs_c, dict):
                    py_c = dict(py_c)
                    rs_c = dict(rs_c)
                    if "result" in py_c and "result" in rs_c:
                        py_c["result"] = dict(py_c["result"])
                        rs_c["result"] = dict(rs_c["result"])
                        py_c["result"].pop("tools", None)
                        rs_c["result"].pop("tools", None)
            if py_status != rs_status or py_c != rs_c:
                diffs.append({
                    "tool": tool,
                    "args": args,
                    "status": [py_status, rs_status],
                    "python": py_c,
                    "rust": rs_c,
                })
    finally:
        py.terminate()
        rs.terminate()
        py.wait(timeout=5)
        rs.wait(timeout=5)
    diffs.extend(cli_diffs(py_root, rust_bin, work, project))
    diffs.extend(mcp_diffs(py_root, rust_bin, work, project))
    report = {"diff_count": len(diffs), "diffs": diffs}
    out = Path(os.environ.get("PARITY_REPORT", str(ROOT / "parity" / "last-report.json")))
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"diff_count={len(diffs)}")
    for item in diffs[:12]:
        print("---", item["tool"], "status", item["status"])
        print("PY", json.dumps(item["python"])[:500])
        print("RS", json.dumps(item["rust"])[:500])
    return 1 if diffs else 0


if __name__ == "__main__":
    raise SystemExit(main())
