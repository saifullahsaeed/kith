"""Letting him run on his own, and watching what he does."""

from __future__ import annotations

import json
import queue

from flask import Response, jsonify, request

from kith.api.blueprint import api
from kith.autonomy import runner as autonomy
from kith.config import AGENT_DB_PATH
from kith.infra.db import repositories as repo
from kith.schemas import (
    AutonomyControlSchema,
    AutonomyStatusSchema,
)
from kith.services.agent_loop import usage_snapshot


@api.get("/autonomy")
@api.output(AutonomyStatusSchema)
@api.doc(summary="Autonomy status", description="Whether Kith is running on its own, and what it's doing.")
def autonomy_status():
    return autonomy.status()


@api.post("/autonomy")
@api.input(AutonomyControlSchema, arg_name="payload")
@api.output(AutonomyStatusSchema)
@api.doc(summary="Control autonomy", description="action = 'start' | 'stop' | 'tick' (run one step now).")
def autonomy_control(payload):
    action = payload.get("action")
    if action == "start":
        return autonomy.start(payload.get("intervalSeconds"))
    if action == "stop":
        return autonomy.stop()
    if action == "tick":
        return autonomy.tick_now()
    return autonomy.status()


@api.get("/autonomy/stream")
@api.doc(summary="Autonomy activity stream", description="Server-sent events of what Kith does on its own.")
def autonomy_stream():
    def generate():
        subscription = autonomy.subscribe()
        try:
            for item in autonomy.recent():
                yield f"data: {json.dumps(item)}\n\n"
            while True:
                try:
                    item = subscription.get(timeout=15)
                except queue.Empty:
                    yield ": ping\n\n"  # keep-alive; also surfaces disconnects
                    continue
                yield f"data: {json.dumps(item)}\n\n"
        finally:
            autonomy.unsubscribe(subscription)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@api.get("/usage")
@api.doc(
    summary="Token usage",
    description="Process-wide token spend since server start — chat and autonomy alike.",
)
def usage():
    return jsonify(usage_snapshot())


@api.get("/activity")
@api.doc(
    summary="Autonomy flight recorder",
    description="Durable per-tick trail (newest first) plus an aggregate summary — how Kith has been doing over time.",
)
def activity():
    try:
        limit = min(500, max(1, int(request.args.get("limit", 100))))
    except (TypeError, ValueError):
        limit = 100
    return jsonify(
        {
            "ticks": repo.messages.list_tick_log(AGENT_DB_PATH, limit),
            "summary": repo.messages.tick_log_summary(AGENT_DB_PATH),
        }
    )
