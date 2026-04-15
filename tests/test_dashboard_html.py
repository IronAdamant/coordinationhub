"""Sanity checks on the embedded dashboard HTML / JS template.

These guard against silent template-string regressions like the v0.7.2
``html.push('<span ...">' + escapeHTML(path);`` typo (missing closing
``)``), which broke every dashboard load with active locks across three
shipped releases (v0.7.2 / v0.7.3 / v0.7.4) before being caught by a
browser console error.
"""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest

from coordinationhub.plugins.dashboard.dashboard_html import DASHBOARD_HTML


def _extract_script() -> str:
    """Return the JavaScript body inside the dashboard's single <script> tag."""
    matches = re.findall(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL)
    assert len(matches) == 1, (
        f"Expected exactly one <script> block in DASHBOARD_HTML, found {len(matches)}. "
        "Multiple scripts indicate a rendering bug like the prematurely-closed "
        "</script> from v0.7.1."
    )
    return matches[0]


class TestDashboardHTMLStructure:
    def test_single_script_block(self) -> None:
        """The dashboard must have exactly one <script>...</script> block."""
        opens = DASHBOARD_HTML.count("<script>")
        closes = DASHBOARD_HTML.count("</script>")
        assert opens == 1, f"expected 1 <script>, found {opens}"
        assert closes == 1, f"expected 1 </script>, found {closes}"

    def test_balanced_panel_markup(self) -> None:
        """Every panel container should be opened and closed in balance."""
        # Quick sanity — count <div class="panel"> openers
        panels = DASHBOARD_HTML.count('class="panel')
        assert panels >= 6, f"expected at least 6 dashboard panels, found {panels}"


class TestDashboardJSSyntax:
    """Verify the embedded JavaScript actually parses.

    Uses ``node --check`` when Node is available on the runner. Skipped
    otherwise — this guard exists to catch typos like a missing ``)``,
    not to require Node as a hard dev dependency.
    """

    def test_js_parses_with_node(self, tmp_path) -> None:
        node = shutil.which("node")
        if node is None:
            pytest.skip("node not installed; skipping JS syntax check")

        script = _extract_script()
        js_file = tmp_path / "dashboard.js"
        js_file.write_text(script)

        result = subprocess.run(
            [node, "--check", str(js_file)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, (
            "Dashboard JavaScript fails to parse:\n"
            f"--- node stderr ---\n{result.stderr}\n"
            "Most likely cause: an unbalanced parenthesis / brace / bracket "
            "introduced when editing a string-builder block in dashboard_html.py."
        )
