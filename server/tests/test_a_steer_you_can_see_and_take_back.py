"""A steer is visible from the moment it is said, and nothing typed is thrown away.

Pressing Enter during a turn puts the text in a queue that reaches the model at the next round
boundary. Three things could happen to it and only one of them was any good:

* **Delivered.** It appeared in the thread when a round took it — the one moment you no longer
  need telling that it landed. Until then it had left the composer and appeared nowhere.
* **Withdrawn.** Not possible. A queue you can watch and not change is a queue that makes you wait
  for your own mistake to be read out — and you could not even watch it.
* **Never read.** Silently deleted at the end of the turn. `forget` argued that carrying it into
  the next prompt would put it in front of a model whose history no longer matched, which is true,
  and does not lead to the bin: a new turn is a fresh prompt.

So the queue is readable, it can be taken back, and what nobody read is sent rather than dropped.
Recording moved with it — a steer is written to the transcript when something happens to it, not
when it is queued, because a steer you take back should never have been in the record at all.
"""

from __future__ import annotations

import pytest

from kith.kernel import events, live_turns
from kith.services import steering


@pytest.fixture(autouse=True)
def a_clean_queue():
    steering.forget_everything()
    yield
    steering.forget_everything()
    live_turns._LIVE.clear()
    events.log._subscribers.clear()


class TestSeeingIt:
    def test_what_is_queued_can_be_read_back(self):
        steering.steer("c-1", "actually, do the other one")
        assert steering.pending("c-1") == ["actually, do the other one"]

    def test_it_comes_back_in_the_order_it_was_said(self):
        """Two sentences typed three seconds apart are one thought."""
        steering.steer("c-1", "first")
        steering.steer("c-1", "second")
        assert steering.pending("c-1") == ["first", "second"]

    def test_reading_does_not_consume(self):
        """`take` is delivery. Looking at the queue must not empty it, or the screen would
        clear it by rendering."""
        steering.steer("c-1", "still here")
        steering.pending("c-1")
        assert steering.pending("c-1") == ["still here"]

    def test_nothing_queued_is_an_empty_list(self):
        assert steering.pending("c-1") == []


class TestTakingItBack:
    def test_withdraw_empties_the_queue(self):
        steering.steer("c-1", "ignore that")
        assert steering.withdraw("c-1") is True
        assert steering.pending("c-1") == []

    def test_withdrawing_nothing_says_so(self):
        assert steering.withdraw("c-1") is False

    def test_a_delivered_steer_cannot_be_withdrawn(self):
        """Once a round has taken it, it is in the prompt. Saying otherwise would be a button
        that claims to reach into a request already sent."""
        steering.steer("c-1", "gone to the model")
        assert steering.take("c-1") == "gone to the model"
        assert steering.withdraw("c-1") is False

    def test_withdrawing_one_conversation_leaves_another(self):
        steering.steer("c-1", "mine")
        steering.steer("c-2", "theirs")
        steering.withdraw("c-1")
        assert steering.pending("c-2") == ["theirs"]


class TestNothingIsThrownAway:
    def test_delivery_records_it_and_says_the_queue_moved(self, db, monkeypatch):
        """Recorded at the boundary that takes it — immediately before the round that acts on
        it, which is what the old record-on-queue was reaching for and missed for the two cases
        where delivery never happened."""
        from kith.api.routes import chat as route
        from kith.services import conversations

        monkeypatch.setattr(route, "AGENT_DB_PATH", db)
        started = conversations.start(db, "hello")
        id = str(started["id"])
        steering.steer(id, "change of plan")

        subscription = events.subscribe()
        try:
            assert route._deliver_steer(id) == "change of plan"
            kinds = _kinds(subscription)
        finally:
            events.unsubscribe(subscription)

        assert "steer" in kinds, "the pending line has to come off the screen"
        assert conversations.full_messages(id)[-1]["content"] == "change of plan"

    def test_delivering_an_empty_queue_records_nothing(self, db, monkeypatch):
        """The loop asks at every round boundary. Most of them have nothing waiting, and a
        transcript with an empty user message per round would be unreadable."""
        from kith.api.routes import chat as route
        from kith.services import conversations

        monkeypatch.setattr(route, "AGENT_DB_PATH", db)
        started = conversations.start(db, "hello")
        id = str(started["id"])
        before = len(conversations.full_messages(id))

        assert route._deliver_steer(id) == ""
        assert len(conversations.full_messages(id)) == before

    def test_what_the_turn_never_read_is_sent_rather_than_deleted(self, db, monkeypatch):
        """The end of a turn used to be where a steer went to die."""
        from kith.api.routes import chat as route
        from kith.services import conversations

        monkeypatch.setattr(route, "AGENT_DB_PATH", db)
        started = conversations.start(db, "hello")
        id = str(started["id"])
        begun: list = []
        monkeypatch.setattr(route, "begin_turn", lambda *a, **k: begun.append(a) and None)
        monkeypatch.setattr(route.live_turns, "watch", lambda live: iter(()))

        route._send_unread_steer(id, "you never read this")

        assert len(begun) == 1, "it gets a turn of its own"
        assert conversations.full_messages(id)[-1]["content"] == "you never read this"

    def test_a_failure_to_send_does_not_raise_into_the_turn_thread(self, db, monkeypatch):
        """This runs in the `finally` of a turn that has already released its readers. Raising
        there is an exception nobody can see."""
        from kith.api.routes import chat as route

        monkeypatch.setattr(route, "AGENT_DB_PATH", db)
        monkeypatch.setattr(route, "begin_turn", _explode)

        route._send_unread_steer("c-1", "still fine")  # must not raise


def _explode(*args, **kwargs):
    raise RuntimeError("no")


def _kinds(subscription) -> list[str]:
    out = []
    while True:
        try:
            event = subscription.queue.get_nowait()
        except Exception:
            return out
        if event.type == "changed":
            out.append(event.data["kind"])
