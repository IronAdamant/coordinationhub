"""Document locking and coordination CLI commands."""

from __future__ import annotations

from typing import Any

from .cli_utils import print_json as _print_json, engine_from_args as _engine_from_args
from .cli_utils import replica_engine_from_args as _replica_engine_from_args, close as _close


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
# acquire-lock
# ------------------------------------------------------------------ #

def cmd_acquire_lock(args):
    engine = _engine_from_args(args)
    try:
        result = engine.acquire_lock(
            args.document_path, args.agent_id, args.lock_type, args.ttl, args.force,
            region_start=args.region_start, region_end=args.region_end,
            retry=args.retry, max_retries=args.max_retries,
            backoff_ms=args.backoff_ms, timeout_ms=args.timeout_ms,
        )
        if args.json_output:
            _print_json(result)
        else:
            _fmt_lock_result(result, args.document_path)
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# release-lock
# ------------------------------------------------------------------ #

def cmd_release_lock(args):
    engine = _engine_from_args(args)
    try:
        result = engine.release_lock(
            args.document_path, args.agent_id,
            region_start=args.region_start, region_end=args.region_end,
        )
        if args.json_output:
            _print_json(result)
        else:
            if result.get("released"):
                print(f"RELEASED: {args.document_path}")
            else:
                print(f"FAILED: {result.get('reason')}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# refresh-lock
# ------------------------------------------------------------------ #

def cmd_refresh_lock(args):
    engine = _engine_from_args(args)
    try:
        result = engine.refresh_lock(
            args.document_path, args.agent_id, ttl=args.ttl,
            region_start=args.region_start, region_end=args.region_end,
        )
        if args.json_output:
            _print_json(result)
        else:
            if result.get("refreshed"):
                print(f"REFRESHED: {args.document_path}")
                print(f"  Expires: {result.get('expires_at')}")
            else:
                print(f"FAILED: {result.get('reason')}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# lock-status
# ------------------------------------------------------------------ #

def cmd_lock_status(args):
    engine = _replica_engine_from_args(args)
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
        _close(engine)


# ------------------------------------------------------------------ #
# list-locks
# ------------------------------------------------------------------ #

def cmd_list_locks(args):
    engine = _replica_engine_from_args(args)
    try:
        result = engine.list_locks(agent_id=getattr(args, "agent_id", None))
        if args.json_output:
            _print_json(result)
        else:
            locks = result.get("locks", [])
            if not locks:
                print("No active locks")
                return
            print(f"Active locks ({len(locks)}):")
            for lock in locks:
                print(f"  {lock['document_path']}")
                print(f"    Held by: {lock['locked_by']}")
                print(f"    Type: {lock['lock_type']}")
                print(f"    Expires: {lock['expires_at']:.0f}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# admin-locks
# ------------------------------------------------------------------ #

def cmd_admin_locks(args):
    engine = _engine_from_args(args)
    try:
        result = engine.admin_locks(
            action=args.action,
            agent_id=getattr(args, "agent_id", None),
            grace_seconds=getattr(args, "grace_seconds", 0.0),
            timeout=getattr(args, "timeout", 600.0),
        )
        if args.json_output:
            _print_json(result)
        else:
            if args.action == "release_by_agent":
                print(f"Released {result.get('released', 0)} lock(s) for {getattr(args, 'agent_id', 'unknown')}")
            elif args.action == "reap_expired":
                print(f"Reaped {result.get('reaped', 0)} expired lock(s)")
            elif args.action == "reap_stale":
                print(f"Reaped {result.get('reaped', 0)} stale agent(s)")
                print(f"  Orphaned children: {result.get('orphaned_children', 0)}")
            else:
                _print_json(result)
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# broadcast
# ------------------------------------------------------------------ #

def cmd_broadcast(args):
    engine = _engine_from_args(args)
    try:
        result = engine.broadcast(
            args.agent_id,
            document_path=getattr(args, "document_path", None),
            handoff_targets=getattr(args, "handoff_targets", None),
            require_ack=getattr(args, "require_ack", False),
            message=getattr(args, "message", None),
        )
        if args.json_output:
            _print_json(result)
        else:
            # Check if this was a handoff
            if result.get("handoff_id") is not None:
                print(f"HOFF from {args.agent_id}: {result.get('handoff_id')}")
                print(f"  To: {', '.join(result.get('to_agents', []))}")
                print(f"  Document: {result.get('document_path') or '(none)'}")
                print(f"  Type: {result.get('handoff_type')}")
            elif result.get("broadcast_id") is not None:
                print(f"Broadcast from {args.agent_id} (ack required)")
                print(f"  Broadcast ID: {result['broadcast_id']}")
                print(f"  Pending acks: {', '.join(result.get('pending_acks', [])) or '(none)'}")
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
        _close(engine)


# ------------------------------------------------------------------ #
# acknowledge-broadcast
# ------------------------------------------------------------------ #

def cmd_acknowledge_broadcast(args):
    engine = _engine_from_args(args)
    try:
        result = engine.acknowledge_broadcast(args.broadcast_id, args.agent_id)
        if args.json_output:
            _print_json(result)
        elif result.get("acknowledged"):
            print(f"Acknowledged broadcast {args.broadcast_id} by {args.agent_id}")
        else:
            print(f"Failed: {result.get('reason', 'unknown')}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# wait-for-broadcast-acks
# ------------------------------------------------------------------ #

def cmd_wait_for_broadcast_acks(args):
    engine = _engine_from_args(args)
    try:
        result = engine.wait_for_broadcast_acks(
            broadcast_id=args.broadcast_id,
            timeout_s=getattr(args, "timeout", 30.0),
        )
        if args.json_output:
            _print_json(result)
        elif result.get("timed_out"):
            reason = result.get("reason", "timeout")
            print(f"Timed out waiting for broadcast acks: {reason}")
        else:
            acks = result.get("acknowledged_by", [])
            print(f"Broadcast {args.broadcast_id} acknowledged by: {', '.join(acks) or '(none)'}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# acknowledge-handoff
# ------------------------------------------------------------------ #

def cmd_acknowledge_handoff(args):
    engine = _engine_from_args(args)
    try:
        result = engine.acknowledge_handoff(args.handoff_id, args.agent_id)
        if args.json_output:
            _print_json(result)
        else:
            print(f"Acknowledged handoff {args.handoff_id} by {args.agent_id}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# complete-handoff
# ------------------------------------------------------------------ #

def cmd_complete_handoff(args):
    engine = _engine_from_args(args)
    try:
        result = engine.complete_handoff(args.handoff_id)
        if args.json_output:
            _print_json(result)
        else:
            print(f"Completed handoff {args.handoff_id}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# cancel-handoff
# ------------------------------------------------------------------ #

def cmd_cancel_handoff(args):
    engine = _engine_from_args(args)
    try:
        result = engine.cancel_handoff(args.handoff_id)
        if args.json_output:
            _print_json(result)
        else:
            print(f"Cancelled handoff {args.handoff_id}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# get-handoffs
# ------------------------------------------------------------------ #

def cmd_get_handoffs(args):
    engine = _replica_engine_from_args(args)
    try:
        result = engine.get_handoffs(
            status=getattr(args, "status", None),
            from_agent_id=getattr(args, "from_agent_id", None),
            limit=getattr(args, "limit", 50),
        )
        handoffs = result.get("handoffs", [])
        if args.json_output:
            _print_json(result)
        elif not handoffs:
            print("No handoffs found")
        else:
            print(f"{len(handoffs)} handoff(s):")
            for h in handoffs:
                to = ", ".join(h.get("to_agents", []))
                print(f"  [{h['status']}] {h['id']} {h['from_agent_id']} → {to}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# wait-for-handoff
# ------------------------------------------------------------------ #

def cmd_wait_for_handoff(args):
    engine = _engine_from_args(args)
    try:
        result = engine.wait_for_handoff(
            handoff_id=args.handoff_id,
            timeout_s=getattr(args, "timeout", 30.0),
        )
        if args.json_output:
            _print_json(result)
        elif result.get("timed_out"):
            print(f"Timed out waiting for handoff {args.handoff_id}")
        else:
            print(f"Handoff {args.handoff_id} completed")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# wait-for-locks
# ------------------------------------------------------------------ #

def cmd_wait_for_locks(args):
    engine = _engine_from_args(args)
    try:
        result = engine.wait_for_locks(args.document_paths, args.agent_id, timeout_s=args.timeout)
        if args.json_output:
            _print_json(result)
        else:
            print(f"Released: {result.get('released') or '(none)'}")
            print(f"Timed out: {result.get('timed_out') or '(none)'}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# await-agent
# ------------------------------------------------------------------ #

def cmd_await_agent(args):
    engine = _engine_from_args(args)
    try:
        result = engine.await_agent(args.agent_id, timeout_s=args.timeout)
        if args.json_output:
            _print_json(result)
        else:
            print(f"Agent: {args.agent_id}")
            print(f"  Status: {result.get('status')}")
            if result.get('awaited'):
                print(f"  Waited: {result.get('waited_s', 0):.1f}s")
            else:
                print(f"  Timeout: {result.get('timeout_s')}s")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# send-message
# ------------------------------------------------------------------ #

def cmd_send_message(args):
    engine = _engine_from_args(args)
    try:
        result = engine.send_message(
            args.from_agent_id, args.to_agent_id, args.message_type,
            payload=getattr(args, 'payload', None),
        )
        if args.json_output:
            _print_json(result)
        else:
            print(f"MESSAGE SENT to {args.to_agent_id}")
            print(f"  From: {args.from_agent_id}")
            print(f"  Type: {args.message_type}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# get-messages
# ------------------------------------------------------------------ #

def cmd_get_messages(args):
    engine = _replica_engine_from_args(args)
    try:
        result = engine.get_messages(
            args.agent_id,
            unread_only=getattr(args, 'unread_only', False),
            limit=getattr(args, 'limit', 50),
        )
        if args.json_output:
            _print_json(result)
        else:
            messages = result.get('messages', [])
            print(f"Messages for {args.agent_id}: {len(messages)}")
            for msg in messages:
                print(f"  From: {msg['from_agent_id']} | Type: {msg['message_type']} | Read: {msg.get('read_at') is not None}")
    finally:
        _close(engine)


# ------------------------------------------------------------------ #
# mark-messages-read
# ------------------------------------------------------------------ #

def cmd_mark_messages_read(args):
    engine = _engine_from_args(args)
    try:
        result = engine.mark_messages_read(
            args.agent_id,
            message_ids=getattr(args, 'message_ids', None),
        )
        if args.json_output:
            _print_json(result)
        else:
            print(f"Marked {result.get('marked_read', 0)} message(s) as read for {args.agent_id}")
    finally:
        _close(engine)
