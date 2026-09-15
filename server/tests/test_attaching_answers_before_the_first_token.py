"""Attaching to a turn answers when you attach, not when the turn first speaks.

A WSGI server writes the status line and headers on the **first chunk the body yields**. So a
streaming response whose generator is still blocked has not answered at all yet: the client's
`fetch` promise is pending, a connection is held waiting for it, and there is nothing on screen
to say the stream was ever joined.

`/attach` was exactly that shape. It hands back `live_turns.watch`, which yields the backlog and
then blocks — and at the moment `live_turns.begin` announces a turn, the backlog is empty. So the
window's rejoin, which fires on that announcement, could not resolve until the turn's first
token: after the transcript is read, after the prompt is built, and after any fold has made its
own round trip to the model. Measured both ways before this was fixed — against waitress a
generator silent for four seconds answered at four seconds, and an empty first chunk changed
nothing, because a WSGI server treats that as nothing written.

Which is the same problem `/api/events` sends `: open` for, and says so in its own comment. Here
the equivalent is a blank line, because `readEvents` skips every empty line it reads.

Two things followed from the gap, and both read as the interface being broken rather than as a
stream that had not started:

* a turn Kith began himself — a background task finishing — streamed into nobody until its first
  token, and if anything went wrong in that window the reply stayed on disk with nothing on
  screen ever fetching it, because the transcript page is fetched once, at mount;
* `queued-send`'s `pollUntilIdle` uses this endpoint as a liveness probe "every two seconds", and
  a request that cannot answer until the turn ends is not a poll. Each one pending holds one of
  Chromium's six connections per origin, which is how the renderer came to sit with a saturated
  pool and every later fetch queued behind it.

The request is made on a thread throughout, because the failure being tested for is a call that
never returns — asserting on it directly would hang the suite rather than fail it.
"""

from __future__ import annotations

import json
import threading

import pytest

from kith.kernel import events, live_turns


@pytest.fixture(autouse=True)
def no_leftover_turns():
    yield
    live_turns._LIVE.clear()
    events.log._subscribers.clear()


@pytest.fixture
def client():
    from kith import settings
    from kith.api import auth
    from kith.app import create_app

    made = create_app()
    made.config["TESTING"] = True
    given = made.test_client()
    given.environ_base[f"HTTP_{auth.HEADER.upper().replace('-', '_')}"] = auth.token(settings.DATA_DIR)
    return given


def _attach(client, conversation_id: str, timeout: float = 2.0):
    """Ask to watch a turn, and give up waiting rather than hanging.

    Returns the response, or None if the call had not come back inside the deadline — which is
    the regression: a body that yields nothing is a request that has not answered.
    """
    answered: list = []
    asking = threading.Thread(
        target=lambda: answered.append(client.get(f"/api/chat/{conversation_id}/attach")),
        daemon=True,
    )
    asking.start()
    asking.join(timeout)
    return answered[0] if answered else None


class TestItAnswersWhenYouAttach:
    def test_a_turn_that_has_said_nothing_still_opens_the_stream(self, client):
        """The whole point. `rejoin` fires on the `turn` event `live_turns.begin` sends, which is
        before the turn has published a single line."""
        turn = live_turns.begin("c-1")
        answered = _attach(client, "c-1")
        live_turns.finish(turn)
        assert answered is not None, (
            "the body never yielded, so no headers were sent and the client's fetch is still pending"
        )
        assert answered.status_code == 200

    def test_the_opening_byte_is_not_an_event(self, client):
        """A blank line rather than a payload, so the client cannot read "a stream opened" as
        something that happened inside the turn. `readEvents` skips every empty line.

        The turn is ended before the body is read, because reading it is what drains the
        generator and a live turn's generator is waiting for the next thing to say.
        """
        turn = live_turns.begin("c-1")
        answered = _attach(client, "c-1")
        live_turns.finish(turn)
        assert answered is not None
        assert answered.get_data(as_text=True).split("\n", 1)[0].strip() == ""

    def test_what_the_turn_says_still_arrives(self, client):
        """The opening byte is an addition, not a replacement: the backlog and everything after
        it come through behind it exactly as before."""
        turn = live_turns.begin("c-2")
        live_turns.publish(turn, json.dumps({"type": "delta", "text": "hi"}) + "\n")
        answered = _attach(client, "c-2")
        live_turns.finish(turn)
        assert answered is not None
        assert '"hi"' in answered.get_data(as_text=True)

    def test_nothing_running_is_still_no_content(self, client):
        """The opening byte must not turn "nothing is running" into a stream that looks live —
        `resumeTurn` reads 204 as "this conversation is idle", and so does `pollUntilIdle`."""
        answered = _attach(client, "c-nothing")
        assert answered is not None and answered.status_code == 204


class TestAnsweringEarlyDoesNotLoseTheTurn:
    """Answering before the turn speaks means nothing pulls on the body until the client reads
    again — so joining can no longer ride on that first pull.

    This is the bug the opening byte would have introduced if `live_turns.watch` had been left
    whole: a generator body does not run until something consumes it, so the reader registered
    itself and snapshotted the backlog on the response's first pull. With the response answering
    on its own byte, a turn that ended in the gap cleared its backlog with the reader not yet in
    `watchers`, and the reply was lost to a stream that had already returned 200.
    """

    def test_a_reader_that_joins_and_reads_later_still_gets_the_backlog(self):
        """`read` at request time, `drain` whenever the client gets round to it."""
        turn = live_turns.begin("c-3")
        reader = live_turns.read(turn)
        live_turns.publish(turn, json.dumps({"type": "delta", "text": "said"}) + "\n")
        live_turns.finish(turn)
        assert '"said"' in "".join(live_turns.drain(turn, reader))

    def test_joining_after_it_ended_is_empty_rather_than_stale(self):
        """The other half of the same question. Once a turn is over the transcript is the record,
        so a late reader is given nothing rather than a replay — see this module's docstring."""
        turn = live_turns.begin("c-4")
        live_turns.publish(turn, json.dumps({"type": "delta", "text": "said"}) + "\n")
        live_turns.finish(turn)
        assert list(live_turns.drain(turn, live_turns.read(turn))) == []

    def test_a_reader_is_let_go_of_when_it_stops_reading(self):
        """`drain`'s `finally`. A reader that hangs up must not leave a queue behind that
        `publish` goes on filling for the rest of the turn."""
        turn = live_turns.begin("c-5")
        try:
            reader = live_turns.read(turn)
            stream = live_turns.drain(turn, reader)
            live_turns.publish(turn, json.dumps({"type": "delta", "text": "one"}) + "\n")
            next(stream)
            assert reader.inbox in turn.watchers
            stream.close()
            assert reader.inbox not in turn.watchers
        finally:
            live_turns.finish(turn)
