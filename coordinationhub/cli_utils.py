"""Shared CLI helper functions used by all cli_* sub-modules."""

from __future__ import annotations

import argparse
import functools
import json
import logging
from pathlib import Path
from typing import Any

from .core import CoordinationEngine

logger = logging.getLogger(__name__)


def print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, default=str))


def engine_from_args(args: argparse.Namespace) -> CoordinationEngine:
    storage_dir = Path(args.storage_dir) if args.storage_dir else None
    project_root = Path(args.project_root) if args.project_root else None
    namespace = getattr(args, "namespace", "hub")
    engine = CoordinationEngine(storage_dir=storage_dir, project_root=project_root, namespace=namespace)
    engine.start()
    return engine


def replica_engine_from_args(args: argparse.Namespace) -> CoordinationEngine:
    """Return a read-replica engine when --replica is set, otherwise a normal engine."""
    if getattr(args, "replica", False):
        storage_dir = Path(args.storage_dir) if args.storage_dir else None
        project_root = Path(args.project_root) if args.project_root else None
        namespace = getattr(args, "namespace", "hub")
        engine = CoordinationEngine(storage_dir=storage_dir, project_root=project_root, namespace=namespace)
        engine.start()
        return engine.read_only_engine()
    return engine_from_args(args)


def close(engine: CoordinationEngine) -> None:
    try:
        engine.close()
    except Exception:
        logger.debug("Engine close raised an exception", exc_info=True)


# ------------------------------------------------------------------ #
# Command decorators — eliminate engine lifecycle boilerplate
# ------------------------------------------------------------------ #

def command(*, replica: bool = False):
    """Decorator for CLI commands that need an engine.

    The wrapped function receives ``(engine, args)`` instead of ``(args,)``.
    Engine creation (read-replica when *replica=True*) and cleanup are
    handled automatically.
    """
    def decorator(cmd_func):
        @functools.wraps(cmd_func)
        def wrapper(args):
            fn = replica_engine_from_args if replica else engine_from_args
            engine = fn(args)
            try:
                return cmd_func(engine, args)
            finally:
                close(engine)
        return wrapper
    return decorator
