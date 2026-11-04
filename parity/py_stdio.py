#!/usr/bin/env python3
"""Minimal JSON-RPC stdio shim over the Python CoordinationEngine.

The optional ``mcp`` package is not required. Framing matches the Rust server:
Content-Length, then one JSON-RPC object.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from coordinationhub.core import CoordinationEngine
from coordinationhub.dispatch import dispatch_tool
from coordinationhub.schemas import TOOL_SCHEMAS

storage = os.environ.get("COORDINATIONHUB_STORAGE_DIR")
project = os.environ.get("COORDINATIONHUB_PROJECT_ROOT")
engine = CoordinationEngine(
    storage_dir=Path(storage) if storage else None,
    project_root=Path(project) if project else None,
    namespace=os.environ.get("COORDINATIONHUB_NAMESPACE", "hub"),
)
engine.start()


def read_message():
    header = b""
    while b"\r\n\r\n" not in header:
        chunk = sys.stdin.buffer.read(1)
        if not chunk:
            return None
        header += chunk
    length = 0
    for line in header.decode().split("\r\n"):
        if line.lower().startswith("content-length:"):
            length = int(line.split(":", 1)[1].strip())
    return json.loads(sys.stdin.buffer.read(length).decode())


def write_message(payload: dict) -> None:
    raw = json.dumps(payload).encode()
    sys.stdout.buffer.write(f"Content-Length: {len(raw)}\r\n\r\n".encode() + raw)
    sys.stdout.buffer.flush()


PHASE2 = {
    "compact_status": "One call snapshot of who is doing what.",
    "claim_work": "Claim an area of work.",
    "release_work": "Release a work claim.",
    "list_work": "List work claims.",
    "inbox": "Unread messages and open handoffs.",
}


def main() -> None:
    while True:
        msg = read_message()
        if msg is None:
            return
        method = msg.get("method", "")
        msg_id = msg.get("id")
        params = msg.get("params") or {}
        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "coordinationhub", "version": "0.8.0"},
            }
        elif method == "tools/call":
            name = params.get("name", "")
            arguments = params.get("arguments") or {}
            if name in PHASE2:
                text = json.dumps({"error": "phase 2 tool is Rust-only", "tool": name}, indent=2)
                result = {"content": [{"type": "text", "text": text}], "isError": True}
            else:
                try:
                    value = dispatch_tool(engine, name, arguments)
                    result = {
                        "content": [{"type": "text", "text": json.dumps(value, indent=2, default=str)}],
                        "isError": False,
                    }
                except Exception as exc:
                    result = {
                        "content": [{"type": "text", "text": f"Error: {exc}"}],
                        "isError": True,
                    }
        else:
            write_message({
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            })
            continue
        write_message({"jsonrpc": "2.0", "id": msg_id, "result": result})


if __name__ == "__main__":
    try:
        main()
    finally:
        engine.close()
