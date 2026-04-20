"""Agent identity and lifecycle CLI commands."""

from __future__ import annotations

from .cli_utils import print_json as _print_json, command as _command


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

@_command(replica=True)
def cmd_status(engine, args):
    result = engine.status()
    if args.json_output:
        _print_json(result)
    else:
        print("CoordinationHub Status")
        for key, value in result.items():
            print(f"  {key.replace('_', ' ').title()}: {value}")


# ------------------------------------------------------------------ #
# register
# ------------------------------------------------------------------ #

@_command()
def cmd_register(engine, args):
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


# ------------------------------------------------------------------ #
# heartbeat
# ------------------------------------------------------------------ #

@_command()
def cmd_heartbeat(engine, args):
    result = engine.heartbeat(args.agent_id)
    if args.json_output:
        _print_json(result)
    else:
        print(f"Heartbeat: {args.agent_id} — updated: {result.get('updated')}")


# ------------------------------------------------------------------ #
# deregister
# ------------------------------------------------------------------ #

@_command()
def cmd_deregister(engine, args):
    result = engine.deregister_agent(args.agent_id)
    if args.json_output:
        _print_json(result)
    else:
        print(f"Deregistered: {args.agent_id}")
        print(f"  Children orphaned: {result.get('children_orphaned')}")
        print(f"  Locks released: {result.get('locks_released')}")


# ------------------------------------------------------------------ #
# list-agents
# ------------------------------------------------------------------ #

@_command()
def cmd_list_agents(engine, args):
    # Auto-reap stale agents so displayed status matches DB state.  This
    # eliminates the "active (STALE)" vs "[stopped]" inconsistency
    # between list-agents and dashboard reported in Review Fourteen:
    # once a stale agent is reaped, both commands render it as stopped.
    engine.reap_stale_agents(timeout=args.stale_timeout)
    result = engine.list_agents(
        active_only=not args.include_stale, stale_timeout=args.stale_timeout,
    )
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


# ------------------------------------------------------------------ #
# agent-relations
# ------------------------------------------------------------------ #

@_command(replica=True)
def cmd_agent_relations(engine, args):
    result = engine.get_agent_relations(args.agent_id, mode=args.mode)
    if args.json_output:
        _print_json(result)
    elif args.mode == "siblings":
        siblings = result.get("siblings", [])
        if not siblings:
            print(f"No siblings for {args.agent_id}")
        else:
            print(f"{len(siblings)} sibling(s):")
            for s in siblings:
                print(f"  {s['agent_id']}: {s['status']}")
    else:
        ancestors = result.get("ancestors", [])
        descendants = result.get("descendants", [])
        print(f"Lineage for {args.agent_id}:")
        print(f"  Ancestors: {', '.join(a['agent_id'] for a in ancestors) or '(none)'}")
        print(f"  Descendants: {', '.join(d['agent_id'] for d in descendants) or '(none)'}")
