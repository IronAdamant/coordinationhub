"""Command handlers for CoordinationHub CLI.

Each cmd_* function is self-contained — imports CoordinationEngine directly.
Shared helpers are defined at the top of this file.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .core import CoordinationEngine


# ------------------------------------------------------------------ #
# Shared helpers (used across all command handlers)
# ------------------------------------------------------------------ #

def _print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, default=str))


def _run_engine(args: argparse.Namespace) -> CoordinationEngine:
    storage_dir = Path(args.storage_dir) if args.storage_dir else None
    project_root = Path(args.project_root) if args.project_root else None
    namespace = getattr(args, "namespace", "hub")
    engine = CoordinationEngine(storage_dir=storage_dir, project_root=project_root, namespace=namespace)
    engine.start()
    return engine


def _close_engine(engine: CoordinationEngine) -> None:
    try:
        engine.close()
    except Exception:
        pass


def _fmt_lock_result(result: dict[str, Any], document_path: str) -> None:
    if result.get("acquired"):
        print(f"LOCKED: {document_path}")
        print(f"  Agent: {result.get('locked_by')}")
        print(f"  Expires: {result.get('expires_at')}")
    elif result.get("released"):
        print(f"RELEASED: {document_path}")
    elif result.get("refreshed"):
        print(f"REFRESHED: {document_path}")
        print(f"  Expires: {result.get('expires_at')}")
    else:
        locked_by = result.get("locked_by", "unknown")
        expires = result.get("expires_at", "unknown")
        print(f"FAILED: {document_path} is locked by {locked_by}")
        print(f"  Expires: {expires}")


# ------------------------------------------------------------------ #
# serve
# ------------------------------------------------------------------ #

def cmd_serve(args):
    from .mcp_server import CoordinationHubMCPServer
    server = CoordinationHubMCPServer(
        storage_dir=args.storage_dir, project_root=args.project_root,
        namespace=getattr(args, "namespace", "hub"), host=args.host, port=args.port,
    )
    print(f"Starting CoordinationHub HTTP server on {server.get_url()}")
    try:
        server.start(blocking=True)
    finally:
        server.stop()


# ------------------------------------------------------------------ #
# serve-mcp
# ------------------------------------------------------------------ #

def cmd_serve_mcp(args):
    import os
    if args.storage_dir:
        os.environ["COORDINATIONHUB_STORAGE_DIR"] = args.storage_dir
    if args.project_root:
        os.environ["COORDINATIONHUB_PROJECT_ROOT"] = args.project_root
    if getattr(args, "namespace", None):
        os.environ["COORDINATIONHUB_NAMESPACE"] = args.namespace
    from .mcp_stdio import main as mcp_main
    mcp_main()


# ------------------------------------------------------------------ #
# status
# ------------------------------------------------------------------ #

def cmd_status(args):
    engine = _run_engine(args)
    try:
        result = engine.status()
        if args.json_output:
            _print_json(result)
        else:
            print("CoordinationHub Status")
            for key, value in result.items():
                print(f"  {key.replace('_', ' ').title()}: {value}")
    finally:
        _close_engine(engine)


# ------------------------------------------------------------------ #
# register
# ------------------------------------------------------------------ #

def cmd_register(args):
    engine = _run_engine(args)
    try:
        result = engine.register_agent(
            agent_id=args.agent_id, parent_id=args.parent_id,
            graph_agent_id=getattr(args, "graph_agent_id", None),
            worktree_root=args.worktree_root,
        )
        if args.json_output:
            _print_json(result)
        else:
            print(f"Registered: {args.agent_id}")
            if result.get("parent_id"):
                print(f"  Parent: {result['parent_id']}")
            print(f"  Worktree: {result.get('worktree_root')}")
            if result.get("graph_agent_id"):
                print(f"  Graph role: {result.get('graph_agent_id')} ({result.get('role', '')})")
            if result.get("owned_files"):
                print(f"  Owned files: {len(result['owned_files'])}")
    finally:
        _close_engine(engine)


# ------------------------------------------------------------------ #
# heartbeat
# ------------------------------------------------------------------ #

def cmd_heartbeat(args):
    engine = _run_engine(args)
    try:
        result = engine.heartbeat(args.agent_id)
        if args.json_output:
            _print_json(result)
        else:
            print(f"Heartbeat: {args.agent_id} — updated: {result.get('updated')}")
    finally:
        _close_engine(engine)


# ------------------------------------------------------------------ #
# deregister
# ------------------------------------------------------------------ #

def cmd_deregister(args):
    engine = _run_engine(args)
    try:
        result = engine.deregister_agent(args.agent_id)
        if args.json_output:
            _print_json(result)
        else:
            print(f"Deregistered: {args.agent_id}")
            print(f"  Children orphaned: {result.get('children_orphaned')}")
            print(f"  Locks released: {result.get('locks_released')}")
    finally:
        _close_engine(engine)


# ------------------------------------------------------------------ #
# list-agents
# ------------------------------------------------------------------ #

def cmd_list_agents(args):
    engine = _run_engine(args)
    try:
        result = engine.list_agents(active_only=not args.include_stale, stale_timeout=args.stale_timeout)
        agents = result.get("agents", [])
        if args.json_output:
            _print_json(result)
        elif not agents:
            print("No agents registered")
        else:
            print(f"{len(agents)} agent(s):")
            for a in agents:
                stale = " (STALE)" if a.get("stale") else ""
                print(f"  {a['agent_id']}: {a['status']}{stale}")
    finally:
        _close_engine(engine)


# ------------------------------------------------------------------ #
# lineage
# ------------------------------------------------------------------ #

def cmd_lineage(args):
    engine = _run_engine(args)
    try:
        result = engine.get_lineage(args.agent_id)
        if args.json_output:
            _print_json(result)
        else:
            ancestors = result.get("ancestors", [])
            descendants = result.get("descendants", [])
            print(f"Lineage for {args.agent_id}:")
            print(f"  Ancestors: {', '.join(a['agent_id'] for a in ancestors) or '(none)'}")
            print(f"  Descendants: {', '.join(d['agent_id'] for d in descendants) or '(none)'}")
    finally:
        _close_engine(engine)


# ------------------------------------------------------------------ #
# siblings
# ------------------------------------------------------------------ #

def cmd_siblings(args):
    engine = _run_engine(args)
    try:
        result = engine.get_siblings(args.agent_id)
        siblings = result.get("siblings", [])
        if args.json_output:
            _print_json(result)
        elif not siblings:
            print(f"No siblings for {args.agent_id}")
        else:
            print(f"{len(siblings)} sibling(s):")
            for s in siblings:
                print(f"  {s['agent_id']}: {s['status']}")
    finally:
        _close_engine(engine)


# ------------------------------------------------------------------ #
# acquire-lock
# ------------------------------------------------------------------ #

def cmd_acquire_lock(args):
    engine = _run_engine(args)
    try:
        result = engine.acquire_lock(args.document_path, args.agent_id, args.lock_type, args.ttl, args.force)
        if args.json_output:
            _print_json(result)
        else:
            _fmt_lock_result(result, args.document_path)
    finally:
        _close_engine(engine)


# ------------------------------------------------------------------ #
# release-lock
# ------------------------------------------------------------------ #

def cmd_release_lock(args):
    engine = _run_engine(args)
    try:
        result = engine.release_lock(args.document_path, args.agent_id)
        if args.json_output:
            _print_json(result)
        else:
            if result.get("released"):
                print(f"RELEASED: {args.document_path}")
            else:
                print(f"FAILED: {result.get('reason')}")
    finally:
        _close_engine(engine)


# ------------------------------------------------------------------ #
# refresh-lock
# ------------------------------------------------------------------ #

def cmd_refresh_lock(args):
    engine = _run_engine(args)
    try:
        result = engine.refresh_lock(args.document_path, args.agent_id, ttl=args.ttl)
        if args.json_output:
            _print_json(result)
        else:
            if result.get("refreshed"):
                print(f"REFRESHED: {args.document_path}")
                print(f"  Expires: {result.get('expires_at')}")
            else:
                print(f"FAILED: {result.get('reason')}")
    finally:
        _close_engine(engine)


# ------------------------------------------------------------------ #
# lock-status
# ------------------------------------------------------------------ #

def cmd_lock_status(args):
    engine = _run_engine(args)
    try:
        result = engine.get_lock_status(args.document_path)
        if args.json_output:
            _print_json(result)
        else:
            if result.get("locked"):
                print(f"LOCKED: {args.document_path}")
                print(f"  By: {result.get('locked_by')}")
                print(f"  Expires: {result.get('expires_at')}")
            else:
                print(f"UNLOCKED: {args.document_path}")
    finally:
        _close_engine(engine)


# ------------------------------------------------------------------ #
# release-agent-locks
# ------------------------------------------------------------------ #

def cmd_release_agent_locks(args):
    engine = _run_engine(args)
    try:
        result = engine.release_agent_locks(args.agent_id)
        if args.json_output:
            _print_json(result)
        else:
            print(f"Released {result.get('released', 0)} lock(s) for {args.agent_id}")
    finally:
        _close_engine(engine)


# ------------------------------------------------------------------ #
# reap-expired-locks
# ------------------------------------------------------------------ #

def cmd_reap_expired_locks(args):
    engine = _run_engine(args)
    try:
        result = engine.reap_expired_locks()
        if args.json_output:
            _print_json(result)
        else:
            print(f"Reaped {result.get('reaped', 0)} expired lock(s)")
    finally:
        _close_engine(engine)


# ------------------------------------------------------------------ #
# reap-stale-agents
# ------------------------------------------------------------------ #

def cmd_reap_stale_agents(args):
    engine = _run_engine(args)
    try:
        result = engine.reap_stale_agents(timeout=args.timeout)
        if args.json_output:
            _print_json(result)
        else:
            print(f"Reaped {result.get('reaped', 0)} stale agent(s)")
            print(f"  Orphaned children: {result.get('orphaned_children', 0)}")
    finally:
        _close_engine(engine)


# ------------------------------------------------------------------ #
# broadcast
# ------------------------------------------------------------------ #

def cmd_broadcast(args):
    engine = _run_engine(args)
    try:
        result = engine.broadcast(args.agent_id, document_path=getattr(args, "document_path", None))
        if args.json_output:
            _print_json(result)
        else:
            ack = result.get("acknowledged_by", [])
            conflicts = result.get("conflicts", [])
            print(f"Broadcast from {args.agent_id}")
            print(f"  Acknowledged by: {ack or '(none)'}")
            if conflicts:
                print(f"  Conflicts:")
                for c in conflicts:
                    print(f"    {c['document_path']} locked by {c['locked_by']}")
    finally:
        _close_engine(engine)


# ------------------------------------------------------------------ #
# wait-for-locks
# ------------------------------------------------------------------ #

def cmd_wait_for_locks(args):
    engine = _run_engine(args)
    try:
        result = engine.wait_for_locks(args.document_paths, args.agent_id, timeout_s=args.timeout)
        if args.json_output:
            _print_json(result)
        else:
            print(f"Released: {result.get('released') or '(none)'}")
            print(f"Timed out: {result.get('timed_out') or '(none)'}")
    finally:
        _close_engine(engine)


# ------------------------------------------------------------------ #
# notify-change
# ------------------------------------------------------------------ #

def cmd_notify_change(args):
    engine = _run_engine(args)
    try:
        result = engine.notify_change(args.document_path, args.change_type, args.agent_id)
        if args.json_output:
            _print_json(result)
        else:
            print(f"Recorded: {args.change_type} on {args.document_path} by {args.agent_id}")
    finally:
        _close_engine(engine)


# ------------------------------------------------------------------ #
# get-notifications
# ------------------------------------------------------------------ #

def cmd_get_notifications(args):
    engine = _run_engine(args)
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
        _close_engine(engine)


# ------------------------------------------------------------------ #
# prune-notifications
# ------------------------------------------------------------------ #

def cmd_prune_notifications(args):
    engine = _run_engine(args)
    try:
        result = engine.prune_notifications(max_age_seconds=args.max_age_seconds, max_entries=args.max_entries)
        if args.json_output:
            _print_json(result)
        else:
            print(f"Pruned {result.get('pruned', 0)} notification(s)")
    finally:
        _close_engine(engine)


# ------------------------------------------------------------------ #
# get-conflicts
# ------------------------------------------------------------------ #

def cmd_get_conflicts(args):
    engine = _run_engine(args)
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
        _close_engine(engine)


# ------------------------------------------------------------------ #
# load-spec
# ------------------------------------------------------------------ #

def cmd_load_spec(args):
    engine = _run_engine(args)
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
        _close_engine(engine)


# ------------------------------------------------------------------ #
# validate-spec
# ------------------------------------------------------------------ #

def cmd_validate_spec(args):
    engine = _run_engine(args)
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
        _close_engine(engine)


# ------------------------------------------------------------------ #
# scan-project
# ------------------------------------------------------------------ #

def cmd_scan_project(args):
    engine = _run_engine(args)
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
        _close_engine(engine)


# ------------------------------------------------------------------ #
# dashboard
# ------------------------------------------------------------------ #

def cmd_dashboard(args):
    engine = _run_engine(args)
    try:
        status = engine.status()
        agents_result = engine.list_agents(active_only=False)
        agents = agents_result.get("agents", [])

        if args.json_output:
            all_status = []
            for a in agents:
                aid = a["agent_id"]
                try:
                    s = engine.get_agent_status(aid)
                    if "error" not in s:
                        all_status.append(s)
                except Exception:
                    pass
            _print_json({"status": status, "agents": all_status})
            return

        graph = __import__("coordinationhub.graphs", fromlist=["get_graph"]).get_graph()
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

        file_map = engine.get_file_agent_map(agent_id=args.agent_id)
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
        _close_engine(engine)


# ------------------------------------------------------------------ #
# agent-status
# ------------------------------------------------------------------ #

def cmd_agent_status(args):
    engine = _run_engine(args)
    try:
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
        _close_engine(engine)


# ------------------------------------------------------------------ #
# assess
# ------------------------------------------------------------------ #

def cmd_assess(args):
    engine = _run_engine(args)
    try:
        result = engine.run_assessment(args.suite_path, format=args.format)
        if "error" in result:
            print(f"Error: {result['error']}", file=sys.stderr)
            return
        if args.output:
            Path(args.output).write_text(result.get("report", json.dumps(result, indent=2)), encoding="utf-8")
            print(f"Report written to {args.output}")
        else:
            if result.get("report"):
                print(result["report"])
            else:
                _print_json(result)
    finally:
        _close_engine(engine)
