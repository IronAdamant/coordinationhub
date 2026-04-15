"""Tool-count regression test to enforce the consolidation target."""

from coordinationhub.dispatch import TOOL_DISPATCH


def test_tool_count_within_target() -> None:
    """Assert that the MCP tool surface stays at or below the post-consolidation target."""
    assert len(TOOL_DISPATCH) <= 50, (
        f"TOOL_DISPATCH has {len(TOOL_DISPATCH)} tools; target is <= 50. "
        "Add new tools only by consolidating existing ones."
    )
