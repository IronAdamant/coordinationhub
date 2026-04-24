"""String-length caps for user-supplied fields (T6.14).

A malicious or runaway caller can wedge tens of megabytes into fields
that land in SQLite and then propagate into dashboards, events, and
log snapshots. Truncating at the primitive boundary keeps the DB small
and the dashboard responsive.

Caps are tunable via environment variables so deployments that need
bigger prompts or descriptions can raise the ceiling without patching
source. Truncation appends a short ``[truncated N→M]`` suffix so
observers can tell when a field was clipped.

Zero internal dependencies — primitives import ``truncate`` directly.
"""

from __future__ import annotations

import os


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


# Caps in Unicode code points. Raise via COORDINATIONHUB_MAX_<FIELD>.
MAX_DESCRIPTION = _env_int("COORDINATIONHUB_MAX_DESCRIPTION", 10_000)
MAX_PROMPT = _env_int("COORDINATIONHUB_MAX_PROMPT", 100_000)
MAX_SUMMARY = _env_int("COORDINATIONHUB_MAX_SUMMARY", 10_000)
MAX_ERROR = _env_int("COORDINATIONHUB_MAX_ERROR", 10_000)
MAX_CURRENT_TASK = _env_int("COORDINATIONHUB_MAX_CURRENT_TASK", 5_000)
MAX_INTENT = _env_int("COORDINATIONHUB_MAX_INTENT", 1_000)
MAX_MESSAGE = _env_int("COORDINATIONHUB_MAX_MESSAGE", 10_000)


def truncate(value: str | None, max_len: int) -> str | None:
    """Return ``value`` clipped to ``max_len`` code points.

    A ``None`` input passes through unchanged. Strings over the cap
    are truncated and annotated so a consumer can spot the clip.
    """
    if value is None:
        return None
    if len(value) <= max_len:
        return value
    original_len = len(value)
    keep = max(0, max_len - 32)  # leave room for the suffix
    return (
        value[:keep]
        + f"... [truncated {original_len}->{max_len}]"
    )
