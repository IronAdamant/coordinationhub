"""Stdio-based MCP server for CoordinationHub using the ``mcp`` Python package.

This module provides an MCP-compliant server that communicates over
stdin/stdout, suitable for integration with Claude Desktop, Cursor, and
other MCP-aware clients.

Entry point::

    coordinationhub              (installed via pyproject.toml console_scripts)
    python -m coordinationhub.mcp_stdio

Requires the optional ``mcp`` dependency:
    pip install coordinationhub[mcp]
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any

from .core import CoordinationEngine
from .mcp_server import dispatch_tool
from .schemas import TOOL_SCHEMAS

logger = logging.getLogger(__name__)

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool
    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False


def _configure_server(engine: CoordinationEngine):
    """Register all CoordinationHub tools on an MCP Server instance.

    Factored out of ``create_server`` so that ``_run_server`` can manage
    the engine lifecycle independently (ensuring it is closed on exit).
    """
    server = Server("coordinationhub")

    @server.list_tools()
    async def list_tools():
        """Return all CoordinationHub tool definitions as MCP Tool objects."""
        return [
            Tool(
                name=name,
                description=schema["description"],
                inputSchema=schema["parameters"],
            )
            for name, schema in TOOL_SCHEMAS.items()
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]):
        """Dispatch an MCP tool call to the appropriate engine method.

        T2.3: exceptions are logged with a correlation id and the client
        sees only a generic message. Previously the raw `str(exc)` leaked
        SQLite error text, paths, and stack fragments. Still returns as
        ``TextContent`` because the MCP SDK's ``call_tool`` decorator
        doesn't expose an error envelope yet; the payload starts with
        ``"Error:"`` so clients can detect the failure textually.
        """
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None, lambda: dispatch_tool(engine, name, arguments),
            )
        except Exception as exc:
            import uuid as _uuid
            correlation_id = _uuid.uuid4().hex[:12]
            logger.exception(
                "tool %s failed [correlation_id=%s]", name, correlation_id,
            )
            return [TextContent(
                type="text",
                text=f"Error: Internal tool execution error (correlation_id={correlation_id})",
            )]

        return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

    return server


def create_server(
    storage_dir: str | None = None,
    project_root: str | None = None,
    namespace: str = "hub",
):
    """Create and configure an MCP Server with all CoordinationHub tools registered.

    Args:
        storage_dir: Directory for CoordinationHub's persistent storage.
        project_root: Root of the project to coordinate. Defaults to auto-detect.
        namespace: Agent ID namespace prefix. Defaults to "hub".

    Returns:
        A tuple of (configured ``mcp.server.Server`` instance, ``CoordinationEngine``).

    The server does NOT register itself as an agent. It is coordination
    middleware, not a swarm participant — registering a self-agent only
    served to keep its own ``last_heartbeat`` fresh, which nothing
    consumed, and leaked a ``hub.<PID>.0`` row in the agents table on every
    abrupt shutdown.
    """
    if not _MCP_AVAILABLE:
        raise RuntimeError(
            "The 'mcp' package is not installed. "
            "Install it with: pip install coordinationhub[mcp]"
        )

    from pathlib import Path
    engine = CoordinationEngine(
        storage_dir=storage_dir and Path(storage_dir),
        project_root=project_root and Path(project_root),
        namespace=namespace,
    )
    engine.start()

    try:
        server = _configure_server(engine)
    except Exception:
        engine.close()
        raise
    return server, engine


async def _run_server() -> None:
    """Start the stdio MCP server and run until the client disconnects."""
    storage_dir = os.environ.get("COORDINATIONHUB_STORAGE_DIR")
    project_root = os.environ.get("COORDINATIONHUB_PROJECT_ROOT")
    namespace = os.environ.get("COORDINATIONHUB_NAMESPACE", "hub")

    server, engine = create_server(
        storage_dir=storage_dir,
        project_root=project_root,
        namespace=namespace,
    )

    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
    finally:
        engine.close()


def main() -> None:
    """Entry point for ``coordinationhub`` console script."""
    if not _MCP_AVAILABLE:
        print(
            "Error: The 'mcp' package is not installed.\n"
            "\n"
            "The CoordinationHub stdio MCP server requires the 'mcp' Python package.\n"
            "Install it with:\n"
            "\n"
            "    pip install coordinationhub[mcp]\n"
            "\n"
            "Or install the mcp package directly:\n"
            "\n"
            "    pip install mcp\n",
            file=sys.stderr,
        )
        sys.exit(1)

    asyncio.run(_run_server())


if __name__ == "__main__":
    main()
