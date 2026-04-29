"""CoordinationHub — multi-agent swarm coordination MCP server.

Exposes agent identity, document locking, lineage tracking, and change
awareness to any MCP-aware coding agent or IDE.

Stdlib-only core. Optional `mcp` package for stdio transport only.
"""

from __future__ import annotations

from .core import CoordinationEngine
from .mcp_server import CoordinationHubMCPServer

__version__ = "0.7.8"

__all__ = [
    "CoordinationEngine",
    "CoordinationHubMCPServer",
    "__version__",
]
