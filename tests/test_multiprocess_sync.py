"""Multi-process stress test for cross-process event synchronization.

Spawns ``coordinationhub serve`` in a subprocess, registers two agents,
creates a task from Agent A, and has Agent B call ``wait_for_task``
against the HTTP server. When Agent A updates the task to ``completed``,
Agent B's wait must return successfully — proving that the SQLite-backed
event journal + hybrid wait path works across processes.
"""

from __future__ import annotations

import json
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server_url(tmp_path_factory: pytest.TempPathFactory):
    """Start coordinationhub serve in a subprocess and yield its URL."""
    tmp_path = tmp_path_factory.mktemp("multiprocess_sync")
    port = _find_free_port()
    proc = subprocess.Popen(
        ["python", "-m", "coordinationhub.cli", "serve", "--port", str(port)],
        cwd=str(Path(__file__).resolve().parent.parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    url = f"http://localhost:{port}"

    # Wait for health endpoint
    deadline = time.time() + 10.0
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=1.0) as resp:
                if resp.status == 200:
                    break
        except Exception:
            pass
        time.sleep(0.2)
    else:
        proc.terminate()
        proc.wait()
        raise RuntimeError("Server did not start in time")

    try:
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def _call_tool(url: str, tool_name: str, arguments: dict) -> dict:
    """POST /call and return the inner result dict."""
    req = urllib.request.Request(
        f"{url}/call",
        data=json.dumps({"tool": tool_name, "arguments": arguments}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30.0) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
        return payload.get("result", payload)


def test_cross_process_wait_for_task(server_url: str) -> None:
    """Agent B's wait_for_task succeeds when Agent A completes the task remotely."""
    import uuid
    agent_a = f"hub.mp.a.{uuid.uuid4().hex[:6]}"
    agent_b = f"hub.mp.b.{uuid.uuid4().hex[:6]}"
    task_id = f"hub.mp.task.{uuid.uuid4().hex[:6]}"

    # Register both agents
    _call_tool(server_url, "register_agent", {"agent_id": agent_a})
    _call_tool(server_url, "register_agent", {"agent_id": agent_b})

    # Agent A creates a task
    result = _call_tool(
        server_url,
        "create_task",
        {
            "task_id": task_id,
            "parent_agent_id": agent_a,
            "description": "cross-process sync test task",
        },
    )
    assert result.get("created") is True, f"Task creation failed: {result}"

    # Agent B starts waiting for the task (in the background so we can complete it)
    import threading

    wait_result: dict = {}

    def wait_thread() -> None:
        try:
            wait_result.update(
                _call_tool(
                    server_url,
                    "wait_for_task",
                    {"task_id": task_id, "timeout_s": 10.0, "poll_interval_s": 0.5},
                )
            )
        except Exception as exc:
            wait_result["_error"] = str(exc)

    t = threading.Thread(target=wait_thread)
    t.start()

    # Give the server time to enter the wait loop
    time.sleep(0.5)

    # Agent A marks the task completed
    update = _call_tool(
        server_url,
        "update_task_status",
        {"task_id": task_id, "status": "completed", "summary": "done"},
    )
    assert update.get("updated") is True, f"Task update failed: {update}"

    t.join(timeout=15.0)
    assert t.is_alive() is False, "wait_for_task thread did not return in time"

    assert wait_result.get("timed_out") is False, (
        f"wait_for_task timed out unexpectedly: {wait_result}"
    )
    assert wait_result.get("status") == "completed", (
        f"wait_for_task did not see completed status: {wait_result}"
    )
