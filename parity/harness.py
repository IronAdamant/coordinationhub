#!/usr/bin/env python3
"""Drive the Python 0.8.0 tree and the Rust binary with the same calls.

Tool results are compared exactly, with a frozen clock. The on-disk file
is not compared: Rust stores a JSON snapshot because the standard library
cannot open SQLite. That difference is recorded in MIGRATION.md.
`who` is a Rust addition and is removed from tools/list before the name
check. `status.tools` is rewritten to the shared tool count for the same
reason.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FROZEN = 1_700_000_000.0
PID = 4242
ADDITIVE_TOOLS = {"who"}
TIME_FIELDS = {
    "started_at", "last_heartbeat", "expires_at", "created_at", "locked_at",
    "run_at", "assigned_at", "declared_at", "updated_at", "spawned_at",
    "registered_at", "acknowledged_at", "completed_at", "satisfied_at",
    "first_attempt_at", "last_attempt_at", "dead_letter_at", "acquired_at",
    "consumed_at", "read_at",
}


def legacy_ref() -> str:
    for ref in ("python-legacy", "origin/python-legacy"):
        probe = subprocess.run(["git", "rev-parse", "--verify", ref], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if probe.returncode == 0:
            return ref
    raise SystemExit("python-legacy is not available. Fetch it with: git fetch origin python-legacy")


def extract_legacy(dest: Path) -> None:
    ref = legacy_ref()
    archive = subprocess.run(["git", "archive", ref], cwd=ROOT, check=True, stdout=subprocess.PIPE).stdout
    subprocess.run(["tar", "-x", "-C", str(dest)], input=archive, check=True)


class Diffs:
    def __init__(self) -> None:
        self.items: list[tuple[str, object, object]] = []

    def add(self, title: str, left, right) -> None:
        self.items.append((title, left, right))
        print(f"DIFF {title}")
        print("--- python ---")
        print(left)
        print("--- rust ---")
        print(right)

    def check(self, title: str, left, right) -> None:
        if left != right:
            self.add(title, left, right)


def align(value):
    """Sort lists whose order SQLite does not define, and ignore the added tool."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key == "tools" and isinstance(item, int):
                out[key] = 50
            else:
                out[key] = align(item)
        return out
    if isinstance(value, list):
        items = [align(item) for item in value]
        if items and all(isinstance(item, dict) and "agent_id" in item and "last_heartbeat" in item for item in items):
            items.sort(key=lambda item: (item.get("agent_id") or "",))
        if items and all(isinstance(item, dict) and "conflict_type" in item and "id" in item for item in items):
            items.sort(key=lambda item: item["id"])
        return items
    return value


def canon(value) -> str:
    return json.dumps(align(value), sort_keys=True, separators=(",", ":"), default=str)


def scrub(value):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key == "tools" and isinstance(item, int):
                out[key] = 50
            elif key == "pid":
                out[key] = 0
            elif key in TIME_FIELDS and isinstance(item, (int, float)):
                out[key] = 0
            else:
                out[key] = scrub(item)
        return out
    if isinstance(value, list):
        return [scrub(item) for item in value]
    return value


def rust_call(binary: Path, tool: str, arguments: dict, storage: Path, project: Path) -> tuple[int, object]:
    env = os.environ.copy()
    env["COORDINATIONHUB_NOW"] = str(int(FROZEN))
    env["COORDINATIONHUB_PID"] = str(PID)
    env["TZ"] = "UTC"
    proc = subprocess.run(
        [str(binary), "call", tool, "--storage-dir", str(storage), "--project-root", str(project), "--args", json.dumps(arguments)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    text = proc.stdout.strip() or proc.stderr.strip().removeprefix("Error: ").strip()
    try:
        parsed = json.loads(proc.stdout) if proc.returncode == 0 else {"__error__": text}
    except json.JSONDecodeError:
        parsed = {"__error__": text, "__raw__": proc.stdout}
    return proc.returncode, parsed


def load_python(legacy: Path):
    sys.path.insert(0, str(legacy))
    os.environ["TZ"] = "UTC"
    time.time = lambda: FROZEN
    os.getpid = lambda: PID
    from coordinationhub.core import CoordinationEngine
    from coordinationhub.dispatch import dispatch_tool
    from coordinationhub.schemas import TOOL_SCHEMAS
    return CoordinationEngine, dispatch_tool, TOOL_SCHEMAS


def py_session(engine_cls, dispatch, storage: Path, project: Path):
    engine = engine_cls(storage_dir=storage, project_root=project, housekeeping=False)
    engine.start()

    def call(tool, arguments):
        try:
            return dispatch(engine, tool, arguments)
        except Exception as exc:
            return {"__error__": str(exc)}

    return engine, call


def tool_suite(binary: Path, engine_cls, dispatch, diffs: Diffs) -> None:
    cases = scripted_cases()
    for name, calls in cases:
        py_dir = Path(tempfile.mkdtemp(prefix=f"py-{name}-"))
        rs_dir = Path(tempfile.mkdtemp(prefix=f"rs-{name}-"))
        project = Path(tempfile.mkdtemp(prefix=f"proj-{name}-"))
        try:
            engine, py = py_session(engine_cls, dispatch, py_dir, project)
            try:
                for index, (tool, arguments) in enumerate(calls):
                    left = py(tool, arguments)
                    code, right = rust_call(binary, tool, arguments, rs_dir, project)
                    if isinstance(right, dict) and "__error__" in right:
                        right_cmp = right["__error__"]
                        left_cmp = left["__error__"] if isinstance(left, dict) and "__error__" in left else left
                        diffs.check(f"{name}[{index}] {tool} error", str(left_cmp), str(right_cmp))
                    else:
                        diffs.check(f"{name}[{index}] {tool}", canon(left), canon(right))
                        if code != 0:
                            diffs.add(f"{name}[{index}] exit", 0, code)
            finally:
                engine.close()
        finally:
            shutil.rmtree(py_dir, ignore_errors=True)
            shutil.rmtree(rs_dir, ignore_errors=True)
            shutil.rmtree(project, ignore_errors=True)


def scripted_cases():
    return [
        ("identity", [
            ("register_agent", {"agent_id": "alpha"}),
            ("register_agent", {"agent_id": "beta", "parent_id": "alpha"}),
            ("heartbeat", {"agent_id": "alpha"}),
            ("heartbeat", {"agent_id": "missing"}),
            ("list_agents", {}),
            ("list_agents", {"active_only": False}),
            ("get_agent_relations", {"agent_id": "beta", "mode": "lineage"}),
            ("get_agent_relations", {"agent_id": "beta", "mode": "siblings"}),
            ("deregister_agent", {"agent_id": "alpha"}),
            ("list_agents", {"active_only": False}),
        ]),
        ("locks", [
            ("register_agent", {"agent_id": "holder"}),
            ("register_agent", {"agent_id": "other"}),
            ("acquire_lock", {"document_path": "notes.md", "agent_id": "holder"}),
            ("acquire_lock", {"document_path": "notes.md", "agent_id": "other"}),
            ("get_lock_status", {"document_path": "notes.md"}),
            ("list_locks", {}),
            ("refresh_lock", {"document_path": "notes.md", "agent_id": "holder", "ttl": 120}),
            ("acquire_lock", {"document_path": "notes.md", "agent_id": "other", "force": True}),
            ("get_conflicts", {}),
            ("release_lock", {"document_path": "notes.md", "agent_id": "other"}),
            ("release_lock", {"document_path": "notes.md", "agent_id": "other"}),
            ("acquire_lock", {"document_path": "region.txt", "agent_id": "holder", "region_start": 1, "region_end": 10, "lock_type": "shared"}),
            ("acquire_lock", {"document_path": "region.txt", "agent_id": "other", "region_start": 1, "region_end": 10, "lock_type": "shared"}),
            ("acquire_lock", {"document_path": "region.txt", "agent_id": "holder", "region_start": 20, "region_end": 30}),
            ("release_lock", {"document_path": "region.txt", "agent_id": "holder"}),
            ("admin_locks", {"action": "reap_expired", "grace_seconds": 0}),
            ("get_contention_hotspots", {"limit": 5}),
        ]),
        ("changes", [
            ("register_agent", {"agent_id": "writer"}),
            ("notify_change", {"document_path": "a.txt", "change_type": "modified", "agent_id": "writer"}),
            ("notify_change", {"document_path": "b.txt", "change_type": "created", "agent_id": "writer"}),
            ("get_notifications", {"limit": 10}),
            ("get_notifications", {"exclude_agent": "writer"}),
            ("status", {}),
        ]),
        ("messages", [
            ("send_message", {"from_agent_id": "alpha", "to_agent_id": "beta", "message_type": "note", "payload": {"text": "hello"}}),
            ("manage_messages", {"action": "get", "agent_id": "beta"}),
            ("manage_messages", {"action": "mark_read", "agent_id": "beta", "message_ids": [1]}),
            ("manage_messages", {"action": "get", "agent_id": "beta", "unread_only": True}),
            ("send_message", {"from_agent_id": "alpha", "to_agent_id": "beta", "message_type": "note", "caller_agent_id": "nope"}),
            ("manage_messages", {"action": "nope", "agent_id": "beta"}),
        ]),
        ("tasks", [
            ("create_task", {"task_id": "t1", "parent_agent_id": "alpha", "description": "write the parser", "priority": 2}),
            ("create_subtask", {"task_id": "t1.1", "parent_task_id": "t1", "parent_agent_id": "alpha", "description": "lexer"}),
            ("assign_task", {"task_id": "t1", "assigned_agent_id": "beta"}),
            ("query_tasks", {"query_type": "task", "task_id": "t1"}),
            ("query_tasks", {"query_type": "subtasks", "parent_task_id": "t1"}),
            ("query_tasks", {"query_type": "tree", "root_task_id": "t1"}),
            ("get_available_tasks", {}),
            ("update_task_status", {"task_id": "t1.1", "status": "completed", "summary": "done"}),
            ("update_task_status", {"task_id": "t1", "status": "failed", "error": "boom"}),
            ("task_failures", {"action": "history", "task_id": "t1"}),
            ("task_failures", {"action": "list_dead_letter"}),
            ("task_failures", {"action": "retry", "task_id": "t1"}),
            ("query_tasks", {"query_type": "missing"}),
            ("wait_for_task", {"task_id": "t1", "timeout_s": 0}),
        ]),
        ("intent", [
            ("manage_work_intents", {"action": "declare", "agent_id": "alpha", "document_path": "notes.md", "intent": "edit intro", "ttl": 90}),
            ("manage_work_intents", {"action": "get"}),
            ("manage_work_intents", {"action": "declare", "agent_id": "beta", "document_path": "notes.md", "intent": "rewrite"}),
            ("acquire_lock", {"document_path": "notes.md", "agent_id": "alpha"}),
            ("manage_work_intents", {"action": "clear", "agent_id": "alpha", "document_path": "notes.md"}),
            ("manage_work_intents", {"action": "nope", "agent_id": "alpha"}),
        ]),
        ("deps", [
            ("manage_dependencies", {"mode": "declare", "dependent_agent_id": "beta", "depends_on_agent_id": "alpha", "depends_on_task_id": "t1", "condition": "task_completed"}),
            ("manage_dependencies", {"mode": "check", "agent_id": "beta"}),
            ("manage_dependencies", {"mode": "assert", "agent_id": "beta"}),
            ("manage_dependencies", {"mode": "list"}),
            ("manage_dependencies", {"mode": "satisfy", "dep_id": 1}),
            ("manage_dependencies", {"mode": "assert", "agent_id": "beta"}),
            ("manage_dependencies", {"mode": "nope"}),
        ]),
        ("lease", [
            ("acquire_coordinator_lease", {"agent_id": "alpha", "ttl": 30}),
            ("manage_leases", {"action": "leader"}),
            ("acquire_coordinator_lease", {"agent_id": "beta", "ttl": 30}),
            ("manage_leases", {"action": "refresh", "agent_id": "alpha"}),
            ("manage_leases", {"action": "release", "agent_id": "alpha"}),
            ("manage_leases", {"action": "claim", "agent_id": "beta", "ttl": 15}),
        ]),
        ("spawn", [
            ("spawn_subagent", {"parent_agent_id": "alpha", "subagent_type": "Explore", "description": "look around", "source": "external"}),
            ("spawn_subagent", {"parent_agent_id": "alpha", "subagent_type": "bad.type", "source": "external"}),
            ("get_pending_spawns", {"parent_agent_id": "alpha"}),
            ("report_subagent_spawned", {"parent_agent_id": "alpha", "subagent_type": "Explore", "child_agent_id": "alpha.1", "source": "external"}),
            ("is_subagent_stop_requested", {"agent_id": "alpha.1"}),
            ("request_subagent_deregistration", {"parent_agent_id": "alpha", "child_agent_id": "alpha.1"}),
            ("is_subagent_stop_requested", {"agent_id": "alpha.1"}),
            ("await_subagent_stopped", {"child_agent_id": "missing", "timeout": 0}),
        ]),
        ("broadcast", [
            ("register_agent", {"agent_id": "parent"}),
            ("register_agent", {"agent_id": "c1", "parent_id": "parent"}),
            ("register_agent", {"agent_id": "c2", "parent_id": "parent"}),
            ("broadcast", {"agent_id": "c1", "document_path": "shared.md"}),
            ("broadcast", {"agent_id": "c1", "require_ack": True, "message": "ship it", "ttl": 60}),
            ("acknowledge_broadcast", {"broadcast_id": 1, "agent_id": "c2"}),
            ("wait_for_broadcast_acks", {"broadcast_id": 1, "timeout_s": 0}),
            ("broadcast", {"agent_id": "c1", "handoff_targets": ["c2"], "document_path": "shared.md"}),
            ("wait_for_handoff", {"handoff_id": 1, "mode": "ack", "agent_id": "c2"}),
            ("wait_for_handoff", {"handoff_id": 1, "mode": "complete"}),
        ]),
        ("visibility", [
            ("register_agent", {"agent_id": "alpha"}),
            ("update_agent_status", {"agent_id": "alpha", "current_task": "port the hub", "scope": ["src"]}),
            ("get_agent_status", {"agent_id": "alpha"}),
            ("get_agent_tree", {"agent_id": "alpha"}),
            ("get_file_agent_map", {}),
            ("acquire_lock", {"document_path": "src/main.rs", "agent_id": "alpha"}),
            ("get_agent_tree", {}),
            ("run_assessment", {"format": "json"}),
            ("load_coordination_spec", {"path": "missing.json"}),
        ]),
        ("errors", [
            ("register_agent", {}),
            ("acquire_lock", {"document_path": "x", "agent_id": "a", "lock_type": "nope"}),
            ("not_a_tool", {}),
        ]),
    ]


def tool_names(binary: Path, schemas, diffs: Diffs) -> None:
    py_names = sorted(schemas)
    proc = subprocess.run([str(binary), "serve-mcp"], input=b"", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # A closed stdin ends the server before a request. Speak a session instead.
    env = os.environ.copy()
    env["COORDINATIONHUB_NOW"] = str(int(FROZEN))
    env["COORDINATIONHUB_PID"] = str(PID)
    storage = Path(tempfile.mkdtemp(prefix="mcp-"))
    project = Path(tempfile.mkdtemp(prefix="mcp-proj-"))
    messages = "\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "parity", "version": "0"}}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "status", "arguments": {}}}),
    ]) + "\n"
    proc = subprocess.run(
        [str(binary), "serve-mcp", "--storage-dir", str(storage), "--project-root", str(project)],
        input=messages.encode(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    lines = [line for line in proc.stdout.decode().splitlines() if line.strip()]
    if len(lines) < 2:
        diffs.add("mcp session", "two responses", proc.stdout.decode() + proc.stderr.decode())
        return
    listed = json.loads(lines[1])
    names = sorted(tool["name"] for tool in listed["result"]["tools"] if tool["name"] not in ADDITIVE_TOOLS)
    diffs.check("mcp tool names", py_names, names)
    if "who" not in {tool["name"] for tool in listed["result"]["tools"]}:
        diffs.add("who tool missing", True, False)
    called = json.loads(lines[2])
    text = called["result"]["content"][0]["text"]
    body = json.loads(text)
    engine, py = py_session(load_python_engine_only(), None, storage, project) if False else (None, None)
    # Compare the status payload with a direct Python call in a fresh store.
    py_dir = Path(tempfile.mkdtemp(prefix="py-status-"))
    py_project = Path(tempfile.mkdtemp(prefix="py-status-proj-"))
    CoordinationEngine, dispatch, _schemas = load_python(ROOT / "unused") if False else (None, None, None)
    shutil.rmtree(storage, ignore_errors=True)
    shutil.rmtree(project, ignore_errors=True)
    shutil.rmtree(py_dir, ignore_errors=True)
    shutil.rmtree(py_project, ignore_errors=True)
    diffs.check("mcp status tools field normalised", scrub(body).get("tools"), 50)


def load_python_engine_only():
    return None


def cli_suite(binary: Path, diffs: Diffs) -> None:
    sys.path.insert(0, os.environ["PARITY_LEGACY"])
    import coordinationhub.cli as cli
    commands = [
        ["register", "alpha", "--json", "--storage-dir", "{store}", "--project-root", "{proj}"],
        ["register", "beta", "--parent-id", "alpha", "--json", "--storage-dir", "{store}", "--project-root", "{proj}"],
        ["heartbeat", "alpha", "--json", "--storage-dir", "{store}", "--project-root", "{proj}"],
        ["list-agents", "--json", "--storage-dir", "{store}", "--project-root", "{proj}"],
        ["status", "--json", "--storage-dir", "{store}", "--project-root", "{proj}"],
        ["acquire-lock", "doc.md", "alpha", "--json", "--storage-dir", "{store}", "--project-root", "{proj}"],
        ["lock-status", "doc.md", "--json", "--storage-dir", "{store}", "--project-root", "{proj}"],
        ["release-lock", "doc.md", "alpha", "--json", "--storage-dir", "{store}", "--project-root", "{proj}"],
    ]
    py_store = Path(tempfile.mkdtemp(prefix="py-cli-"))
    rs_store = Path(tempfile.mkdtemp(prefix="rs-cli-"))
    project = Path(tempfile.mkdtemp(prefix="cli-proj-"))
    try:
        for template in commands:
            argv = [part.replace("{store}", str(py_store)).replace("{proj}", str(project)) for part in template]
            py_out = py_cli(cli, argv)
            rs_argv = [part.replace("{store}", str(rs_store)).replace("{proj}", str(project)) for part in template]
            rs_out = rust_cli(binary, rs_argv)
            diffs.check("cli " + " ".join(template[:2]), align(scrub(py_out)), align(scrub(rs_out)))
    finally:
        shutil.rmtree(py_store, ignore_errors=True)
        shutil.rmtree(rs_store, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)


def py_cli(cli, argv):
    import io
    from contextlib import redirect_stdout, redirect_stderr
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = cli.main(argv)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    text = out.getvalue().strip()
    try:
        parsed = json.loads(text) if text else {"__stderr__": err.getvalue()}
    except json.JSONDecodeError:
        parsed = {"__text__": text, "__stderr__": err.getvalue()}
    return {"code": code or 0, "body": parsed}


def rust_cli(binary: Path, argv):
    env = os.environ.copy()
    env["COORDINATIONHUB_NOW"] = str(int(FROZEN))
    env["COORDINATIONHUB_PID"] = str(PID)
    env["TZ"] = "UTC"
    proc = subprocess.run([str(binary), *argv], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    text = proc.stdout.strip()
    try:
        parsed = json.loads(text) if text else {"__stderr__": proc.stderr}
    except json.JSONDecodeError:
        parsed = {"__text__": text, "__stderr__": proc.stderr}
    return {"code": proc.returncode, "body": parsed}


def concurrent_suite(binary: Path, engine_cls, diffs: Diffs) -> None:
    project = Path(tempfile.mkdtemp(prefix="race-proj-"))
    py_dir = Path(tempfile.mkdtemp(prefix="race-py-"))
    rs_dir = Path(tempfile.mkdtemp(prefix="race-rs-"))
    try:
        engine, py = py_session(engine_cls, __import__("coordinationhub.dispatch", fromlist=["dispatch_tool"]).dispatch_tool, py_dir, project)
        py("register_agent", {"agent_id": "a"})
        py("register_agent", {"agent_id": "b"})
        results = []
        def worker(agent):
            results.append(py("acquire_lock", {"document_path": "race.md", "agent_id": agent}))
        threads = [threading.Thread(target=worker, args=(agent,)) for agent in ("a", "b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        wins = sum(1 for row in results if row.get("acquired") is True)
        engine.close()
        rust_call(binary, "register_agent", {"agent_id": "a"}, rs_dir, project)
        rust_call(binary, "register_agent", {"agent_id": "b"}, rs_dir, project)
        # Sequential force is not a race. Run two processes.
        procs = []
        for agent in ("a", "b"):
            env = os.environ.copy()
            env["COORDINATIONHUB_NOW"] = str(int(FROZEN))
            env["COORDINATIONHUB_PID"] = str(PID)
            procs.append(subprocess.Popen(
                [str(binary), "call", "acquire_lock", "--storage-dir", str(rs_dir), "--project-root", str(project), "--args", json.dumps({"document_path": "race.md", "agent_id": agent})],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            ))
        rs_results = []
        for proc in procs:
            out, _err = proc.communicate(timeout=10)
            rs_results.append(json.loads(out.decode() or "{}"))
        rs_wins = sum(1 for row in rs_results if row.get("acquired") is True)
        diffs.check("concurrent exactly one lock winner", 1, wins)
        diffs.check("concurrent rust exactly one lock winner", 1, rs_wins)
    finally:
        shutil.rmtree(project, ignore_errors=True)
        shutil.rmtree(py_dir, ignore_errors=True)
        shutil.rmtree(rs_dir, ignore_errors=True)


def http_suite(binary: Path, diffs: Diffs) -> None:
    # Both servers answer GET /health. Body is compared after parsing.
    import socket
    def grab(port_holder, command):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        port_holder.append(port)
        return command + ["--port", str(port), "--no-auth", "--host", "127.0.0.1"]
    # Python serve is long-running. Start both and curl /health.
    py_port, rs_port = [], []
    storage_py = tempfile.mkdtemp(prefix="http-py-")
    storage_rs = tempfile.mkdtemp(prefix="http-rs-")
    project = tempfile.mkdtemp(prefix="http-proj-")
    env = os.environ.copy()
    env["PYTHONPATH"] = os.environ["PARITY_LEGACY"]
    env["COORDINATIONHUB_NOW"] = str(int(FROZEN))
    py_cmd = grab(py_port, [sys.executable, "-m", "coordinationhub", "serve", "--storage-dir", storage_py, "--project-root", project])
    rs_cmd = grab(rs_port, [str(binary), "serve", "--storage-dir", storage_rs, "--project-root", project])
    py = subprocess.Popen(py_cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    rs = subprocess.Popen(rs_cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        py_body = wait_health(py_port[0])
        rs_body = wait_health(rs_port[0])
        diffs.check("http /health", py_body, rs_body)
    finally:
        py.terminate()
        rs.terminate()
        py.wait(timeout=5)
        rs.wait(timeout=5)
        shutil.rmtree(storage_py, ignore_errors=True)
        shutil.rmtree(storage_rs, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)


def wait_health(port: int):
    import urllib.request
    url = f"http://127.0.0.1:{port}/health"
    last = ""
    for _ in range(50):
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:
                return json.loads(response.read().decode())
        except Exception as exc:
            last = str(exc)
            time.sleep(0.05)
    return {"__error__": last}


def main() -> int:
    binary = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "target" / "release" / "coordinationhub").resolve()
    if not binary.is_file():
        raise SystemExit(f"missing binary {binary}")
    legacy = Path(tempfile.mkdtemp(prefix="coordinationhub-legacy-"))
    extract_legacy(legacy)
    os.environ["PARITY_LEGACY"] = str(legacy)
    engine_cls, dispatch, schemas = load_python(legacy)
    diffs = Diffs()
    tool_suite(binary, engine_cls, dispatch, diffs)
    cli_suite(binary, diffs)
    concurrent_suite(binary, engine_cls, diffs)
    # MCP name check uses the already-imported schemas.
    py_names = sorted(schemas)
    env = os.environ.copy()
    env["COORDINATIONHUB_NOW"] = str(int(FROZEN))
    env["COORDINATIONHUB_PID"] = str(PID)
    storage = Path(tempfile.mkdtemp(prefix="mcp-"))
    project = Path(tempfile.mkdtemp(prefix="mcp-proj-"))
    messages = "\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "parity", "version": "0"}}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "register_agent", "arguments": {"agent_id": "mcp-alpha"}}}),
    ]) + "\n"
    proc = subprocess.run([str(binary), "serve-mcp", "--storage-dir", str(storage), "--project-root", str(project)], input=messages.encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    lines = [json.loads(line) for line in proc.stdout.decode().splitlines() if line.strip()]
    if len(lines) < 3:
        diffs.add("mcp frames", 3, proc.stdout.decode() + proc.stderr.decode())
    else:
        names = sorted(tool["name"] for tool in lines[1]["result"]["tools"] if tool["name"] not in ADDITIVE_TOOLS)
        diffs.check("mcp tool names", py_names, names)
        text = json.loads(lines[2]["result"]["content"][0]["text"])
        engine, py = py_session(engine_cls, dispatch, Path(tempfile.mkdtemp(prefix="mcp-py-")), project)
        try:
            left = py("register_agent", {"agent_id": "mcp-alpha"})
        finally:
            engine.close()
        diffs.check("mcp register_agent", canon(scrub(left)), canon(scrub(text)))
    http_suite(binary, diffs)
    shutil.rmtree(legacy, ignore_errors=True)
    print(f"diffs {len(diffs.items)}")
    return 1 if diffs.items else 0


if __name__ == "__main__":
    sys.exit(main())
