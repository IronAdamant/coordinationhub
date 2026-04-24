"""Tool schemas for CoordinationHub — all MCP tools.

Each functional group lives in its own sibling module (``identity``,
``locking``, ``coordination`` …). This package re-exports the aggregated
``TOOL_SCHEMAS`` dict used by the HTTP server, stdio MCP server, and the
documentation generator.

The individual group dicts are also re-exported for callers that want to
introspect a single domain without pulling the whole surface.
"""

from __future__ import annotations

from .identity import TOOL_SCHEMAS_IDENTITY
from .locking import TOOL_SCHEMAS_LOCKING
from .coordination import TOOL_SCHEMAS_COORDINATION
from .messaging import TOOL_SCHEMAS_MESSAGING
from .change import TOOL_SCHEMAS_CHANGE
from .audit import TOOL_SCHEMAS_AUDIT
from .visibility import TOOL_SCHEMAS_VISIBILITY
from .tasks import TOOL_SCHEMAS_TASKS
from .intent import TOOL_SCHEMAS_INTENT
from .handoffs import TOOL_SCHEMAS_HANDOFFS
from .deps import TOOL_SCHEMAS_DEPS
from .dlq import TOOL_SCHEMAS_DLQ
from .leases import TOOL_SCHEMAS_LEASES
from .spawner import TOOL_SCHEMAS_SPAWNER


TOOL_SCHEMAS: dict[str, dict] = (
    TOOL_SCHEMAS_IDENTITY
    | TOOL_SCHEMAS_LOCKING
    | TOOL_SCHEMAS_COORDINATION
    | TOOL_SCHEMAS_CHANGE
    | TOOL_SCHEMAS_AUDIT
    | TOOL_SCHEMAS_VISIBILITY
    | TOOL_SCHEMAS_MESSAGING
    | TOOL_SCHEMAS_TASKS
    | TOOL_SCHEMAS_INTENT
    | TOOL_SCHEMAS_HANDOFFS
    | TOOL_SCHEMAS_DEPS
    | TOOL_SCHEMAS_DLQ
    | TOOL_SCHEMAS_LEASES
    | TOOL_SCHEMAS_SPAWNER
)


# T6.13: semantic-version string identifying the shape of TOOL_SCHEMAS.
# Bump the major when a tool is renamed or removed; bump the minor when
# a tool or parameter is added in a backwards-compatible way; bump the
# patch for description/documentation-only edits. Clients pinning to an
# older major can detect a breaking change at handshake without having
# to diff schema dicts.
#
# Exposed via ``mcp_server`` on ``/tools`` and ``/health`` responses and
# on the stdio MCP ``tools/list`` handshake.
TOOLS_VERSION = "1.0.0"


__all__ = [
    "TOOL_SCHEMAS",
    "TOOLS_VERSION",
    "TOOL_SCHEMAS_IDENTITY",
    "TOOL_SCHEMAS_LOCKING",
    "TOOL_SCHEMAS_COORDINATION",
    "TOOL_SCHEMAS_MESSAGING",
    "TOOL_SCHEMAS_CHANGE",
    "TOOL_SCHEMAS_AUDIT",
    "TOOL_SCHEMAS_VISIBILITY",
    "TOOL_SCHEMAS_TASKS",
    "TOOL_SCHEMAS_INTENT",
    "TOOL_SCHEMAS_HANDOFFS",
    "TOOL_SCHEMAS_DEPS",
    "TOOL_SCHEMAS_DLQ",
    "TOOL_SCHEMAS_LEASES",
    "TOOL_SCHEMAS_SPAWNER",
]
