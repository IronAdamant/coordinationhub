"""Tests for cli.py — command-line argument parser and dispatch."""

from __future__ import annotations

import pytest
from coordinationhub.cli import create_parser, _COMMANDS


EXPECTED_COMMANDS = {
    "serve", "serve-mcp", "status", "register", "heartbeat", "deregister",
    "list-agents", "lineage", "siblings", "acquire-lock", "release-lock",
    "refresh-lock", "lock-status", "list-locks", "release-agent-locks",
    "reap-expired-locks", "reap-stale-agents", "broadcast", "wait-for-locks",
    "notify-change", "get-notifications", "prune-notifications", "get-conflicts", "contention-hotspots",
    "load-spec", "validate-spec", "scan-project", "dashboard",
    "agent-status", "assess", "agent-tree",
    "doctor", "init", "watch",
}


class TestCreateParser:
    """Tests for create_parser — verifies all subcommands are registered."""

    def test_all_expected_commands_registered(self):
        """Every expected subcommand maps to a handler in _COMMANDS."""
        parser = create_parser()
        # argparse subparsers names
        sub_names = {
            action.option_strings[0].lstrip("-") if action.option_strings else None
            for action in parser._subparsers._group_actions
        }
        # _COMMANDS keys should match expected commands
        assert set(_COMMANDS.keys()) == EXPECTED_COMMANDS

    def test_register_command_parses_required_args(self):
        """The register subcommand accepts agent_id and --parent-id."""
        parser = create_parser()
        args = parser.parse_args(["register", "my-agent", "--parent-id", "parent.1"])
        assert args.agent_id == "my-agent"
        assert args.parent_id == "parent.1"

    def test_acquire_lock_parses_all_args(self):
        """acquire-lock requires document_path and agent_id, accepts lock-type/ttl/force."""
        parser = create_parser()
        args = parser.parse_args([
            "acquire-lock", "/path/to/file.py", "agent.1",
            "--lock-type", "shared", "--ttl", "60", "--force",
        ])
        assert args.document_path == "/path/to/file.py"
        assert args.agent_id == "agent.1"
        assert args.lock_type == "shared"
        assert args.ttl == 60.0
        assert args.force is True

    def test_notify_change_requires_three_args(self):
        """notify-change requires document_path, change_type, agent_id."""
        parser = create_parser()
        args = parser.parse_args([
            "notify-change", "src/main.py", "modified", "agent.1",
        ])
        assert args.document_path == "src/main.py"
        assert args.change_type == "modified"
        assert args.agent_id == "agent.1"

    def test_get_notifications_optional_args(self):
        """get-notifications has --since, --exclude-agent, --limit as optional."""
        parser = create_parser()
        args = parser.parse_args([
            "get-notifications",
            "--since", "123456.0",
            "--exclude-agent", "dead.agent",
            "--limit", "50",
        ])
        assert args.since == 123456.0
        assert args.exclude_agent == "dead.agent"
        assert args.limit == 50

    def test_assess_requires_suite_path(self):
        """assess requires --suite and accepts --format, --output, --graph-agent-id."""
        parser = create_parser()
        args = parser.parse_args([
            "assess", "--suite", "/path/to/suite.json",
            "--format", "json", "--output", "/tmp/report.md",
            "--graph-agent-id", "planner",
        ])
        assert args.suite_path == "/path/to/suite.json"
        assert args.format == "json"
        assert args.output_path == "/tmp/report.md"
        assert args.graph_agent_id == "planner"

    def test_shared_args_present(self):
        """--storage-dir, --project-root, --namespace, --json are available on each subcommand."""
        parser = create_parser()
        # Shared args via parents= are parsed as part of the subparser,
        # so they come AFTER the subcommand name.
        args = parser.parse_args([
            "status",
            "--storage-dir", "/tmp/store",
            "--project-root", "/proj",
            "--namespace", "test",
            "--json",
        ])
        assert args.storage_dir == "/tmp/store"
        assert args.project_root == "/proj"
        assert args.namespace == "test"
        assert args.json_output is True

    def test_no_command_shows_help(self):
        """With no subcommand, parser.parse_args([]) returns None as dest."""
        parser = create_parser()
        # parse_args without args uses sys.argv; use a known-empty list instead
        # to verify the default dest behavior
        # The 'command' dest will be None when no subcommand is given
        # We test the structure: no error on parse_args with empty list
        # Note: parser may exit on sys.argv; test structure via parse_known_args
        ns, extras = parser.parse_known_args([])
        assert ns.command is None

    def test_broadcast_accepts_document_path_option(self):
        """broadcast accepts optional --document-path."""
        parser = create_parser()
        args = parser.parse_args([
            "broadcast", "agent.1", "--document-path", "src/shared.py",
        ])
        assert args.agent_id == "agent.1"
        assert args.document_path == "src/shared.py"

    def test_wait_for_locks_accepts_document_paths_list(self):
        """wait-for-locks accepts multiple document paths as positional args."""
        parser = create_parser()
        args = parser.parse_args([
            "wait-for-locks", "agent.1", "a.txt", "b.py", "c.json",
            "--timeout", "30",
        ])
        assert args.agent_id == "agent.1"
        assert args.document_paths == ["a.txt", "b.py", "c.json"]
        assert args.timeout == 30.0

    def test_scan_project_extensions_option(self):
        """scan-project accepts --extensions as a list."""
        parser = create_parser()
        args = parser.parse_args([
            "scan-project", "--extensions", ".py", ".md", "--worktree-root", "/src",
        ])
        assert args.extensions == [".py", ".md"]
        assert args.worktree_root == "/src"
