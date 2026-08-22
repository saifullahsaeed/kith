"""`/api/events` as the client actually reads it.

`kernel/events` is tested on its own terms in `test_the_stream_can_be_resumed`. This is the wire:
the three lines that make an SSE frame, the header that resumes one, and the fact that both kinds
of event come down one connection — which is the whole reason there is one endpoint here instead of
the two there used to be.

Read with `iter_encoded()` and only ever a bounded number of frames. An SSE stream does not end;
after the replay it blocks for the keep-alive, so a test that asked for one frame more than the
server owes it would sit there for fifteen seconds and then pass anyway.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kith.kernel import changes, events
from kith.services import activity


@pytest.fixture
def client():
    """A test client on the real blueprint, with the token the API expects."""
    from kith import settings
    from kith.api import auth
    from kith.app import create_app

    made = create_app()
    made.config["TESTING"] = True
    given = made.test_client()
    given.environ_base[f"HTTP_{auth.HEADER.upper().replace('-', '_')}"] = auth.token(settings.DATA_DIR)
    return given


@pytest.fixture(autouse=True)
def no_leftover_subscribers():
    yield
    events.log._subscribers.clear()


def _read(response, frames: int) -> list[str]:
    """Exactly `frames` frames, then stop. Never one more — see the module docstring."""
    stream = response.iter_encoded()
    out = []
    while len(out) < frames:
        out.append(next(stream).decode())
    return out


def _parse(frame: str) -> dict:
    """One SSE frame as its fields, with the id split back into its two halves."""
    parsed: dict = {}
    for line in frame.strip().splitlines():
        field, _, value = line.partition(": ")
        parsed[field] = value
    if "data" in parsed:
        parsed["data"] = json.loads(parsed["data"])
    if "id" in parsed:
        epoch, _, number = parsed["id"].rpartition("-")
        parsed["epoch"], parsed["n"] = epoch, int(number)
    return parsed


def _at(cursor: int) -> str:
    """A cursor as this run would have written it."""
    return f"{events.EPOCH}-{cursor}"


class TestTheFrame:
    def test_it_opens_immediately(self, client):
        """A byte before anything has happened, so the client knows it is connected rather than
        connecting — and so anything that buffers until first byte lets go."""
        response = client.get("/api/events")
        try:
            assert _read(response, 1) == [": open\n\n"]
        finally:
            response.close()

    def test_a_change_carries_an_id_a_type_and_the_data(self, client):
        """The three lines the whole design rests on: `id:` is what comes back as
        `Last-Event-ID`, `event:` is what lets one connection carry both kinds.

        Resumed from wherever the log happens to be, not from zero. The log is per-process and
        shared with every other test in the run, so asking to replay from the beginning gets a
        backlog of everything the suite has published — or a `resync`, once that backlog is longer
        than the log holds. Which is the behaviour under test elsewhere, and noise here.
        """
        cursor = events.log.newest
        changes.publish("task")
        response = client.get("/api/events", headers={"Last-Event-ID": _at(cursor)})
        try:
            frame = _parse(_read(response, 2)[1])
        finally:
            response.close()
        assert frame["event"] == "changed"
        assert frame["data"] == {"kind": "task", "conversation": ""}
        # `<epoch>-<n>`: the number says where in the sequence, the epoch says which sequence.
        assert frame["n"] > 0
        assert frame["epoch"] == events.EPOCH

    def test_one_connection_carries_both_kinds(self, client):
        """Two endpoints held two of Chromium's six connections per origin. `event:` is how SSE
        was designed to multiplex, and using it is what collapsed them into one."""
        cursor = events.log.newest
        changes.publish("task")
        activity.feed.publish("tool", "read a file")

        response = client.get("/api/events", headers={"Last-Event-ID": _at(cursor)})
        try:
            frames = [_parse(frame) for frame in _read(response, 3)[1:]]
        finally:
            response.close()
        assert [frame["event"] for frame in frames] == ["changed", "activity"]
        assert frames[1]["data"]["text"] == "read a file"


class TestResuming:
    def test_last_event_id_replays_what_was_missed(self, client):
        cursor = events.log.newest
        changes.publish("task")
        changes.publish("project")

        response = client.get("/api/events", headers={"Last-Event-ID": _at(cursor)})
        try:
            frames = [_parse(frame) for frame in _read(response, 3)[1:]]
        finally:
            response.close()
        assert [frame["data"]["kind"] for frame in frames] == ["task", "project"]

    def test_a_cursor_from_another_run_gets_a_resync(self, client):
        """The late-reconnect case, and the reason the id carries an epoch at all.

        A number alone catches a restart only while the new run is still *behind* the old cursor.
        Reconnect after the fresh process has published past it and the number looks like an
        ordinary position — so the client would be handed the wrong events and told it was caught
        up. Here the cursor is well inside this run's range and still refused, because it belongs
        to a sequence that no longer exists.
        """
        changes.publish("task")
        inside = events.log.newest  # a perfectly ordinary position in *this* run
        response = client.get("/api/events", headers={"Last-Event-ID": f"deadbeef-{inside}"})
        try:
            frame = _parse(_read(response, 2)[1])
        finally:
            response.close()
        assert frame["event"] == "resync"

    def test_a_cursor_ahead_of_the_log_gets_a_resync(self, client):
        """The numeric check still stands on its own, for a client too old to send an epoch."""
        response = client.get("/api/events", headers={"Last-Event-ID": "999999"})
        try:
            frame = _parse(_read(response, 2)[1])
        finally:
            response.close()
        assert frame["event"] == "resync"

    def test_a_resync_carries_a_usable_cursor(self, client):
        """Otherwise the client's next reconnect asks the same impossible question again."""
        changes.publish("task")
        response = client.get("/api/events", headers={"Last-Event-ID": "999999"})
        try:
            frame = _parse(_read(response, 2)[1])
        finally:
            response.close()
        assert frame["n"] == events.log.newest
        assert frame["epoch"] == events.EPOCH

    def test_an_unreadable_cursor_is_treated_as_a_fresh_connection(self, client):
        """Not as an error. A 400 on a reconnect loop is a window that never recovers, and what
        the client needs next is its own refetch either way."""
        response = client.get("/api/events", headers={"Last-Event-ID": "not-a-number"})
        try:
            assert response.status_code == 200
            assert _read(response, 1) == [": open\n\n"]
        finally:
            response.close()


class TestHousekeeping:
    def test_the_headers_say_do_not_buffer_me(self, client):
        """A proxy or a dev server that buffers a stream turns push into a mystery."""
        response = client.get("/api/events")
        try:
            assert response.mimetype == "text/event-stream"
            assert response.headers["Cache-Control"] == "no-cache"
            assert response.headers["X-Accel-Buffering"] == "no"
        finally:
            response.close()

    def test_closing_the_response_lets_the_subscriber_go(self, client):
        """A stream nobody closed is a queue the server fills forever. Subscribing happens at
        request time rather than in the generator — so that no event published in the gap is
        missed — which means the release has to cover a response that was never even read."""
        before = events.log.subscribers
        response = client.get("/api/events")
        assert events.log.subscribers == before + 1
        response.close()
        assert events.log.subscribers == before


def test_no_other_stream_is_left(client):
    """The two endpoints this replaced are gone, not deprecated.

    Leaving them would leave the socket cost and the two failure modes in place for anything that
    had not been moved over — and "temporarily both" is how an app ends up with eleven timers and
    a push channel it does not trust.
    """
    assert client.get("/api/changes").status_code == 404
    assert client.get("/api/activity/stream").status_code == 404
    routes = {rule.rule for rule in client.application.url_map.iter_rules()}
    assert "/api/events" in routes
    assert not [rule for rule in routes if rule.endswith("/stream")], routes


def test_the_interface_reads_the_same_endpoint():
    """One name, checked from both sides.

    The client's URL is a string in TypeScript and the route is a decorator in Python, so nothing
    but this connects them — and the failure mode of getting it wrong is a window that connects to
    nothing and simply looks quiet.
    """
    ui = Path(__file__).resolve().parents[2] / "ui" / "src"
    used = (ui / "lib" / "backend" / "events.ts").read_text(encoding="utf-8")
    assert "/api/events" in used
