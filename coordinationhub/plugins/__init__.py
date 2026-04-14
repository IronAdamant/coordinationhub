"""CoordinationHub plugin system.

Plugins extend CoordinationHub with optional capabilities:
  - assessment      — coordination trace scoring
  - graph           — coordination graph loading and validation
  - dashboard       — web dashboard and SSE events

All plugins are loaded by default for backward compatibility.
"""

from __future__ import annotations
