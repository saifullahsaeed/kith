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
