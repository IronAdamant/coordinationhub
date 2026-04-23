"""Tool-count regression test to enforce the consolidation target.

T5.8: the assertion is ``==`` rather than ``<=`` so additions are
flagged (not silently accepted). Bump this number intentionally when
consolidating or intentionally adding a tool.
"""

from coordinationhub.dispatch import TOOL_DISPATCH

# T5.8: exact-count pin. Bump intentionally when the tool surface
# legitimately changes; a diff shift without a pin update means a
# silent addition.
EXPECTED_TOOL_COUNT = 50


def test_tool_count_exact() -> None:
    """Assert that the MCP tool surface is exactly the pinned count."""
    assert len(TOOL_DISPATCH) == EXPECTED_TOOL_COUNT, (
        f"TOOL_DISPATCH has {len(TOOL_DISPATCH)} tools; "
        f"EXPECTED_TOOL_COUNT is {EXPECTED_TOOL_COUNT}. "
        "If you intentionally added or removed a tool, update "
        "EXPECTED_TOOL_COUNT in this file. Otherwise revert the dispatch change."
    )
