"""CLI commands for the work intent board."""

from __future__ import annotations

from .cli_utils import print_json as _print_json, command as _command


# ------------------------------------------------------------------ #
# declare-work-intent
# ------------------------------------------------------------------ #

@_command()
def cmd_declare_work_intent(engine, args):
    result = engine.declare_work_intent(
        agent_id=args.agent_id,
        document_path=args.document_path,
        intent=args.intent,
        ttl=getattr(args, "ttl", 60.0),
    )
    if args.json_output:
        _print_json(result)
    else:
        print(f"Intent declared: {args.agent_id} → {args.document_path}")
        print(f"  Intent: {args.intent}")


# ------------------------------------------------------------------ #
# get-work-intents
# ------------------------------------------------------------------ #

@_command(replica=True)
def cmd_get_work_intents(engine, args):
    result = engine.get_work_intents(getattr(args, "agent_id", None))
    intents = result.get("intents", [])
    if args.json_output:
        _print_json(result)
    elif not intents:
        print("No active work intents")
    else:
        print(f"{len(intents)} active intent(s):")
        for i in intents:
            print(f"  {i['agent_id']}: {i['document_path']} — {i['intent']}")


# ------------------------------------------------------------------ #
# clear-work-intent
# ------------------------------------------------------------------ #

@_command()
def cmd_clear_work_intent(engine, args):
    result = engine.clear_work_intent(args.agent_id)
    if args.json_output:
        _print_json(result)
    else:
        print(f"Intent cleared: {args.agent_id}")
