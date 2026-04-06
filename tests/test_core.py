"""Tests for core.py — CoordinationEngine top-level methods and integration."""

from __future__ import annotations

import json
import pytest
import tempfile
from pathlib import Path

from coordinationhub.core import CoordinationEngine
from coordinationhub import graphs as _g
from coordinationhub import visibility as _v


class TestRegisterAgentWithGraph:
    """Tests for register_agent with graph_agent_id (responsibility storage path)."""

    def test_register_agent_stores_responsibilities(self, engine, tmp_path):
        """When graph_agent_id is provided, responsibilities are stored."""
        spec = tmp_path / "coordination_spec.json"
        spec.write_text(json.dumps({
            "agents": [
                {"id": "planner", "role": "decomposer", "model": "minimax-m2.7",
                 "responsibilities": ["break_down", "assign"]},
            ],
            "handoffs": [],
        }))
        engine.load_coordination_spec(str(spec))

        aid = engine.generate_agent_id()
        result = engine.register_agent(aid, graph_agent_id="planner")

        assert result["graph_agent_id"] == "planner"
        assert result["role"] == "decomposer"
        assert "break_down" in result["responsibilities"]

    def test_register_agent_unknown_graph_agent_id(self, engine, tmp_path):
        """Unknown graph_agent_id is silently ignored (no error raised)."""
        spec = tmp_path / "coordination_spec.json"
        spec.write_text(json.dumps({
            "agents": [{"id": "p", "role": "r", "responsibilities": ["x"]}],
            "handoffs": [],
        }))
        engine.load_coordination_spec(str(spec))

        aid = engine.generate_agent_id()
        result = engine.register_agent(aid, graph_agent_id="nonexistent_agent")
        # Should not raise — graph_agent_id is optional and non-matching values are ignored
        assert "responsibilities" not in result or result.get("responsibilities") == []

    def test_register_agent_without_graph(self, engine):
        """Without a loaded graph, register_agent still returns a context bundle."""
        aid = engine.generate_agent_id()
        result = engine.register_agent(aid)
        assert result["agent_id"] == aid
        assert "worktree_root" in result
        assert "registered_agents" in result
        assert result["graph_loaded"] is False


class TestLoadCoordinationSpec:
    """Tests for load_coordination_spec delegation to graphs.py."""

    def test_load_spec_from_file(self, engine, tmp_path):
        spec = tmp_path / "coordination_spec.json"
        spec.write_text(json.dumps({
            "agents": [{"id": "a", "role": "r", "responsibilities": []}],
            "handoffs": [],
        }))
        result = engine.load_coordination_spec(str(spec))
        assert result["loaded"] is True
        assert result["agent_count"] == 1
        assert "a" in result["agents"]

    def test_load_spec_auto_detects_yaml_before_json(self, engine, tmp_path):
        """YAML spec auto-detected when project_root is set and ruamel.yaml available."""
        (tmp_path / "coordination_spec.yaml").write_text(
            "agents:\n  - id: p\n    role: r\n    responsibilities: []\nhandoffs: []"
        )
        engine._project_root = tmp_path
        result = engine.load_coordination_spec()
        # Without ruamel.yaml installed, returns error; with it, loads successfully
        assert result.get("loaded") is True or result.get("error", "").startswith("YAML support")

    def test_load_spec_returns_error_for_invalid_spec(self, engine, tmp_path):
        spec = tmp_path / "coordination_spec.json"
        spec.write_text(json.dumps({
            "agents": [{"id": "p"}],  # missing role + responsibilities
            "handoffs": [],
        }))
        result = engine.load_coordination_spec(str(spec))
        assert result["loaded"] is False
        assert "errors" in result

    def test_load_spec_no_file(self, engine):
        result = engine.load_coordination_spec("/nonexistent/path/spec.json")
        assert result["loaded"] is False


class TestValidateGraph:
    """Tests for validate_graph delegation to graphs.py."""

    def test_validate_graph_valid(self, engine, tmp_path):
        spec = tmp_path / "coordination_spec.json"
        spec.write_text(json.dumps({
            "agents": [{"id": "p", "role": "r", "responsibilities": []}],
            "handoffs": [],
        }))
        engine.load_coordination_spec(str(spec))
        result = engine.validate_graph()
        assert result["valid"] is True
        assert result["errors"] == []

    def test_validate_graph_no_graph_loaded(self, engine):
        _g.clear_graph()
        result = engine.validate_graph()
        assert result["valid"] is False
        errors_text = " ".join(result["errors"]).lower()
        assert "no coordination graph" in errors_text or "no graph" in errors_text


class TestRunAssessment:
    """Tests for run_assessment error handling and format output."""

    def test_run_assessment_missing_file(self, engine):
        result = engine.run_assessment("/nonexistent/suite.json")
        assert "error" in result
        assert "not found" in result["error"].lower()

    def test_run_assessment_invalid_json(self, engine, tmp_path):
        bad = tmp_path / "bad_suite.json"
        bad.write_text("not json {{{")
        result = engine.run_assessment(str(bad))
        assert "error" in result

    def test_run_assessment_json_format(self, engine, tmp_path):
        suite = tmp_path / "suite.json"
        suite.write_text(json.dumps({
            "name": "test_suite",
            "traces": [{"trace_id": "t1", "events": []}],
        }))
        result = engine.run_assessment(str(suite), format="json")
        assert "overall_score" in result
        assert "metrics" in result
        assert result["suite_name"] == "test_suite"

    def test_run_assessment_markdown_format(self, engine, tmp_path):
        suite = tmp_path / "suite.json"
        suite.write_text(json.dumps({
            "name": "md_suite",
            "traces": [{"trace_id": "t1", "events": []}],
        }))
        result = engine.run_assessment(str(suite), format="markdown")
        assert "report" in result
        assert "scores" in result
        assert "md_suite" in result["report"]


class TestNormalizePath:
    """Tests for normalize_path edge cases."""

    def test_normalize_path_absolute(self, engine, tmp_path):
        """Absolute paths outside project root are returned as-is."""
        result = engine.acquire_lock("/tmp/some_file.txt", "hub.1.0")
        assert result["document_path"] == "/tmp/some_file.txt"

    def test_normalize_path_relative_outside_project_root(self, engine, tmp_path):
        """Absolute paths outside project root are returned as absolute."""
        from coordinationhub.paths import normalize_path
        engine._project_root = tmp_path
        # /tmp is always outside tmp_path (which is a subdir of /tmp/pytest-of-aron/...)
        result = normalize_path("/tmp/some_file.txt", tmp_path)
        assert result == "/tmp/some_file.txt"

    def test_normalize_path_file_inside_project_root(self, engine, tmp_path):
        """Files inside project root are returned as relative paths."""
        from coordinationhub.paths import normalize_path
        engine._project_root = tmp_path
        file_path = tmp_path / "project.py"
        file_path.write_text("# project")
        result = normalize_path(str(file_path), tmp_path)
        assert result == "project.py"

    def test_normalize_path_backslash_normalized(self, engine, tmp_path):
        """Windows-style backslashes are converted to forward slashes."""
        from coordinationhub.paths import normalize_path
        engine._project_root = tmp_path
        result = normalize_path("src\\app\\main.py", tmp_path)
        assert "\\" not in result


class TestDetectProjectRoot:
    """Tests for detect_project_root edge cases."""

    def test_detects_git_directory(self, tmp_path):
        from coordinationhub.paths import detect_project_root
        (tmp_path / ".git").mkdir()
        assert detect_project_root(str(tmp_path)) == tmp_path

    def test_walks_up_to_find_git(self, tmp_path):
        from coordinationhub.paths import detect_project_root
        subdir = tmp_path / "a" / "b"
        subdir.mkdir(parents=True)
        (tmp_path / ".git").mkdir()
        assert detect_project_root(str(subdir)) == tmp_path

    def test_returns_none_without_git(self, tmp_path):
        from coordinationhub.paths import detect_project_root
        subdir = tmp_path / "noroot"
        subdir.mkdir(parents=True)
        assert detect_project_root(str(subdir)) is None

    def test_returns_none_at_fs_root(self):
        from coordinationhub.paths import detect_project_root
        result = detect_project_root("/")
        assert result is None


class TestGenerateAgentId:
    """Tests for generate_agent_id."""

    def test_generate_agent_id_format(self, engine):
        """Agent IDs contain namespace, PID, and sequence number."""
        aid = engine.generate_agent_id()
        assert aid.startswith("hub.")
        parts = aid.split(".")
        assert len(parts) >= 3
        assert parts[1].isdigit()  # PID component

    def test_generate_agent_id_increments_sequence(self, engine):
        """Multiple calls without parent_id increment the sequence."""
        ids = [engine.generate_agent_id() for _ in range(3)]
        sequences = [int(a.split(".")[-1]) for a in ids]
        assert sequences == sorted(sequences)

    def test_generate_agent_id_with_parent(self, engine, registered_agent):
        """Passing parent_id creates a child ID under that parent."""
        child = engine.generate_agent_id(registered_agent)
        assert child.startswith(registered_agent + ".")
        assert child != registered_agent

    def test_generate_agent_id_unknown_parent_raises(self, engine):
        """Unknown parent_id raises ValueError."""
        with pytest.raises(ValueError, match="Parent agent not found"):
            engine.generate_agent_id("hub.unknown.parent.99")
