"""Agent identity and lifecycle CLI commands."""

from __future__ import annotations

from .cli_utils import print_json as _print_json, engine_from_args as _engine_from_args, close as _close


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
    engine = _engine_from_args(args)
    try:
        result = engine.status()
        if args.json_output:
            _print_json(result)
        else:
            print("CoordinationHub Status")
            for key, value in result.items():
                print(f"  {key.replace('_', ' ').title()}: {value}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# register
# ------------------------------------------------------------------ #

def cmd_register(args):
    engine = _engine_from_args(args)
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
        _close(engine)


# ------------------------------------------------------------------ #
# heartbeat
# ------------------------------------------------------------------ #

def cmd_heartbeat(args):
    engine = _engine_from_args(args)
    try:
        result = engine.heartbeat(args.agent_id)
        if args.json_output:
            _print_json(result)
        else:
            print(f"Heartbeat: {args.agent_id} — updated: {result.get('updated')}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# deregister
# ------------------------------------------------------------------ #

def cmd_deregister(args):
    engine = _engine_from_args(args)
    try:
        result = engine.deregister_agent(args.agent_id)
        if args.json_output:
            _print_json(result)
        else:
            print(f"Deregistered: {args.agent_id}")
            print(f"  Children orphaned: {result.get('children_orphaned')}")
            print(f"  Locks released: {result.get('locks_released')}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# list-agents
# ------------------------------------------------------------------ #

def cmd_list_agents(args):
    engine = _engine_from_args(args)
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
        _close(engine)


# ------------------------------------------------------------------ #
# lineage
# ------------------------------------------------------------------ #

def cmd_lineage(args):
    engine = _engine_from_args(args)
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
        _close(engine)


# ------------------------------------------------------------------ #
# siblings
# ------------------------------------------------------------------ #

def cmd_siblings(args):
    engine = _engine_from_args(args)
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
        _close(engine)
