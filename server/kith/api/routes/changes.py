"""One stream telling the interface what changed.

The counterpart to `services/changes`. Before this the app had a single push channel — the activity
feed — and eleven `setInterval`s asking for everything else on their own clocks, which is why one
reload showed you a new task and a second showed you the turn that made it.

Deliberately not a data channel. An event says "tasks changed"; the widget that cares refetches
through the endpoint it already uses. That keeps one shape here however many consumers appear, and
means a missed event costs a stale second rather than a wrong screen.
"""

from __future__ import annotations

import json
import queue

from flask import Response

from kith.api.blueprint import api
from kith.kernel import changes


@api.get("/changes")
@api.doc(summary="What changed", description="Server-sent events naming what just changed.")
def changes_stream():
    def generate():
        subscription = changes.subscribe()
        try:
            # An immediate line so the client knows the stream is open rather than merely connecting,
            # and so a proxy that buffers until first byte lets go of it.
            yield ": open\n\n"
            while True:
                try:
                    event = subscription.get(timeout=15)
                except queue.Empty:
                    yield ": ping\n\n"  # keep-alive, and how a dropped connection surfaces
                    continue
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            changes.unsubscribe(subscription)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
