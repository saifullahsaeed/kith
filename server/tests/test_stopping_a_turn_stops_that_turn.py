"""Stop belongs to a turn, not to a conversation — and a stopped turn still finishes itself.

The turn no longer dies when the socket does (that is what killed a turn when you merely
switched conversations), so stopping is said out loud instead: a route sets an Event and the
worker reads it between events. That is the right shape, but the switch was filed under the
*conversation*, and a conversation outlives the turn running in it. So a second turn's switch
overwrote the first's, and the first turn to finish deleted the entry the still-running one
was reading — after which Stop reported "nothing to stop" for a turn plainly in progress.

The other half is what "stopped" costs. Stopping abandoned the `_turn` generator mid-loop,
which is not the same as ending it: everything after its last `yield` — the tick-log row
saying what the turn spent, the feed's own "done" — simply never ran. A turn you stopped is
exactly the one whose cost you want to look at, and it was the only kind that left no row.
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

from kith.api.routes import chat as route
from kith.infra.db import repositories as repo
from kith.services import conversations


@pytest.fixture
def feed(db: Path, monkeypatch):
    """The Mind feed, pointed at a temp database and stubbed at the module.

    Patched through `sys.modules` rather than by setting an attribute: `_MindFeed._publish`
    does `from kith.services.activity import feed` inside the call, so it reads the module out
    of `sys.modules` every time and an attribute patch would never be seen.
    """
    published: list[dict] = []
    monkeypatch.setattr(route, "AGENT_DB_PATH", db)

    class FakeFeed:
        def publish(self, kind, text, **fields):
            published.append({"kind": kind, "text": text, **fields})

        def charge_session(self, conversation_id, uncached_in, cost_usd):
            return False  # never over budget; the cap has its own tests

    monkeypatch.setitem(sys.modules, "kith.services.activity", type("M", (), {"feed": FakeFeed()})())
    return published


@pytest.fixture
def conversation(db: Path):
    return conversations.start(db, "hi")["id"]


def _drive(monkeypatch, conversation: str, events: list[dict], stop_after: int) -> list[dict]:
    """Run `_turn` over a fixed list of events, setting the switch once `stop_after` lines
    have come out — the same interleaving as a click landing mid-turn."""

    def fake_stream(*args, **kwargs):
        yield from events

    monkeypatch.setattr(route, "stream_agent", fake_stream)

    stopping = threading.Event()
    recorder = route._Recorder(conversation)
    seen: list[dict] = []
    for line in route._turn(recorder, [], route.default_config(), conversation, "go", stopping=stopping):
        seen.append(json.loads(line))
        if len(seen) == stop_after:
            stopping.set()
    return seen


class TestTheSwitchBelongsToOneTurn:
    """`_RUNNING` is read by a running turn and written by every new one, so identity matters:
    a turn may only be stopped by its own switch, and may only clear its own on the way out."""

    def test_a_finished_turn_does_not_disarm_the_one_still_running(self, conversation):
        first = route._arm(conversation)
        second = route._arm(conversation)

        route._disarm(conversation, first)  # the first turn ends, having never been stopped

        # Before, an unconditional `pop` took the second turn's switch with it, and Stop then
        # had nothing to set for a turn plainly in progress.
        assert route._current(conversation) is second

    def test_stop_reaches_the_current_turn_and_leaves_the_older_one(self, conversation):
        first = route._arm(conversation)
        second = route._arm(conversation)

        route._stop(conversation)

        # Sharing one dict slot meant both turns read whichever switch was written last, so a
        # single click stopped two turns.
        assert second.is_set()
        assert not first.is_set()

    def test_the_last_turn_out_clears_the_conversation(self, conversation):
        first = route._arm(conversation)
        second = route._arm(conversation)

        route._disarm(conversation, first)
        route._disarm(conversation, second)

        assert route._current(conversation) is None

    def test_stopping_a_conversation_with_no_turn_says_so(self, conversation):
        assert route._stop(conversation) is False


class TestWhatAStoppedTurnLeavesBehind:
    """A stopped turn is a turn that happened: it spent tokens, it may have said something,
    and both belong on the record."""

    def test_it_leaves_a_row_saying_what_it_spent(self, db: Path, conversation, feed, monkeypatch):
        events = [
            {"type": "delta", "role": "text", "text": "working on it"},
            {
                "type": "stats",
                "stats": {"promptTokens": 900, "responseTokens": 40, "uncachedTokens": 900},
            },
            {"type": "delta", "role": "text", "text": " — and the rest"},
        ]

        _drive(monkeypatch, conversation, events, stop_after=2)

        # Before, the worker returned out of its own loop and abandoned the generator, so
        # `_MindFeed.finish` never ran and the turn was absent from the log it should dominate.
        rows = repo.messages.list_turn_log(db, 10)
        assert len(rows) == 1
        assert (rows[0]["mode"], rows[0]["tokens_out"]) == ("chat", 40)

    def test_the_feed_hears_the_turn_close(self, conversation, feed, monkeypatch):
        _drive(monkeypatch, conversation, [{"type": "delta", "role": "text", "text": "hi"}], 1)

        # Without this the Mind panel shows a turn that never stops running.
        assert [line for line in feed if line["kind"] == "done"]

    def test_it_stops_before_the_next_event(self, conversation, feed, monkeypatch):
        events = [
            {"type": "delta", "role": "text", "text": "one"},
            {"type": "delta", "role": "text", "text": "two"},
        ]

        seen = _drive(monkeypatch, conversation, events, stop_after=1)

        assert [line.get("text") for line in seen if line["type"] == "delta"] == ["one"]

    def test_it_keeps_the_half_answer_and_says_it_was_stopped(self, conversation, feed, monkeypatch):
        events = [
            {"type": "delta", "role": "text", "text": "half an answer"},
            {"type": "delta", "role": "text", "text": " and the rest"},
        ]

        _drive(monkeypatch, conversation, events, stop_after=1)

        # Read from the transcript rather than from `timeline()`: the marker is a fact about the
        # turn, and `timeline()` is the *interface* view, which renders parts of a reply. What
        # the half-answer must never be is absent — a stopped answer is still an answer given.
        assert any(entry.get("type") == "stopped" for entry in conversations.read(conversation))
        said = [m["content"] for m in conversations.full_messages(conversation) if m["role"] == "assistant"]
        assert said == ["half an answer"]

    def test_stopping_as_the_turn_ends_records_the_reply_once(
        self, db: Path, conversation, feed, monkeypatch
    ):
        """The race the queue makes easy to lose: the switch is set after the *last* event, so
        the turn is already over by the time anyone reads it. Two finishes wrote the whole reply
        into the transcript twice — the next turn's prompt, and the person rereading it, saw him
        say the same thing twice. One exit makes that unrepresentable."""
        events = [{"type": "delta", "role": "text", "text": "all done"}]

        _drive(monkeypatch, conversation, events, stop_after=1)

        said = [m["content"] for m in conversations.full_messages(conversation) if m["role"] == "assistant"]
        assert said == ["all done"]
        assert len(repo.messages.list_turn_log(db, 10)) == 1


class TestATurnNobodyStops:
    """The ordinary path, unchanged: `stopping` left as None is the reminder path in
    `autonomy.runner`, where there is nobody to click anything."""

    def test_it_runs_to_the_end_and_says_done(self, db: Path, conversation, feed, monkeypatch):
        def fake_stream(*args, **kwargs):
            yield {"type": "delta", "role": "text", "text": "finished"}

        monkeypatch.setattr(route, "stream_agent", fake_stream)
        recorder = route._Recorder(conversation)

        seen = [
            json.loads(line) for line in route._turn(recorder, [], route.default_config(), conversation, "go")
        ]

        assert seen[-1]["type"] == "done"
        timeline = conversations.timeline(conversation)
        assert not [entry for entry in timeline if entry.get("kind") == "stopped"]
        assert len(repo.messages.list_turn_log(db, 10)) == 1

    def test_a_turn_that_dies_says_so_rather_than_reading_as_answered(
        self, db: Path, conversation, feed, monkeypatch
    ):
        """Not an error *event* — the stream itself blowing up, which is what a provider hanging
        up looks like. One exit means the books close either way; the risk is that they close
        silently, and a turn that crashed would be logged as one that answered."""

        def fake_stream(*args, **kwargs):
            yield {"type": "delta", "role": "text", "text": "starting"}
            raise RuntimeError("the provider hung up")

        monkeypatch.setattr(route, "stream_agent", fake_stream)
        recorder = route._Recorder(conversation)

        with pytest.raises(RuntimeError):
            list(route._turn(recorder, [], route.default_config(), conversation, "go"))

        rows = repo.messages.list_turn_log(db, 10)
        assert len(rows) == 1
        assert rows[0]["outcome"] == "error: the provider hung up"

    def test_an_error_still_closes_the_books_once(self, db: Path, conversation, feed, monkeypatch):
        def fake_stream(*args, **kwargs):
            yield {"type": "error", "message": "provider timed out"}

        monkeypatch.setattr(route, "stream_agent", fake_stream)
        recorder = route._Recorder(conversation)

        seen = [
            json.loads(line) for line in route._turn(recorder, [], route.default_config(), conversation, "go")
        ]

        assert seen[-1]["type"] == "error"  # no "done" after a turn that died
        rows = repo.messages.list_turn_log(db, 10)
        assert len(rows) == 1
        assert rows[0]["outcome"] == "error: provider timed out"


class TestTheFlagGoesUpBeforeTheWaitsAreReleased:
    """Stop has to be true before it is loud, or it hands the turn one more round.

    A turn parked on `ask` is not reading the switch — it is inside a tool call, waiting on an
    event — so Stop has to release that wait or nothing happens for fifteen minutes. It used to
    release *first* and set the flag afterwards, which is a race the turn can win: `ask` returns
    the instant the event is set, the loop appends the result, `_turn` yields it and only then
    reads the flag. Lose that and the flag is still down, so the turn carries on into another
    model call — on the full conversation — after the person pressed Stop.

    Both observed stops in a real conversation shut down cleanly, so this is the window being
    closed rather than a reproduction of it. That is the point: the ordering is what makes the
    window exist at all, and it costs nothing to not have one.
    """

    def test_the_switch_is_already_set_when_the_question_is_released(self, conversation, monkeypatch):
        seen: list[bool] = []
        event = route._arm(conversation)
        monkeypatch.setattr(route.questions, "release", lambda _id: seen.append(event.is_set()))
        monkeypatch.setattr(route.permissions, "release_waiting", lambda: None)

        route._stop(conversation)

        assert seen == [True], (
            "the question was released while the stop flag was still down — the turn can wake, "
            "reach its next check, find nothing set, and go round again"
        )

    def test_it_still_reports_whether_there_was_a_turn(self, conversation, monkeypatch):
        monkeypatch.setattr(route.questions, "release", lambda _id: None)
        monkeypatch.setattr(route.permissions, "release_waiting", lambda: None)

        assert route._stop(conversation) is False
        route._arm(conversation)
        assert route._stop(conversation) is True
