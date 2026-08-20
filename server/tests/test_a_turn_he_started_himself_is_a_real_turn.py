"""There is one way a turn begins, and everything that supervises one is attached to it.

There used to be two. A typed message went through `chat()` and got the apparatus: a live turn
the window can follow, a stop switch, a steer queue, the session binding, one place that closes
the books however it ends. A turn Kith started himself — a reminder due, a background task
finishing — went through `continue_conversation`, which built the messages and drove `_turn` by
hand, and had none of it.

That was never a design. The dates say so:

    2026-08-08  the turn moves off the request thread (`work()`)
    2026-08-09  the live turn stops belonging to a connection
    2026-08-14  `continue_conversation` is written

It was written five days after the door it needed was already open, and it was written for an
unrelated reason: `services/scheduler` had been importing three *private* functions out of the
chat route, the last upward edge in the tree. Exposing something public for it to call was
right. What got exposed was a second copy of the turn half, and every piece of supervision was
quietly left behind in the copy that stayed.

What that cost, concretely: an unattended turn could not be watched (`workspace.tsx` has
listened for the event since it shipped and only `live_turns.begin` sends it, so the transcript
grew on disk and you found out on reload), could not be stopped, and could not be steered —
`/steer` answers "nothing is running", so typing at one started a *second* turn on the same
conversation.
"""

from __future__ import annotations

import time

import pytest

from kith.api.routes import chat as route
from kith.kernel import live_turns, session_context


@pytest.fixture
def turn(monkeypatch):
    """A turn that does nothing, so what is asserted is the machinery around it."""
    seen: dict = {}

    def _turn(recorder, messages, config, conversation_id, opening="", *, stop_switch=None):
        seen["messages"] = messages
        seen["opening"] = opening
        seen["stopping"] = stop_switch
        seen["conversation"] = session_context.current()
        seen["unattended"] = session_context.unattended()
        seen["live"] = live_turns.current(conversation_id) is not None
        yield '{"type": "delta", "role": "text", "text": "hi"}\n'

    monkeypatch.setattr(route, "_turn", _turn)
    monkeypatch.setattr(route, "_build_messages", lambda history, *a, **k: list(history))
    monkeypatch.setattr(route, "_tool_block_chars", lambda: 0)
    monkeypatch.setattr(route.conversations, "record", lambda *a, **k: None)
    monkeypatch.setattr(route.conversations, "full_messages", lambda _c: [])
    return seen


def _drive(conversation_id: str, trigger: str = "a build finished") -> None:
    route.continue_conversation(conversation_id, trigger)


class TestTheSchedulersTurnIsSupervised:
    def test_it_registers_as_a_live_turn(self, turn):
        """The one the window listens for. Without it the transcript grows on disk and the
        only way to find out is to reload — which is what the UI comment calls "the second
        Cmd-R"."""
        _drive("c-1")
        assert turn["live"] is True

    def test_it_can_be_stopped(self, turn):
        """`_turn` reads this between events. None means nothing can interrupt it, which is
        what an autonomous turn used to be handed."""
        _drive("c-1")
        assert turn["stopping"] is not None

    def test_it_knows_which_conversation_it_is(self, turn):
        """`paths.base_dir` reads this to decide where a relative path lands. Set by the
        caller until today and by the turn itself now, so it cannot be forgotten at a call
        site again."""
        _drive("c-7")
        assert turn["conversation"] == "c-7"

    def test_the_trigger_reaches_the_turn_as_its_opening(self, turn):
        _drive("c-1", "One of your reminders just fired.")
        assert turn["opening"] == "One of your reminders just fired."
        assert turn["messages"][-1]["content"] == "One of your reminders just fired."

    def test_the_switch_is_released_when_it_ends(self, turn):
        """A turn that ends leaving its switch armed makes the *next* one in that conversation
        answer to a stop nobody pressed."""
        _drive("c-1")
        assert route._RUNNING.get("c-1") is None

    def test_the_live_turn_is_finished(self, turn):
        """`live_turns.finish` releases every reader. A watcher left waiting on a turn that
        already ended is a window that never stops spinning."""
        _drive("c-1")
        assert live_turns.current("c-1") is None


class TestItIsTheSameDoor:
    def test_only_one_place_begins_a_turn(self):
        """The whole point. Two call sites means two chances to forget one of these."""
        source = route.__file__
        with open(source) as handle:
            body = handle.read()
        assert body.count("live_turns.begin(") == 1
        assert body.count("_Recorder(conversation_id)") == 1

    def test_the_scheduler_still_gets_a_synchronous_call(self, turn):
        """`wake_finished` and `fire_due` both loop over conversations calling this. Returning
        the moment the thread started would set every one of them going at once."""
        started = time.monotonic()
        _drive("c-1")
        assert turn.get("messages") is not None, "the turn ran before the call returned"
        assert time.monotonic() - started < 30


class TestNobodyIsWatchingIsStillTrue:
    def test_an_unattended_turn_says_so_from_inside(self, turn):
        """The flag the permission guard now reads. It is set by the scheduler and has to
        survive the hop onto the turn's own thread — `contextvars.copy_context()` is what
        carries it, and a bare `Thread(target=...)` would have dropped it silently."""
        with session_context.nobody_watching():
            _drive("c-1")
        assert turn["unattended"] is True

    def test_a_turn_nobody_asked_for_is_not_mistaken_for_a_watched_one(self, turn):
        """Why the permission guard could not stay as it was. It used to ask `live_turns`
        whether a turn was running and read the answer as "somebody is looking" — safe only
        while the scheduler ran outside the machinery. It doesn't any more."""
        with session_context.nobody_watching():
            _drive("c-1")
        assert turn["live"] is True and turn["unattended"] is True
