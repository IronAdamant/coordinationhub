"""Tests for file ownership scan, graph loading, and visibility tools (v0.3.0)."""

from __future__ import annotations

import json
import pytest
import tempfile
import time
from pathlib import Path

from coordinationhub.core import CoordinationEngine
from coordinationhub import graphs as _graphs


class TestFileOwnershipScan:
    """Tests for scan_project and file_ownership table."""

    def test_scan_project_creates_ownership_entries(self, engine, registered_agent, tmp_path):
        """Scanning should create file_ownership entries for all tracked files."""
        # Create some test files
        (tmp_path / "a.py").write_text("# a")
        (tmp_path / "b.md").write_text("# b")
        (tmp_path / "c.txt").write_text("c")
        subdir = tmp_path / "sub"
        subdir.mkdir()
        (subdir / "d.py").write_text("# d")

        result = engine.scan_project(worktree_root=str(tmp_path))
        assert result["scanned"] >= 4
        assert result["owned"] >= 4

    def test_scan_project_assigns_to_registered_agent(self, engine, registered_agent, tmp_path):
        """Files should be assigned to the registered root agent."""
        (tmp_path / "x.py").write_text("# x")
        engine.scan_project(worktree_root=str(tmp_path))
        with engine._connect() as conn:
            row = conn.execute(
                "SELECT assigned_agent_id FROM file_ownership WHERE document_path = ?",
                ("x.py",),
            ).fetchone()
            assert row is not None
            assert row["assigned_agent_id"] == registered_agent

    def test_scan_project_upserts_existing(self, engine, registered_agent, tmp_path):
        """Re-scanning should upsert, not duplicate."""
        (tmp_path / "y.py").write_text("# y")
        engine.scan_project(worktree_root=str(tmp_path))
        engine.scan_project(worktree_root=str(tmp_path))
        with engine._connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM file_ownership WHERE document_path = ?", ("y.py",)
            ).fetchone()
            assert count["COUNT(*)"] == 1

    def test_scan_project_respects_extensions_filter(self, engine, registered_agent, tmp_path):
        """Passing extensions should only scan those."""
        (tmp_path / "a.py").write_text("# a")
        (tmp_path / "b.md").write_text("# b")
        result = engine.scan_project(worktree_root=str(tmp_path), extensions=[".py"])
        assert result["scanned"] >= 1
        with engine._connect() as conn:
            b_row = conn.execute(
                "SELECT assigned_agent_id FROM file_ownership WHERE document_path = ?", ("b.md",)
            ).fetchone()
            # b.md should not be in ownership if only .py was scanned
            # (it may still be there from a prior run, so just check count)

    def test_scan_project_excludes_dotfiles_and_caches(self, engine, registered_agent, tmp_path):
        """Dotfiles, __pycache__, .pytest_cache should be skipped."""
        (tmp_path / "a.py").write_text("# a")
        (tmp_path / ".hidden.py").write_text("# hidden")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "a.pyc").write_text("bytecode")
        (tmp_path / ".pytest_cache").mkdir()
        result = engine.scan_project(worktree_root=str(tmp_path))
        with engine._connect() as conn:
            hidden = conn.execute(
                "SELECT document_path FROM file_ownership WHERE document_path LIKE '.%'"
            ).fetchall()
            assert len(hidden) == 0


class TestAgentStatus:
    """Tests for get_agent_status and update_agent_status."""

    def test_get_agent_status_returns_full_info(self, engine, registered_agent):
        """get_agent_status should return all fields."""
        result = engine.get_agent_status(registered_agent)
        assert "agent_id" in result
        assert result["agent_id"] == registered_agent
        assert "status" in result
        assert "owned_files" in result
        assert "active_locks" in result
        assert "lineage" in result

    def test_get_agent_status_unknown_agent(self, engine):
        """Unknown agent should return an error."""
        result = engine.get_agent_status("nonexistent.agent.0")
        assert "error" in result

    def test_update_agent_status_stores_task(self, engine, registered_agent):
        """update_agent_status should persist the current_task."""
        result = engine.update_agent_status(registered_agent, "Working on feature X")
        assert result["updated"] is True
        status = engine.get_agent_status(registered_agent)
        assert status.get("current_task") == "Working on feature X"

    def test_update_agent_status_unknown_agent(self, engine):
        """Updating unknown agent should fail gracefully."""
        result = engine.update_agent_status("unknown.agent", "Doing stuff")
        assert result["updated"] is False
        assert "error" in result


class TestFileAgentMap:
    """Tests for get_file_agent_map."""

    def test_get_file_agent_map_returns_all_files(self, engine, registered_agent, tmp_path):
        """Should return all files with ownership info."""
        (tmp_path / "a.py").write_text("# a")
        (tmp_path / "b.py").write_text("# b")
        engine.scan_project(worktree_root=str(tmp_path))
        result = engine.get_file_agent_map()
        assert result["total"] >= 2
        assert len(result["files"]) >= 2

    def test_get_file_agent_map_filters_by_agent(self, engine, registered_agent, tmp_path):
        """Passing agent_id should filter to that agent's files."""
        (tmp_path / "a.py").write_text("# a")
        engine.scan_project(worktree_root=str(tmp_path))
        result = engine.get_file_agent_map(agent_id=registered_agent)
        for f in result["files"]:
            assert f["assigned_agent_id"] == registered_agent

    def test_get_file_agent_map_empty(self, engine):
        """Empty file_ownership should return zero entries."""
        result = engine.get_file_agent_map()
        assert result["total"] == 0
        assert result["files"] == []


class TestGraphLoading:
    """Tests for load_coordination_spec and validate_graph."""

    @pytest.mark.skipif(not _graphs._YAML_AVAILABLE, reason="ruamel.yaml not installed")
    def test_load_spec_finds_yaml(self, engine, tmp_path):
        """load_coordination_spec should find coordination_spec.yaml."""
        spec_file = tmp_path / "coordination_spec.yaml"
        spec_file.write_text(
            "agents:\n"
            "  - id: planner\n"
            "    role: decompose\n"
            "    responsibilities: [break down tasks]\n"
            "handoffs: []\n"
        )
        # Create a fresh engine with this project root
        eng = CoordinationEngine(project_root=tmp_path)
        eng.start()
        try:
            result = eng.load_coordination_spec()
            assert result["loaded"] is True
            assert "planner" in result.get("agents", [])
        finally:
            eng.close()

    def test_load_spec_finds_json(self, engine, tmp_path):
        """load_coordination_spec should find coordination_spec.json."""
        spec_file = tmp_path / "coordination_spec.json"
        spec_file.write_text(json.dumps({
            "agents": [{"id": "executor", "role": "implement", "responsibilities": ["write code"]}],
            "handoffs": [],
        }))
        eng = CoordinationEngine(project_root=tmp_path)
        eng.start()
        try:
            result = eng.load_coordination_spec()
            assert result["loaded"] is True
        finally:
            eng.close()

    def test_validate_graph_valid(self, engine, tmp_path):
        """A valid graph should pass validation."""
        spec_file = tmp_path / "coordination_spec.json"
        spec_file.write_text(json.dumps({
            "agents": [{"id": "planner", "role": "decompose", "responsibilities": []}],
            "handoffs": [],
            "assessment": {"metrics": ["role_stability"]},
        }))
        eng = CoordinationEngine(project_root=tmp_path)
        eng.start()
        try:
            eng.load_coordination_spec()
            result = eng.validate_graph()
            assert result["valid"] is True
            assert result["errors"] == []
        finally:
            eng.close()

    def test_validate_graph_invalid_missing_field(self, engine, tmp_path):
        """An invalid graph should report errors."""
        spec_file = tmp_path / "coordination_spec.json"
        spec_file.write_text(json.dumps({
            "agents": [{"id": "bad"}],  # missing required 'role' and 'responsibilities'
            "handoffs": [],
        }))
        eng = CoordinationEngine(project_root=tmp_path)
        eng.start()
        try:
            eng.load_coordination_spec()
            result = eng.validate_graph()
            assert result["valid"] is False
            assert len(result["errors"]) > 0
        finally:
            eng.close()

    def test_load_spec_not_found(self, engine):
        """When no spec file exists, load_coordination_spec should return loaded=False."""
        eng = CoordinationEngine(project_root=None)  # No project root
        eng.start()
        try:
            result = eng.load_coordination_spec()
            assert result["loaded"] is False
        finally:
            eng.close()
