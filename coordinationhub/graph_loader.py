"""Coordination graph loading from disk.

Handles YAML (via ruamel.yaml) and JSON spec files, and spec-file auto-detection.
Zero internal dependencies.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Try YAML; degrade to JSON-only if not available
_YAML_AVAILABLE = False
try:
    from ruamel.yaml import YAML as _YAML

    _YAML_AVAILABLE = True
except ImportError:
    pass


def load_graph(path: Path) -> dict[str, Any]:
    """Load a coordination graph from a YAML or JSON file."""
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        if not _YAML_AVAILABLE:
            raise ImportError(
                "YAML support requires ruamel.yaml. "
                "Install it with: pip install coordinationhub[yaml] "
                "Or use coordination_spec.json instead."
            )
        yaml = _YAML(typ="safe")
        return yaml.load(text)  # type: ignore[return-value]
    return json.loads(text)


def find_graph_spec(project_root: Path | None) -> Path | None:
    """Look for coordination_spec.yaml then coordination_spec.yml then coordination_spec.json at project root."""
    if project_root is None:
        return None
    for candidate in [
        project_root / "coordination_spec.yaml",
        project_root / "coordination_spec.yml",
        project_root / "coordination_spec.json",
    ]:
        if candidate.is_file():
            return candidate
    return None
