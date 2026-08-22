"""The one stream the interface listens to.

This replaces two endpoints. `/api/changes` said what changed and lost anything queued when a
connection dropped; `/api/activity/stream` carried the feed and re-sent its whole backlog on every
connect, duplicating lines. Two channels, two failure modes, and — since the renderer loads the
backend over plain HTTP/1.1 — two of Chromium's six connections per origin held permanently.

One endpoint, two event types, and the difference between them is not cosmetic:

* ``changed`` — a kind of thing moved. No values, ever. The client invalidates and refetches
  through the API it already uses. See `kernel/changes`.
* ``activity`` — a line of what he is doing. The value *is* the event: there is no endpoint that
  will tell you again what a tool call said thirty seconds ago, so this one carries its payload.

SSE rather than a WebSocket, deliberately. Nothing here flows the other way — the client has the
whole REST API for that — and `id:`/`Last-Event-ID` is a resumption protocol the browser and every
proxy already implement. A socket would mean writing one.

`Last-Event-ID` is the whole reason this exists in this shape. See `kernel/events` for the three
ways a cursor can be wrong and the honest answer to each; this module is that contract on the wire.
"""

from __future__ import annotations

import json
import queue

from flask import Response, request

from kith.api.blueprint import api
from kith.kernel import events

#: Seconds without an event before a keep-alive comment goes out. Short enough that a dead
#: connection surfaces as a write failure here rather than as a window that has quietly stopped
#: hearing anything, and well inside the idle timeout of anything that might sit in between.
_PING = 15


def _frame(event: events.Event) -> str:
    """One event as SSE wire text.

    The `id:` line is the contract: the client sends the last one it saw back as `Last-Event-ID`
    on reconnect, and notices a gap if two arrive out of step. `event:` is what lets one
    connection carry both kinds — `EventSource.addEventListener("activity", …)` is how SSE was
    designed to multiplex, and using it is what collapsed two sockets into one.

    The id is `<epoch>-<n>`, not a bare number, and the epoch half is what makes a restart
    detectable however late the client reconnects. SSE treats the id as opaque text and echoes it
    back verbatim, so carrying two facts in it costs nothing. See `kernel/events`.
    """
    return (
        f"id: {events.EPOCH}-{event.id}\n"
        f"event: {event.type}\n"
        f"data: {json.dumps(event.data)}\n\n"
    )


def _cursor() -> tuple[int | None, str | None]:
    """Where this client got to, and which run of the server it got there in.

    The header is the browser's own, set automatically by `EventSource` on a reconnect; the desktop
    shell sends the same header from the main process, where it holds the cursor across renderer
    reloads. A `?since=` alternative was left out on purpose: two ways to say one thing means two
    code paths to keep honest, and the header is the one the standard specifies.

    `<epoch>-<n>`. A bare number is still accepted and read as "no epoch given", which is what a
    client that predates this sends — it then gets the numeric checks alone, which is what it had.
    """
    raw = request.headers.get("Last-Event-ID", "").strip()
    if not raw:
        return None, None
    epoch, _, number = raw.rpartition("-")
    try:
        return int(number), (epoch or None)
    except ValueError:
        # A cursor we cannot read is not a cursor. Treated as a fresh connection rather than as an
        # error: the client's next fetch is what it actually needs, and a 400 on a reconnect loop
        # would be a window that never recovers.
        return None, None


@api.get("/events")
@api.doc(
    summary="What is happening",
    description="One server-sent stream: `changed` events naming what moved, `activity` lines of "
    "what he is doing. Resumable with `Last-Event-ID`.",
)
def events_stream():
    since, epoch = _cursor()
    subscription = events.subscribe(since, epoch)

    def generate():
        try:
            # An immediate byte, so the client knows the stream is *open* rather than merely
            # connecting — and so anything buffering until first byte lets go of it.
            yield ": open\n\n"

            if subscription.resync:
                # We cannot say what was missed, so we say that. The client refetches everything
                # it holds and takes the cursor we are actually at, rather than being told it is
                # up to date when it is not.
                yield _frame(events.Event(id=subscription.at, type="resync", data={}))
            else:
                for event in subscription.missed:
                    yield _frame(event)

            while True:
                try:
                    event = subscription.queue.get(timeout=_PING)
                except queue.Empty:
                    yield ": ping\n\n"
                    continue
                yield _frame(event)
        finally:
            # Reached when the client goes away, which for a generator is the next write after it
            # did. The keep-alive is what makes that happen at all on an idle stream.
            events.unsubscribe(subscription)

    response = Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Tells nginx and anything like it not to sit on the stream waiting for a whole
            # response. Harmless when nothing is in front of us, which is the usual case.
            "X-Accel-Buffering": "no",
            #
            # `Connection: keep-alive` was here, copied from every SSE example there is, and it is
            # a real bug rather than a redundancy: `Connection` is a hop-by-hop header, which
            # PEP 3333 forbids a WSGI application from setting. Werkzeug let it through; waitress
            # raises on it, so the whole endpoint answered 500 the moment it ran on a production
            # server. It was never doing anything — the connection stays open because the response
            # is a generator that has not finished, not because of a header.
        },
    )
    # Subscribing happens above, at request time, rather than in the generator's first line — a
    # generator does not run until something consumes it, and an event published in that gap would
    # be missed silently, which is the failure this whole module exists to remove.
    #
    # Which leaves one case the `finally` cannot reach: a response closed before it was ever
    # started, whose generator therefore never runs. `call_on_close` catches that, and unsubscribe
    # is a `discard` — so the two together are safe rather than merely likely.
    response.call_on_close(lambda: events.unsubscribe(subscription))
    return response
