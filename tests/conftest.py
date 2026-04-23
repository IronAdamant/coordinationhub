"""Pytest fixtures for CoordinationHub tests using in-memory SQLite."""

from __future__ import annotations

import threading
import pytest
import tempfile
from typing import Any, Callable, Sequence
from coordinationhub.core import CoordinationEngine


def run_concurrent(
    n: int,
    target: Callable[..., Any],
    args_per_worker: Sequence[tuple] | None = None,
    timeout: float = 10.0,
) -> tuple[list[Any], list[BaseException]]:
    """Run `target` in `n` threads that all release from a barrier simultaneously.

    T5.1 regression test helper. Earlier concurrency tests started threads in
    a for-loop (`for t in threads: t.start()`); by the time thread N-1 started,
    thread 0 may already have completed. That does not actually exercise
    concurrency. With a Barrier every worker arrives at the critical section
    inside one scheduler tick, maximising overlap.

    Returns (results, errors) where results[i] is what target(args_per_worker[i])
    returned (or None on error) and errors is a flat list of exceptions.
    """
    if args_per_worker is None:
        args_per_worker = [() for _ in range(n)]
    assert len(args_per_worker) == n, "args_per_worker must have length n"

    barrier = threading.Barrier(n)
    results: list[Any] = [None] * n
    errors: list[BaseException] = []
    errors_lock = threading.Lock()

    def _worker(idx: int) -> None:
        try:
            barrier.wait(timeout=timeout)
            results[idx] = target(*args_per_worker[idx])
        except BaseException as exc:
            with errors_lock:
                errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=timeout)

    return results, errors


@pytest.fixture
def engine(tmp_path):
    """Fresh in-memory CoordinationEngine for each test.

    T2.2: project_root is pinned to pytest's ``tmp_path`` and storage
    lives in a subdir of the same path so tests that pass
    ``worktree_root=str(tmp_path)`` to scan_project don't trip the scan-
    root validator (which rejects walks outside the engine's configured
    project root). Tests that need a different project root can build
    their own CoordinationEngine.
    """
    storage_dir = tmp_path / "_coordhub_storage"
    storage_dir.mkdir(exist_ok=True)
    eng = CoordinationEngine(
        storage_dir=str(storage_dir),
        project_root=tmp_path,
    )
    eng.start()
    yield eng
    eng.close()


@pytest.fixture
def registered_agent(engine):
    """A registered root agent."""
    aid = engine.generate_agent_id()
    engine.register_agent(aid)
    return aid


@pytest.fixture
def two_agents(engine):
    """Two registered sibling agents under the same parent."""
    parent = engine.generate_agent_id()
    engine.register_agent(parent)
    child = engine.generate_agent_id(parent)
    engine.register_agent(child, parent)
    other = engine.generate_agent_id(parent)
    engine.register_agent(other, parent)
    return {"parent": parent, "child": child, "other": other}
