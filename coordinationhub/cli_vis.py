"""Change awareness, audit, graph, and assessment CLI commands."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .cli_utils import print_json as _print_json, engine_from_args as _engine_from_args
from .cli_utils import replica_engine_from_args as _replica_engine_from_args, close as _close


# ------------------------------------------------------------------ #
# notify-change
# ------------------------------------------------------------------ #

def cmd_notify_change(args):
    engine = _engine_from_args(args)
    try:
        result = engine.notify_change(args.document_path, args.change_type, args.agent_id)
        if args.json_output:
            _print_json(result)
        else:
            print(f"Recorded: {args.change_type} on {args.document_path} by {args.agent_id}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# get-notifications
# ------------------------------------------------------------------ #

def cmd_get_notifications(args):
    engine = _replica_engine_from_args(args)
    try:
        result = engine.get_notifications(since=args.since, exclude_agent=args.exclude_agent, limit=args.limit)
        if args.json_output:
            _print_json(result)
        else:
            notifs = result.get("notifications", [])
            if not notifs:
                print("No notifications")
            else:
                print(f"{len(notifs)} notification(s):")
                for n in notifs:
                    print(f"  {n['document_path']}: {n['change_type']} by {n['agent_id']}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# prune-notifications
# ------------------------------------------------------------------ #

def cmd_prune_notifications(args):
    engine = _engine_from_args(args)
    try:
        result = engine.prune_notifications(max_age_seconds=args.max_age_seconds, max_entries=args.max_entries)
        if args.json_output:
            _print_json(result)
        else:
            print(f"Pruned {result.get('pruned', 0)} notification(s)")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# wait-for-notifications
# ------------------------------------------------------------------ #

def cmd_wait_for_notifications(args):
    engine = _engine_from_args(args)
    try:
        result = engine.wait_for_notifications(
            agent_id=args.agent_id,
            timeout_s=getattr(args, "timeout", 30.0),
            poll_interval_s=getattr(args, "poll_interval", 2.0),
            exclude_agent=getattr(args, "exclude_agent", None),
        )
        if args.json_output:
            _print_json(result)
        elif result.get("timed_out"):
            print(f"Timed out waiting for notifications (no new notifications)")
        else:
            notifs = result.get("notifications", [])
            print(f"Received {len(notifs)} notification(s):")
            for n in notifs:
                print(f"  {n['document_path']}: {n['change_type']} by {n['agent_id']}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# get-conflicts
# ------------------------------------------------------------------ #

def cmd_get_conflicts(args):
    engine = _replica_engine_from_args(args)
    try:
        result = engine.get_conflicts(document_path=args.document_path, agent_id=args.agent_id, limit=args.limit)
        if args.json_output:
            _print_json(result)
        else:
            conflicts = result.get("conflicts", [])
            if not conflicts:
                print("No conflicts")
            else:
                print(f"{len(conflicts)} conflict(s):")
                for c in conflicts:
                    print(f"  {c['document_path']}: {c['conflict_type']} ({c['agent_a']} vs {c['agent_b']})")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# contention-hotspots
# ------------------------------------------------------------------ #

def cmd_contention_hotspots(args):
    engine = _replica_engine_from_args(args)
    try:
        result = engine.get_contention_hotspots(limit=args.limit)
        if args.json_output:
            _print_json(result)
        else:
            hotspots = result.get("hotspots", [])
            if not hotspots:
                print("No contention hotspots")
            else:
                print(f"{len(hotspots)} contention hotspot(s):")
                for h in hotspots:
                    agents = ", ".join(h["agents_involved"])
                    print(f"  {h['document_path']}: {h['conflict_count']} conflicts ({agents})")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# load-spec
# ------------------------------------------------------------------ #

def cmd_load_spec(args):
    engine = _engine_from_args(args)
    try:
        path = getattr(args, "path", None)
        result = engine.load_coordination_spec(path)
        if args.json_output:
            _print_json(result)
        else:
            if result.get("loaded"):
                print(f"Graph loaded from: {result.get('path')}")
                print(f"  Agents: {result.get('agents', [])}")
            else:
                print("No graph loaded")
                if result.get("errors"):
                    for err in result["errors"]:
                        print(f"  Error: {err}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# validate-spec
# ------------------------------------------------------------------ #

def cmd_validate_spec(args):
    engine = _engine_from_args(args)
    try:
        result = engine.validate_graph()
        if args.json_output:
            _print_json(result)
        else:
            if result.get("valid"):
                print("Graph is valid")
            else:
                print("Graph validation errors:")
                for err in result.get("errors", []):
                    print(f"  {err}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# scan-project
# ------------------------------------------------------------------ #

def cmd_scan_project(args):
    engine = _engine_from_args(args)
    try:
        result = engine.scan_project(
            worktree_root=getattr(args, "worktree_root", None),
            extensions=getattr(args, "extensions", None),
        )
        if args.json_output:
            _print_json(result)
        else:
            print(f"Scanned: {result.get('scanned', 0)} files")
            print(f"Owned: {result.get('owned', 0)} files")
            if result.get("error"):
                print(f"Error: {result['error']}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# dashboard
# ------------------------------------------------------------------ #

def cmd_dashboard(args):
    engine = _engine_from_args(args)
    try:
        # Auto-reap stale agents so the dashboard matches list-agents output.
        # Review Fourteen found the two CLIs drifted when one called
        # reap_stale_agents and the other did not.
        engine.reap_stale_agents(timeout=600.0)
        status = engine.status()
        agents_result = engine.list_agents(active_only=False)
        agents = agents_result.get("agents", [])

        if args.json_output:
            # Compact single-line JSON for LLMs: includes full file->agent->task mapping
            all_status = []
            for a in agents:
                aid = a["agent_id"]
                try:
                    s = engine.get_agent_status(aid)
                    if "error" not in s:
                        all_status.append(s)
                except Exception:
                    pass
            file_map = engine.get_file_agent_map(agent_id=args.agent_id if hasattr(args, "agent_id") else None)
            _print_json({
                "status": status,
                "agents": all_status,
                "file_map": file_map.get("files", []),
            })
            return

        graph = __import__("coordinationhub.plugins.graph", fromlist=["get_graph"]).get_graph()
        print("=" * 70)
        print("COORDINATIONHUB DASHBOARD")
        print("=" * 70)
        print(f"Graph loaded: {status.get('graph_loaded')}")
        print(f"Active agents: {status.get('active_agents')}  |  Total registered: {status.get('registered_agents')}")
        print(f"Active locks: {status.get('active_locks')}  |  Owned files: {status.get('owned_files', 0)}")
        print("-" * 70)

        if args.minimal:
            for a in agents:
                stale = " [STALE]" if a.get("stale") else ""
                print(f"  {a['agent_id']} [{a['status']}]{stale}")
            print("-" * 70)
            return

        for a in agents:
            aid = a["agent_id"]
            stale = " [STALE]" if a.get("stale") else ""
            print(f"\nAgent: {aid} [{a['status']}]{stale}")
            try:
                s = engine.get_agent_status(aid)
                if "error" in s:
                    continue
                if s.get("graph_agent_id"):
                    print(f"  Role: {s['graph_agent_id']} ({s.get('role', '')})")
                if s.get("current_task"):
                    print(f"  Task: {s['current_task']}")
                if s.get("responsibilities"):
                    print(f"  Responsibilities: {', '.join(s['responsibilities'])}")
                if s.get("owned_files"):
                    print(f"  Owns {len(s['owned_files'])} files")
                if s.get("active_locks"):
                    print(f"  Locks: {', '.join(s['active_locks'])}")
            except Exception as e:
                print(f"  (error loading status: {e})")

        file_map = engine.get_file_agent_map(agent_id=args.agent_id if hasattr(args, "agent_id") else None)
        if file_map.get("files"):
            print("\n" + "-" * 70)
            print(f"FILE OWNERSHIP ({file_map['total']} tracked files)")
            current_agent = None
            for entry in file_map["files"]:
                if entry["assigned_agent_id"] != current_agent:
                    current_agent = entry["assigned_agent_id"]
                    print(f"\n  {current_agent} ({entry['role']}):")
                print(f"    {entry['document_path']}")
                if entry.get("task_description"):
                    print(f"      → {entry['task_description']}")
        print("=" * 70)
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# agent-status
# ------------------------------------------------------------------ #

def cmd_agent_status(args):
    engine = _replica_engine_from_args(args)
    try:
        if getattr(args, "tree", False):
            result = engine.get_agent_tree(args.agent_id)
            if args.json_output:
                _print_json(result)
            else:
                if "error" in result:
                    print(f"Error: {result['error']}")
                    return
                print(result["text_tree"])
                return
        result = engine.get_agent_status(args.agent_id)
        if args.json_output:
            _print_json(result)
        else:
            if "error" in result:
                print(f"Error: {result['error']}")
                return
            print(f"Agent: {result['agent_id']}")
            print(f"  Status: {result['status']}")
            print(f"  Parent: {result.get('parent_id') or '(root)'}")
            if result.get("graph_agent_id"):
                print(f"  Graph ID: {result['graph_agent_id']}")
                print(f"  Role: {result.get('role', '')}")
                print(f"  Model: {result.get('model', '')}")
            if result.get("responsibilities"):
                print(f"  Responsibilities: {', '.join(result['responsibilities'])}")
            if result.get("current_task"):
                print(f"  Current task: {result['current_task']}")
            print(f"  Owned files: {len(result.get('owned_files', []))}")
            print(f"  Active locks: {len(result.get('active_locks', []))}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# agent-tree
# ------------------------------------------------------------------ #

def cmd_agent_tree(args):
    engine = _replica_engine_from_args(args)
    try:
        result = engine.get_agent_tree(getattr(args, "agent_id", None))
        if args.json_output:
            _print_json(result)
        else:
            if "error" in result:
                print(f"Error: {result['error']}")
                return
            print(result["text_tree"])
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# assess
# ------------------------------------------------------------------ #

def cmd_assess(args):
    engine = _engine_from_args(args)
    try:
        result = engine.run_assessment(
            suite_path=getattr(args, "suite_path", None),
            format=args.format,
            graph_agent_id=getattr(args, "graph_agent_id", None),
            scope=getattr(args, "scope", "project"),
        )
        if "error" in result:
            print(f"Error: {result['error']}", file=sys.stderr)
            return
        output_path = getattr(args, "output", None) or getattr(args, "output_path", None)
        if output_path:
            Path(output_path).write_text(
                result.get("report", json.dumps(result, indent=2)),
                encoding="utf-8",
            )
            print(f"Report written to {output_path}")
        else:
            if result.get("report"):
                print(result["report"])
            else:
                _print_json(result)
    finally:
        _close(engine)
