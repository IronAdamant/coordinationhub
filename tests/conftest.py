"""Pytest fixtures for CoordinationHub tests using in-memory SQLite."""

from __future__ import annotations

import pytest
import tempfile
from coordinationhub.core import CoordinationEngine


@pytest.fixture
def engine():
    """Fresh in-memory CoordinationEngine for each test."""
    with tempfile.TemporaryDirectory() as tmpdir:
        eng = CoordinationEngine(storage_dir=tmpdir)
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
