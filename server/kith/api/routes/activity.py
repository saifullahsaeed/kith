"""What he has been doing, live and on the record."""

from __future__ import annotations

from flask import jsonify, request

from kith.api.blueprint import api
from kith.infra.db import repositories as repo
from kith.kernel import events
from kith.services.activity import feed
from kith.services.turn.meter import usage_snapshot
from kith.settings import AGENT_DB_PATH


@api.get("/activity/status")
@api.doc(
    summary="What he is doing",
    description="The round feed's counters and token spend for the session.",
)
def activity_status():
    return jsonify(
        {
            # No queues. `toReview` went with the `review` column, and `toApprove` went once it
            # was looked at: it showed four plans that could not be approved — none of them had a
            # plan filed — while the actual approval happened by typing "ok approved" in chat.
            # A queue beside the conversation is a second place to find out what the conversation
            # is already telling you.
            **feed.status(),
        }
    )


@api.get("/activity/recent")
@api.doc(
    summary="The last lines of the feed",
    description="What he has been doing, most recent last — the snapshot a window opens with.",
)
def activity_recent():
    """The first half of snapshot-then-subscribe.

    `/api/activity/stream` was here, and it sent these same lines down the stream on every single
    connection — which, with no id on the wire and no dedupe on the client, is why a reconnect
    duplicated the last hundred lines into the feed. Separating them is the fix: a window fetches
    the backlog once, and `/api/events` carries only what happens after it.

    `at` is what makes the seam exact. The stream is already running when a window asks for this,
    so lines can arrive on it while this request is in flight — and without a cursor the client has
    to guess whether a streamed line is also in the snapshot. It is the log position this snapshot
    was taken at, and anything the stream delivers above it is new. Read *before* the buffer, so a
    line published between the two readings lands on the "still to come" side and is shown once,
    rather than on the "already have it" side and dropped.
    """
    at = events.log.newest
    return jsonify({"activity": feed.recent(), "at": at})


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
