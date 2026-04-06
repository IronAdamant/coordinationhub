"""Tests for assessment runner."""

from __future__ import annotations

import json
import pytest
import tempfile
from pathlib import Path

from coordinationhub.assessment import (
    load_suite,
    run_assessment,
    format_markdown_report,
    score_role_stability,
    score_handoff_latency,
    score_outcome_verifiability,
    score_protocol_adherence,
    score_spawn_propagation,
    _suggest_graph_refinements,
)
from coordinationhub.graphs import CoordinationGraph, set_graph, clear_graph


MINIMAL_GRAPH = CoordinationGraph({
    "agents": [
        {"id": "planner", "role": "decompose", "responsibilities": ["plan", "document"]},
        {"id": "executor", "role": "implement", "responsibilities": ["implement", "write code"]},
    ],
    "handoffs": [{"from": "planner", "to": "executor", "condition": "always"}],
    "assessment": {"metrics": ["role_stability", "handoff_latency", "outcome_verifiability", "protocol_adherence"]},
})


class TestMetricScorers:
    """Tests for individual metric scorer functions."""

    def test_score_role_stability(self):
        trace = {
            "trace_id": "t1",
            "events": [
                {"type": "register", "agent_id": "hub.1.0", "graph_id": "planner"},
                {"type": "lock", "path": "a.py", "agent_id": "hub.1.0"},
            ],
        }
        score = score_role_stability(trace, MINIMAL_GRAPH)
        assert 0.0 <= score <= 1.0

    def test_score_handoff_latency(self):
        trace = {
            "trace_id": "t1",
            "events": [
                {"type": "handoff", "from": "planner", "to": "executor", "condition": "always"},
            ],
        }
        score = score_handoff_latency(trace, MINIMAL_GRAPH)
        assert 0.0 <= score <= 1.0

    def test_score_handoff_latency_no_handoffs(self):
        trace = {"trace_id": "t1", "events": []}
        score = score_handoff_latency(trace, MINIMAL_GRAPH)
        assert score == 1.0  # vacuously correct

    def test_score_outcome_verifiability(self):
        trace = {
            "trace_id": "t1",
            "events": [
                {"type": "lock", "path": "a.py", "agent_id": "hub.1.0"},
                {"type": "modified", "path": "a.py", "agent_id": "hub.1.0"},
            ],
        }
        score = score_outcome_verifiability(trace, None)
        assert 0.0 <= score <= 1.0

    def test_score_outcome_verifiability_lock_without_modify(self):
        """Lock then unlock without modification should score 0.0."""
        trace = {
            "trace_id": "t1",
            "events": [
                {"type": "lock", "path": "a.py", "agent_id": "hub.1.0"},
                {"type": "unlock", "path": "a.py", "agent_id": "hub.1.0"},
            ],
        }
        score = score_outcome_verifiability(trace, None)
        assert score == 0.0

    def test_score_outcome_verifiability_lock_modify_unlock(self):
        """Lock → modify → unlock with no intervening issues scores 1.0."""
        trace = {
            "trace_id": "t1",
            "events": [
                {"type": "lock", "path": "a.py", "agent_id": "hub.1.0"},
                {"type": "modified", "path": "a.py", "agent_id": "hub.1.0"},
                {"type": "unlock", "path": "a.py", "agent_id": "hub.1.0"},
            ],
        }
        score = score_outcome_verifiability(trace, None)
        assert score == 1.0

    def test_score_outcome_verifiability_modify_without_lock(self):
        """Modification without ever locking scores 0.0."""
        trace = {
            "trace_id": "t1",
            "events": [
                {"type": "modified", "path": "a.py", "agent_id": "hub.1.0"},
            ],
        }
        score = score_outcome_verifiability(trace, None)
        assert score == 0.0

    def test_score_protocol_adherence(self):
        trace = {
            "trace_id": "t1",
            "events": [
                {"type": "register", "agent_id": "hub.1.0", "graph_id": "planner"},
            ],
        }
        score = score_protocol_adherence(trace, MINIMAL_GRAPH)
        assert 0.0 <= score <= 1.0

    def test_score_spawn_propagation_child_within_parent_scope(self):
        """Child agent acting within parent's responsibilities should score high."""
        trace = {
            "trace_id": "t1",
            "events": [
                {"type": "register", "agent_id": "hub.1.0", "graph_id": "planner", "parent_id": ""},
                {"type": "register", "agent_id": "hub.1.0.0", "graph_id": "", "parent_id": "hub.1.0"},
                # Child registers a lock — coordination primitives always score 1.0
                {"type": "lock", "path": "a.py", "agent_id": "hub.1.0.0"},
            ],
        }
        score = score_spawn_propagation(trace, MINIMAL_GRAPH)
        assert score == 1.0  # lock is always permitted

    def test_score_spawn_propagation_child_outside_parent_scope(self):
        """Child agent acting outside parent's responsibilities should score low."""
        trace = {
            "trace_id": "t1",
            "events": [
                {"type": "register", "agent_id": "hub.1.0", "graph_id": "planner", "parent_id": ""},
                {"type": "register", "agent_id": "hub.1.0.0", "graph_id": "", "parent_id": "hub.1.0"},
                # Child writes code but parent is "plan/document" only — violation
                {"type": "modified", "path": "app.py", "agent_id": "hub.1.0.0"},
            ],
        }
        score = score_spawn_propagation(trace, MINIMAL_GRAPH)
        assert score < 1.0  # outside parent scope

    def test_score_spawn_propagation_coordination_always_ok(self):
        """Lock/unlock/notify are always permitted regardless of scope."""
        trace = {
            "trace_id": "t1",
            "events": [
                {"type": "register", "agent_id": "hub.1.0", "graph_id": "planner", "parent_id": ""},
                {"type": "register", "agent_id": "hub.1.0.0", "graph_id": "", "parent_id": "hub.1.0"},
                {"type": "lock", "path": "a.py", "agent_id": "hub.1.0.0"},
                {"type": "unlock", "path": "a.py", "agent_id": "hub.1.0.0"},
            ],
        }
        score = score_spawn_propagation(trace, MINIMAL_GRAPH)
        assert score == 1.0  # coordination primitives always ok

    def test_score_spawn_propagation_empty_trace(self):
        """Empty trace should score 1.0."""
        trace = {"trace_id": "t1", "events": []}
        score = score_spawn_propagation(trace, MINIMAL_GRAPH)
        assert score == 1.0


class TestRunAssessment:
    """Tests for run_assessment()."""

    def test_run_assessment_with_graph(self):
        suite = {
            "name": "test_suite",
            "traces": [
                {
                    "trace_id": "trace_001",
                    "events": [
                        {"type": "register", "agent_id": "hub.1.0", "graph_id": "planner"},
                        {"type": "handoff", "from": "planner", "to": "executor", "condition": "always"},
                    ],
                },
            ],
        }
        result = run_assessment(suite, MINIMAL_GRAPH)
        assert result["suite_name"] == "test_suite"
        assert "overall_score" in result
        assert "metrics" in result
        assert result["graph_loaded"] is True
        assert "trace_001" in result["traces"]

    def test_run_assessment_without_graph(self):
        suite = {
            "name": "no_graph_suite",
            "traces": [{"trace_id": "t1", "events": []}],
        }
        result = run_assessment(suite, None)
        assert result["graph_loaded"] is False
        assert result["overall_score"] >= 0.0

    def test_run_assessment_empty_traces(self):
        suite = {"name": "empty", "traces": []}
        result = run_assessment(suite, MINIMAL_GRAPH)
        assert result["overall_score"] >= 0.0

    def test_run_assessment_all_metrics_present(self):
        suite = {"name": "s", "traces": [{"trace_id": "t", "events": []}]}
        result = run_assessment(suite, MINIMAL_GRAPH)
        expected_metrics = {"role_stability", "handoff_latency", "outcome_verifiability", "protocol_adherence", "spawn_propagation"}
        assert set(result["metrics"].keys()) == expected_metrics

    def test_run_assessment_spawn_propagation_included(self):
        suite = {"name": "s", "traces": [{"trace_id": "t", "events": []}]}
        result = run_assessment(suite, MINIMAL_GRAPH)
        assert "spawn_propagation" in result["metrics"]

    def test_run_assessment_graph_agent_id_filter(self):
        suite = {
            "name": "filtered",
            "traces": [
                {"trace_id": "t1", "events": [
                    {"type": "register", "agent_id": "hub.1.0", "graph_id": "planner"},
                    {"type": "handoff", "from": "planner", "to": "executor"},
                ]},
                {"trace_id": "t2", "events": [
                    {"type": "register", "agent_id": "hub.2.0", "graph_id": "executor"},
                    {"type": "modified", "path": "a.py", "agent_id": "hub.2.0"},
                ]},
            ],
        }
        result = run_assessment(suite, MINIMAL_GRAPH, graph_agent_id="planner")
        assert result["graph_agent_id_filter"] == "planner"
        # Only t1 should be scored (it has the planner register event)
        assert len(result["traces"]) == 1
        assert "t1" in result["traces"]

    def test_run_assessment_stores_full_trace(self):
        suite = {
            "name": "trace_test",
            "traces": [
                {"trace_id": "t1", "events": [
                    {"type": "register", "agent_id": "hub.1.0", "graph_id": "planner"},
                ]},
            ],
        }
        result = run_assessment(suite, MINIMAL_GRAPH)
        assert result.get("full_trace_json") is not None
        parsed = json.loads(result["full_trace_json"])
        assert len(parsed) == 1
        assert parsed[0]["trace_id"] == "t1"

    def test_run_assessment_suggested_refinements(self):
        suite = {
            "name": "refinement_test",
            "traces": [
                {"trace_id": "t1", "events": [
                    {"type": "register", "agent_id": "hub.99.0", "graph_id": "reviewer"},  # reviewer not in graph
                ]},
            ],
        }
        result = run_assessment(suite, MINIMAL_GRAPH)
        refinements = result.get("suggested_refinements", [])
        # Should suggest missing_agent for 'reviewer' (registered in trace but not in graph)
        assert any(r["type"] == "missing_agent" and r["graph_agent_id"] == "reviewer"
                   for r in refinements)


class TestFormatMarkdownReport:
    """Tests for format_markdown_report()."""

    def test_format_markdown_report(self):
        result = {
            "suite_name": "my_tests",
            "run_at": 1700000000.0,
            "graph_loaded": True,
            "overall_score": 0.85,
            "metrics": {
                "role_stability": 0.9,
                "handoff_latency": 0.8,
                "outcome_verifiability": 0.85,
                "protocol_adherence": 0.85,
            },
            "traces": {
                "trace_001": {
                    "role_stability": 0.9,
                    "handoff_latency": 0.8,
                    "outcome_verifiability": 0.85,
                    "protocol_adherence": 0.85,
                },
            },
        }
        report = format_markdown_report(result)
        assert "Assessment Report: my_tests" in report
        assert "**Overall Score:** 85.00%" in report
        assert "role_stability" in report

    def test_format_markdown_report_with_refinements(self):
        result = {
            "suite_name": "refine_test",
            "run_at": 1700000000.0,
            "graph_loaded": True,
            "overall_score": 0.75,
            "metrics": {"role_stability": 0.75, "handoff_latency": 0.75, "outcome_verifiability": 0.75, "protocol_adherence": 0.75, "spawn_propagation": 0.75},
            "traces": {},
            "graph_agent_id_filter": "planner",
            "suggested_refinements": [
                {"type": "missing_handoff", "from_agent": "planner", "to_agent": "reviewer",
                 "suggestion": "handoff from 'planner' to 'reviewer' is used in traces but not defined in graph",
                 "reason": "protocol_adherence"},
            ],
        }
        report = format_markdown_report(result)
        assert "refine_test" in report
        assert "**Filtered by:** graph_agent_id = planner" in report
        assert "Suggested Graph Refinements" in report
        assert "missing_handoff" in report


class TestLoadSuite:
    """Tests for load_suite()."""

    def test_load_suite_from_file(self, tmp_path):
        suite_file = tmp_path / "suite.json"
        suite_file.write_text(json.dumps({
            "name": "test", "traces": [{"trace_id": "t1", "events": []}],
        }))
        suite = load_suite(suite_file)
        assert suite["name"] == "test"
        assert len(suite["traces"]) == 1

    def test_load_suite_invalid_json(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not json {{{")
        with pytest.raises(json.JSONDecodeError):
            load_suite(bad_file)
