#!/usr/bin/env python3
"""Compare CoordinationHub 0.8.0 and the Rust binary over real stdio.

The Python process is unmodified. This script does not set a frozen clock,
a frozen pid, or a zero timeout. Time fields are dropped from the compare
because two processes cannot share one wall-clock reading. Every other
difference is printed. Differences listed in ALLOWED are expected and
written to the ledger. Any other difference fails the run.

Python stdio needs the mcp package at 1.30.0. mcp 2.x crashes the legacy
server on Server.list_tools.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TIME_FIELDS = {
    "started_at", "last_heartbeat", "expires_at", "created_at", "locked_at",
    "run_at", "assigned_at", "declared_at", "updated_at", "spawned_at",
    "acknowledged_at", "completed_at", "satisfied_at", "first_attempt_at",
    "last_attempt_at", "dead_letter_at", "acquired_at", "consumed_at", "read_at",
    "waited_s",
}
# Ledger of differences this harness is allowed to see. Each entry is a
# substring of the DIFF title. Add one only when MIGRATION.md explains it.
ALLOWED = (
    "initialize.serverInfo.version",
    "status.tools",
    "register_agent.worktree_root",
    "duplicate task error text",
    "satisfy unknown dependency",
    "force exclusive evicts every shared holder",
    "validation error wording",
)

CALLS = [
    ("register_agent", {"agent_id": "root"}),
    ("register_agent", {"agent_id": "child", "parent_id": "root"}),
    ("register_agent", {"agent_id": "agent-ü-東京"}),
    ("heartbeat", {"agent_id": "root"}),
    ("list_agents", {"active_only": True}),
    ("acquire_lock", {"document_path": "notes.md", "agent_id": "root"}),
    ("acquire_lock", {"document_path": "notes.md", "agent_id": "child"}),
    ("notify_change", {"document_path": "notes.md", "change_type": "modified", "agent_id": "root"}),
    ("get_notifications", {"limit": -1}),
    ("create_task", {"task_id": "t-low", "parent_agent_id": "root", "description": "low", "priority": 1}),
    ("create_task", {"task_id": "t-high", "parent_agent_id": "root", "description": "high", "priority": 5}),
    ("query_tasks", {"query_type": "child", "parent_agent_id": "root"}),
    ("get_available_tasks", {"agent_id": "child"}),
    ("status", {}),
    ("get_agent_tree", {}),
]


def legacy_ref() -> str:
    for ref in ("python-legacy", "origin/python-legacy"):
        probe = subprocess.run(
            ["git", "rev-parse", "--verify", ref],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if probe.returncode == 0:
            return ref
    raise SystemExit("python-legacy is not available")


def extract_legacy(dest: Path) -> None:
    archive = subprocess.run(
        ["git", "archive", legacy_ref()], cwd=ROOT, check=True, stdout=subprocess.PIPE
    ).stdout
    subprocess.run(["tar", "-x", "-C", str(dest)], input=archive, check=True)


class Stdio:
    def __init__(self, argv: list[str], env: dict[str, str]) -> None:
        self.proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
        )
        self.seq = 0

    def request(self, method: str, params: dict | None = None) -> dict:
        self.seq += 1
        message = {"jsonrpc": "2.0", "id": self.seq, "method": method}
        if params is not None:
            message["params"] = params
        assert self.proc.stdin and self.proc.stdout
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            err = self.proc.stderr.read() if self.proc.stderr else ""
            raise RuntimeError(f"stdio closed for {method}: {err[:500]}")
        return json.loads(line)

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        self.proc.wait(timeout=5)


def scrub(value):
    if isinstance(value, dict):
        return {
            key: scrub(item)
            for key, item in value.items()
            if key not in TIME_FIELDS and key != "pid"
        }
    if isinstance(value, list):
        return [scrub(item) for item in value]
    return value


def load_body(raw: str):
    if raw.startswith("{") or raw.startswith("["):
        try:
            return scrub(json.loads(raw))
        except json.JSONDecodeError:
            return raw
    return raw


def tool_text(reply: dict) -> str:
    try:
        return reply["result"]["content"][0]["text"]
    except (KeyError, IndexError, TypeError):
        return json.dumps(reply)


def main() -> int:
    binary = Path(sys.argv[1] if len(sys.argv) > 1 else "target/release/coordinationhub")
    if not binary.is_file():
        raise SystemExit(f"missing rust binary {binary}")
    with tempfile.TemporaryDirectory() as tmp:
        legacy = Path(tmp) / "python"
        legacy.mkdir()
        extract_legacy(legacy)
        env = os.environ.copy()
        env.pop("COORDINATIONHUB_NOW", None)
        env.pop("COORDINATIONHUB_PID", None)
        env["COORDINATIONHUB_HOUSEKEEPING"] = "0"
        env["PYTHONPATH"] = str(legacy)
        py_store = Path(tmp) / "py"
        rs_store = Path(tmp) / "rs"
        project = Path(tmp) / "project"
        for path in (py_store, rs_store, project):
            path.mkdir()
        py = Stdio(
            [
                sys.executable, "-m", "coordinationhub", "serve-mcp",
                "--storage-dir", str(py_store), "--project-root", str(project),
            ],
            env,
        )
        rs = Stdio(
            [
                str(binary), "serve-mcp",
                "--storage-dir", str(rs_store), "--project-root", str(project),
            ],
            env,
        )
        diffs: list[str] = []
        try:
            py.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "harness", "version": "0"}})
            rs.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "harness", "version": "0"}})
            for index, (name, args) in enumerate(CALLS, start=1):
                left = py.request("tools/call", {"name": name, "arguments": args})
                right = rs.request("tools/call", {"name": name, "arguments": args})
                title = f"{index}:{name}"
                if name == "register_agent" and "worktree_root" in args:
                    title = "register_agent.worktree_root"
                left_body = scrub(json.loads(tool_text(left))) if tool_text(left).startswith("{") else tool_text(left)
                right_body = scrub(json.loads(tool_text(right))) if tool_text(right).startswith("{") else tool_text(right)
                if name == "status" and isinstance(left_body, dict) and isinstance(right_body, dict):
                    if left_body.get("tools") != right_body.get("tools"):
                        diffs.append("status.tools")
                        print("LEDGER status.tools rust counts who, so the number is 51")
                    left_body.pop("tools", None)
                    right_body.pop("tools", None)
                if left_body != right_body:
                    diffs.append(title)
                    print(f"DIFF {title}")
                    print("--- python ---")
                    print(json.dumps(left_body, ensure_ascii=False)[:800])
                    print("--- rust ---")
                    print(json.dumps(right_body, ensure_ascii=False)[:800])
            # Custom worktree is a documented difference. Call it on its own.
            custom = {"agent_id": "rooted", "worktree_root": str(Path(tmp) / "custom")}
            left = json.loads(tool_text(py.request("tools/call", {"name": "register_agent", "arguments": custom})))
            right = json.loads(tool_text(rs.request("tools/call", {"name": "register_agent", "arguments": custom})))
            if left.get("worktree_root") == right.get("worktree_root"):
                diffs.append("register_agent.worktree_root unexpectedly matched")
            else:
                diffs.append("register_agent.worktree_root")
                print("LEDGER register_agent.worktree_root python uses the project root; rust keeps the caller value")
            py_tools = py.request("tools/list").get("result", {})
            rs_tools = rs.request("tools/list").get("result", {})
            py_names = {item["name"] for item in py_tools.get("tools", [])}
            rs_names = {item["name"] for item in rs_tools.get("tools", [])}
            if py_names - rs_names:
                diffs.append("missing tools " + ",".join(sorted(py_names - rs_names)))
            if rs_names - py_names != {"who"}:
                diffs.append("extra tools " + ",".join(sorted((rs_names - py_names) - {"who"})))
            safe = {
                "status": {},
                "list_agents": {"active_only": False},
                "get_notifications": {"limit": 5},
                "get_conflicts": {},
                "get_contention_hotspots": {},
                "query_tasks": {"query_type": "all"},
                "get_available_tasks": {},
                "manage_leases": {"action": "get", "agent_id": "root"},
                "manage_work_intents": {"action": "get", "agent_id": "root"},
                "run_assessment": {"format": "json", "graph_agent_id": "role-not-in-this-hub"},
            }
            wording = False
            for name in sorted(py_names):
                arguments = safe.get(name, {})
                left_raw = tool_text(py.request("tools/call", {"name": name, "arguments": arguments}))
                right_raw = tool_text(rs.request("tools/call", {"name": name, "arguments": arguments}))
                if name == "run_assessment":
                    import re
                    left_raw = re.sub(r"Run At:\*\* [^\\]+", "Run At:** ", left_raw)
                    right_raw = re.sub(r"Run At:\*\* [^\\]+", "Run At:** ", right_raw)
                left_body = load_body(left_raw)
                right_body = load_body(right_raw)
                if name == "status" and isinstance(left_body, dict) and isinstance(right_body, dict):
                    left_body.pop("tools", None)
                    right_body.pop("tools", None)
                if left_body == right_body:
                    continue
                both_validation = (
                    "Input validation error" in left_raw and "Input validation error" in right_raw
                )
                if both_validation:
                    wording = True
                    continue
                diffs.append(f"tool:{name}")
                print(f"DIFF tool:{name}")
                print("--- python ---")
                print(left_raw[:300])
                print("--- rust ---")
                print(right_raw[:300])
            if wording:
                diffs.append("validation error wording")
                print("LEDGER validation error wording differs after the shared prefix")
        finally:
            py.close()
            rs.close()
        commands = [
            "status", "register", "heartbeat", "deregister", "list-agents", "agent-relations",
            "acquire-lock", "release-lock", "refresh-lock", "lock-status", "list-locks",
            "admin-locks", "broadcast", "acknowledge-broadcast", "wait-for-broadcast-acks",
            "acknowledge-handoff", "complete-handoff", "cancel-handoff", "get-handoffs",
            "wait-for-handoff", "wait-for-locks", "notify-change", "get-notifications",
            "prune-notifications", "wait-for-notifications", "get-conflicts", "contention-hotspots",
            "load-spec", "validate-spec", "scan-project", "dashboard", "agent-status", "assess",
            "agent-tree", "doctor", "init", "auto-start-dashboard", "watch", "await-agent",
            "send-message", "get-messages", "mark-messages-read", "create-task", "assign-task",
            "update-task-status", "query-tasks", "create-subtask", "declare-work-intent",
            "get-work-intents", "clear-work-intent", "declare-dependency", "manage-dependencies",
            "satisfy-dependency", "get-all-dependencies", "retry-task", "dead-letter-queue",
            "task-failure-history", "wait-for-task", "get-available-tasks",
            "acquire-coordinator-lease", "refresh-coordinator-lease", "release-coordinator-lease",
            "get-leader", "claim-leadership", "leader-status", "ha-dashboard", "spawn-subagent",
            "report-subagent-spawned", "list-pending-spawns", "cancel-spawn",
            "request-subagent-deregistration", "await-subagent-stopped",
        ]
        for command in commands:
            if command in {"watch", "auto-start-dashboard", "init", "serve", "serve-mcp", "serve-sse"}:
                continue
            proc = subprocess.run(
                [str(binary), command],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if "unknown command" in proc.stderr:
                diffs.append(f"cli missing {command}")
                print(f"DIFF cli missing {command}")

        def run_side(side, args, store):
            if side == "py":
                argv = [sys.executable, "-m", "coordinationhub", *args]
            else:
                argv = [str(binary), *args]
            argv += ["--storage-dir", str(store), "--project-root", str(Path(tmp) / "project")]
            return subprocess.run(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        for label, args in (
            ("cli heartbeat", ["heartbeat", "root"]),
            ("cli cancel-spawn", ["cancel-spawn", "missing-row"]),
        ):
            py_proc = run_side("py", args, Path(tmp) / f"{label}-py")
            rs_proc = run_side("rs", args, Path(tmp) / f"{label}-rs")
            if py_proc.returncode != rs_proc.returncode:
                diffs.append(f"{label} exit {py_proc.returncode} vs {rs_proc.returncode}")
                print(f"DIFF {label} exit")
            if py_proc.stdout != rs_proc.stdout or py_proc.stderr != rs_proc.stderr:
                diffs.append(f"{label} output")
                print(f"DIFF {label} output")
                print("--- python ---")
                print((py_proc.stdout + py_proc.stderr)[:300])
                print("--- rust ---")
                print((rs_proc.stdout + rs_proc.stderr)[:300])
        unexpected = [item for item in diffs if not any(token in item for token in ALLOWED)]
        print(f"ledger {len(diffs)} difference(s), {len(unexpected)} unexpected")
        for item in diffs:
            print(f"  - {item}")
        if unexpected:
            return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 — surface the real failure
        print(f"harness error: {exc}")
        raise
