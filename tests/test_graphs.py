"""Tests for coordination graph loading, validation, and in-memory representation."""

from __future__ import annotations

import pytest
import json
import tempfile
from pathlib import Path

from coordinationhub.plugins.graph.graphs import (
    validate_graph,
    CoordinationGraph,
    load_graph,
    find_graph_spec,
    set_graph,
    get_graph,
    clear_graph,
)


class TestGraphValidation:
    """Tests for validate_graph() top-level function."""

    def test_validate_valid_full_spec(self):
        data = {
            "agents": [
                {"id": "planner", "role": "decompose tasks", "model": "minimax-m2.7",
                 "responsibilities": ["break down stories", "assign subtasks"]},
                {"id": "executor", "role": "implement", "model": "minimax-m2.7",
                 "responsibilities": ["write code", "run tests"]},
            ],
            "handoffs": [
                {"from": "planner", "to": "executor", "condition": "task_size < 500"},
            ],
            "escalation": {"max_retries": 3, "fallback": "human_review"},
            "assessment": {"metrics": ["role_stability", "handoff_latency"]},
        }
        result = validate_graph(data)
        assert result["valid"] is True
        assert result["errors"] == []

    def test_validate_missing_agents_field(self):
        result = validate_graph({"handoffs": [], "escalation": {}})
        assert result["valid"] is False
        assert any("agents" in e for e in result["errors"])

    def test_validate_missing_required_agent_fields(self):
        data = {
            "agents": [
                {"id": "planner", "role": "decompose"},  # missing responsibilities
            ],
            "handoffs": [],
        }
        result = validate_graph(data)
        assert result["valid"] is False
        assert any("responsibilities" in e for e in result["errors"])

    def test_validate_duplicate_agent_id(self):
        data = {
            "agents": [
                {"id": "planner", "role": "a", "responsibilities": []},
                {"id": "planner", "role": "b", "responsibilities": []},
            ],
            "handoffs": [],
        }
        result = validate_graph(data)
        assert result["valid"] is False
        assert any("duplicate" in e for e in result["errors"])

    def test_validate_unknown_agent_in_handoff(self):
        data = {
            "agents": [{"id": "planner", "role": "a", "responsibilities": []}],
            "handoffs": [
                {"from": "planner", "to": "nonexistent", "condition": "always"},
            ],
        }
        result = validate_graph(data)
        assert result["valid"] is False
        assert any("nonexistent" in e for e in result["errors"])

    def test_validate_handoffs_agents_not_required_for_validation(self):
        # When agent_ids set is empty (no agents defined), handoffs are still syntactically valid
        data = {
            "agents": [],
            "handoffs": [{"from": "a", "to": "b", "condition": "always"}],
        }
        result = validate_graph(data)
        # Should pass syntactic validation (no unknown agent refs since no agents to check)
        assert result["valid"] is True

    def test_validate_escalation_invalid_max_retries(self):
        data = {
            "agents": [{"id": "planner", "role": "a", "responsibilities": []}],
            "handoffs": [],
            "escalation": {"max_retries": "not_an_int", "fallback": "human"},
        }
        result = validate_graph(data)
        assert result["valid"] is False

    def test_validate_assessment_metrics_not_list(self):
        data = {
            "agents": [{"id": "planner", "role": "a", "responsibilities": []}],
            "handoffs": [],
            "assessment": {"metrics": "not_a_list"},
        }
        result = validate_graph(data)
        assert result["valid"] is False


class TestCoordinationGraph:
    """Tests for CoordinationGraph in-memory representation."""

    def test_coordination_graph_agents_lookup(self):
        data = {
            "agents": [
                {"id": "planner", "role": "decompose", "responsibilities": ["a", "b"]},
                {"id": "executor", "role": "implement", "responsibilities": ["c"]},
            ],
            "handoffs": [{"from": "planner", "to": "executor", "condition": "x"}],
            "escalation": {"max_retries": 3, "fallback": "human"},
            "assessment": {"metrics": ["role_stability"]},
        }
        graph = CoordinationGraph(data)
        assert graph.agent("planner")["role"] == "decompose"
        assert graph.agent("executor")["responsibilities"] == ["c"]
        assert graph.agent("nonexistent") is None

    def test_coordination_graph_handoffs(self):
        data = {
            "agents": [{"id": "p", "role": "r", "responsibilities": []}],
            "handoffs": [
                {"from": "p", "to": "x", "condition": "c1"},
                {"from": "p", "to": "y", "condition": "c2"},
            ],
        }
        graph = CoordinationGraph(data)
        assert set(graph.handoff_targets("p")) == {"x", "y"}
        assert graph.handoff_targets("nonexistent") == []

    def test_coordination_graph_is_valid(self):
        data = {
            "agents": [{"id": "p", "role": "r", "responsibilities": []}],
            "handoffs": [],
        }
        graph = CoordinationGraph(data)
        assert graph.is_valid() is True

    def test_coordination_graph_validation_errors(self):
        data = {
            "agents": [{"id": "p"}],  # missing role + responsibilities
            "handoffs": [],
        }
        graph = CoordinationGraph(data)
        assert graph.is_valid() is False
        errors = graph.validation_errors()
        assert len(errors) > 0

    def test_coordination_graph_agent_ids(self):
        data = {
            "agents": [
                {"id": "a", "role": "r", "responsibilities": []},
                {"id": "b", "role": "s", "responsibilities": []},
            ],
            "handoffs": [],
        }
        graph = CoordinationGraph(data)
        assert set(graph.agent_ids()) == {"a", "b"}

    def test_coordination_graph_properties(self):
        data = {
            "agents": [{"id": "x", "role": "y", "responsibilities": ["z"]}],
            "handoffs": [],
            "escalation": {"max_retries": 2, "fallback": "boss"},
            "assessment": {"metrics": ["a", "b"]},
        }
        graph = CoordinationGraph(data)
        assert graph.escalation == {"max_retries": 2, "fallback": "boss"}
        assert graph.assessment == {"metrics": ["a", "b"]}
        assert graph.raw == data


class TestGraphModuleSingleton:
    """Tests for module-level graph state management."""

    def test_set_and_get_graph(self):
        clear_graph()
        assert get_graph() is None
        data = {
            "agents": [{"id": "t", "role": "t", "responsibilities": []}],
            "handoffs": [],
        }
        graph = set_graph(data)
        assert get_graph() is graph
        assert get_graph().agent("t")["role"] == "t"
        clear_graph()

    def test_clear_graph(self):
        data = {
            "agents": [{"id": "t", "role": "t", "responsibilities": []}],
            "handoffs": [],
        }
        set_graph(data)
        clear_graph()
        assert get_graph() is None


class TestLoadGraph:
    """Tests for load_graph() from file."""

    def test_load_json_graph(self, tmp_path):
        spec = tmp_path / "coordination_spec.json"
        spec.write_text(json.dumps({
            "agents": [{"id": "p", "role": "r", "responsibilities": []}],
            "handoffs": [],
        }))
        data = load_graph(spec)
        assert data["agents"][0]["id"] == "p"

    def test_load_unknown_extension_falls_back_to_json(self, tmp_path):
        # load_graph treats unknown extensions as JSON
        spec = tmp_path / "coordination_spec.foo"
        spec.write_text(json.dumps({"agents": [], "handoffs": []}))
        data = load_graph(spec)
        assert isinstance(data, dict)


class TestFindGraphSpec:
    """Tests for find_graph_spec()."""

    def test_finds_yaml_before_json(self, tmp_path):
        (tmp_path / "coordination_spec.yaml").write_text("agents: []\nhandoffs: []")
        (tmp_path / "coordination_spec.json").write_text("{}")
        result = find_graph_spec(tmp_path)
        assert result is not None
        assert result.name == "coordination_spec.yaml"

    def test_finds_json_when_no_yaml(self, tmp_path):
        (tmp_path / "coordination_spec.json").write_text("{}")
        result = find_graph_spec(tmp_path)
        assert result is not None
        assert result.name == "coordination_spec.json"

    def test_returns_none_when_no_file(self, tmp_path):
        assert find_graph_spec(tmp_path) is None

    def test_returns_none_for_none_project_root(self):
        assert find_graph_spec(None) is None
