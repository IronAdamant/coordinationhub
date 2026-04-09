"""Shared CLI helper functions used by all cli_* sub-modules."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .core import CoordinationEngine


def print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, default=str))


def engine_from_args(args: argparse.Namespace) -> CoordinationEngine:
    storage_dir = Path(args.storage_dir) if args.storage_dir else None
    project_root = Path(args.project_root) if args.project_root else None
    namespace = getattr(args, "namespace", "hub")
    engine = CoordinationEngine(storage_dir=storage_dir, project_root=project_root, namespace=namespace)
    engine.start()
    return engine


def close(engine: CoordinationEngine) -> None:
    try:
        engine.close()
    except Exception:
        pass
