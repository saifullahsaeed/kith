"""What he has been doing, live and on the record."""

from __future__ import annotations

import json
import queue

from flask import Response, jsonify, request

from kith.api.blueprint import api
from kith.config import AGENT_DB_PATH
from kith.infra.db import repositories as repo
from kith.services.activity import feed
from kith.services.agent_loop import usage_snapshot


def _waiting_on_you(status: str) -> list[dict]:
    """Tasks in one status, capped at a handful, newest first.

    A nudge with a button on it, not the board. Silent on failure for the same reason every
    other bit of accounting here is: a count that cannot be read is not a reason to make the
    status endpoint fail.

    These were computed by the loop that used to run unasked, which is why they read as its concern. They
    are not: `review` is work he believes is finished and nobody has checked, `planning` is a
    plan waiting for a look. Both are still true of work done in a conversation, and a queue
    nobody is shown is a queue nobody works.
    """
    try:
        waiting = [t for t in repo.tasks.list_tasks(AGENT_DB_PATH) if t.get("status") == status]
        return [{"id": t["id"], "goal": t.get("goal") or ""} for t in waiting[:6]]
    except Exception:
        return []


@api.get("/activity/status")
@api.doc(
    summary="What is waiting on you",
    description="Work he has finished and plans he has drafted, both awaiting your look.",
)
def activity_status():
    return jsonify(
        {
            **feed.status(),
            # `toReview` is gone with the `review` column. Work he thinks is finished no longer
            # waits in a tray to be noticed — `_verify_done` refuses the close and he raises it
            # in chat with `ask`, which holds the turn until it is answered. The one queue left
            # is the one decision that is genuinely a person's: approving a plan.
            "toApprove": _waiting_on_you("planning"),
        }
    )


@api.get("/activity/stream")
@api.doc(summary="Activity stream", description="Server-sent events of what Kith is doing.")
def activity_stream():
    def generate():
        subscription = feed.subscribe()
        try:
            for item in feed.recent():
                yield f"data: {json.dumps(item)}\n\n"
            while True:
                try:
                    item = subscription.get(timeout=15)
                except queue.Empty:
                    yield ": ping\n\n"  # keep-alive; also surfaces disconnects
                    continue
                yield f"data: {json.dumps(item)}\n\n"
        finally:
            feed.unsubscribe(subscription)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@api.get("/usage")
@api.doc(
    summary="Token usage",
    description="Process-wide token spend since server start.",
)
def usage():
    return jsonify(usage_snapshot())


@api.get("/activity")
@api.doc(
    summary="The flight recorder",
    description=(
        "Durable per-turn trail (newest first) plus an aggregate summary — what he has cost over time."
    ),
)
def activity():
    try:
        limit = min(500, max(1, int(request.args.get("limit", 100))))
    except (TypeError, ValueError):
        limit = 100
    return jsonify(
        {
            # `ticks` is the wire name the interface already reads, kept so this change is
            # server-side only. What it carries is turns, and always was.
            "ticks": repo.messages.list_turn_log(AGENT_DB_PATH, limit),
            "summary": repo.messages.turn_log_summary(AGENT_DB_PATH),
        }
    )
