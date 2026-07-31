"""Letting him run on his own, and watching what he does."""

from __future__ import annotations

import json
import queue

from apiflask import abort
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
@api.doc(
    summary="Control a session's work",
    description=(
        "action = 'start' (this session keeps working) | 'stop' (it stops; all of them "
        "when no conversationId is given) | 'tick' (one step now) | 'cancel' (abandon the "
        "step in flight, leaving the session working)."
    ),
)
def autonomy_control(payload):
    action = payload.get("action")
    conversation = str(payload.get("conversationId") or "").strip()
    if action in ("start", "continue"):
        # Named per session now. Roaming was one switch over one board — on meant every open
        # task everywhere was fair game and off meant nothing happened at all, so with two
        # projects going there was no way to say "continue this one".
        #
        # It also no longer changes your permission mode behind your back. That existed
        # because roaming ran at 4am with nobody to answer a question, so ask-mode meant he
        # spent the night refusing himself. A session you started and can watch is a
        # different proposition, and silently widening what he may do is not a thing to do
        # on someone's behalf.
        if not conversation:
            # abort, not a returned dict: @api.output serialises the return value through
            # AutonomyStatusSchema, which has no `error` field — so a returned error came
            # back as {} with a 400 and nothing saying why. Fourth time that schema has
            # eaten something.
            abort(400, "conversationId required — work belongs to a session")
        return autonomy.keep_working(conversation)
    if action in ("stop", "rest"):
        # No conversation means all of them, which is what "stop" means when you are not
        # looking at a particular one.
        return autonomy.rest(conversation)
    if action == "tick":
        return autonomy.tick_now()
    # Stopping the step in flight is deliberately not the same action as stopping roaming.
    # One says "not this", the other says "not any more", and collapsing them meant you
    # could only ever say the second.
    if action == "cancel":
        return autonomy.cancel_tick()
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
