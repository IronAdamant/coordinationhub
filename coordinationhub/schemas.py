"""Tool schemas for CoordinationHub.

Aggregates all tool-group schemas into TOOL_SCHEMAS.
Backward-compatible re-export — `from .schemas import TOOL_SCHEMAS` still works.

Group files:
- schemas_identity.py       — Identity & Registration (6 tools)
- schemas_locking.py        — Document Locking (7 tools)
- schemas_coordination.py    — Coordination Actions (2 tools)
- schemas_change.py          — Change Awareness (3 tools)
- schemas_audit.py          — Audit & Status (2 tools)
- schemas_visibility.py      — Graph & Visibility (7 tools)
"""

from __future__ import annotations

from .schemas_identity import TOOL_SCHEMAS_IDENTITY
from .schemas_locking import TOOL_SCHEMAS_LOCKING
from .schemas_coordination import TOOL_SCHEMAS_COORDINATION
from .schemas_change import TOOL_SCHEMAS_CHANGE
from .schemas_audit import TOOL_SCHEMAS_AUDIT
from .schemas_visibility import TOOL_SCHEMAS_VISIBILITY

TOOL_SCHEMAS: dict[str, dict] = (
    TOOL_SCHEMAS_IDENTITY
    | TOOL_SCHEMAS_LOCKING
    | TOOL_SCHEMAS_COORDINATION
    | TOOL_SCHEMAS_CHANGE
    | TOOL_SCHEMAS_AUDIT
    | TOOL_SCHEMAS_VISIBILITY
)
