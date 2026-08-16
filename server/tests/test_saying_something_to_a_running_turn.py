"""Changing course mid-turn without throwing the turn away.

There was one way to change your mind while he was working: Stop. That kills the run and loses
everything it had worked out, and the next message starts from a cold prompt. It is a fire
alarm, and what it gets reached for with is a steering wheel.

The failure that earned this is in a real transcript. A question arrived eleven and a half hours
after the previous turn ended, and the turn that started on it reasoned "Continuing the task"
and re-answered the old one — because the new words were one line at the end of 435,000 tokens,
253,000 of which were tool results from the task that had just finished. Nothing was dropped;
the new instruction simply had no weight.

Steering fixes the half of that which is about *reaching a turn already running*. The text is
appended at a round boundary, so it arrives as the freshest thing in the prompt and everything
already worked out stays.

**Why a round boundary and not an interrupt.** A round is the only moment the conversation is a
list a provider will accept. Inside one there is a half-read stream and a tool call announced
but not answered, and appending there produces a message list that gets rejected. The wait is
one round.
"""

from __future__ import annotations

import pytest

from kith import tools
from kith.config import default_config
from kith.services import agent_loop, steering


@pytest.fixture(autouse=True)
def nothing_left_over():
    steering.forget_everything()
    yield
    steering.forget_everything()


def _model(*rounds):
    """A fake provider that plays the given rounds, recording the prompt it was handed."""
    seen: list[list[dict]] = []

    def fake(messages, *_a, **_k):
        seen.append(list(messages))
        yield from rounds[min(len(seen) - 1, len(rounds) - 1)]

    return fake, seen


def _said(prompt: list[dict]) -> list[str]:
    return [str(m.get("content") or "") for m in prompt if m.get("role") == "user"]


class TestItReachesTheTurn:
    def test_what_you_say_arrives_at_the_next_round(self, db, monkeypatch):
        fake, seen = _model([{"type": "delta", "text": "..."}], [{"type": "delta", "text": "ok"}])
        monkeypatch.setattr(agent_loop, "_stream_once", fake)
        steering.steer("c1", "actually, check the milestones instead")

        list(
            agent_loop._run_turn(
                [{"role": "user", "content": "the original task"}],
                default_config(),
                "host",
                db,
                tools.host(db, language_server=False),
                max_rounds=8,
                conversation_id="c1",
                steer=lambda: steering.take("c1"),
            )
        )
        assert len(seen) >= 1
        assert "actually, check the milestones instead" in _said(seen[0])

    def test_it_arrives_as_the_freshest_thing_in_the_prompt(self, db, monkeypatch):
        """The whole point. Anywhere earlier and it competes with the task it is correcting."""
        fake, seen = _model([{"type": "delta", "text": "ok"}])
        monkeypatch.setattr(agent_loop, "_stream_once", fake)
        steering.steer("c1", "stop doing that")

        list(
            agent_loop._run_turn(
                [{"role": "user", "content": "the original task"}],
                default_config(),
                "host",
                db,
                tools.host(db, language_server=False),
                max_rounds=8,
                conversation_id="c1",
                steer=lambda: steering.take("c1"),
            )
        )
        assert _said(seen[0])[-1] == "stop doing that"

    def test_nothing_already_worked_out_is_lost(self, db, monkeypatch):
        """The difference from Stop. The original request is still there."""
        fake, seen = _model([{"type": "delta", "text": "ok"}])
        monkeypatch.setattr(agent_loop, "_stream_once", fake)
        steering.steer("c1", "and the ui side too")

        list(
            agent_loop._run_turn(
                [{"role": "user", "content": "the original task"}],
                default_config(),
                "host",
                db,
                tools.host(db, language_server=False),
                max_rounds=8,
                conversation_id="c1",
                steer=lambda: steering.take("c1"),
            )
        )
        assert "the original task" in _said(seen[0])

    def test_it_is_announced_so_the_window_can_show_it(self, db, monkeypatch):
        fake, _ = _model([{"type": "delta", "text": "ok"}])
        monkeypatch.setattr(agent_loop, "_stream_once", fake)
        steering.steer("c1", "changed my mind")

        events = list(
            agent_loop._run_turn(
                [],
                default_config(),
                "host",
                db,
                tools.host(db, language_server=False),
                max_rounds=8,
                conversation_id="c1",
                steer=lambda: steering.take("c1"),
            )
        )
        steered = [e for e in events if e.get("type") == "steered"]
        assert steered and steered[0]["text"] == "changed my mind"

    def test_a_turn_with_nothing_said_to_it_is_unchanged(self, db, monkeypatch):
        """Costs nothing when nobody steers, which is nearly every turn."""
        fake, seen = _model([{"type": "delta", "text": "ok"}])
        monkeypatch.setattr(agent_loop, "_stream_once", fake)

        list(
            agent_loop._run_turn(
                [{"role": "user", "content": "just the task"}],
                default_config(),
                "host",
                db,
                tools.host(db, language_server=False),
                max_rounds=8,
                conversation_id="c1",
                steer=lambda: steering.take("c1"),
            )
        )
        assert _said(seen[0]) == ["just the task"]

    def test_two_things_typed_quickly_arrive_together(self, db, monkeypatch):
        """One thought split across two sends. Delivering them a round apart would have him
        act on half of it first."""
        fake, seen = _model([{"type": "delta", "text": "ok"}])
        monkeypatch.setattr(agent_loop, "_stream_once", fake)
        steering.steer("c1", "wait")
        steering.steer("c1", "do the backend first")

        list(
            agent_loop._run_turn(
                [],
                default_config(),
                "host",
                db,
                tools.host(db, language_server=False),
                max_rounds=8,
                conversation_id="c1",
                steer=lambda: steering.take("c1"),
            )
        )
        assert _said(seen[0])[-1] == "wait\n\ndo the backend first"


class TestTheQueueItself:
    def test_blank_is_not_something_to_say(self):
        assert not steering.steer("c1", "   ")

    def test_it_is_drained_by_reading(self):
        steering.steer("c1", "once")
        assert steering.take("c1") == "once"
        assert steering.take("c1") == ""

    def test_it_will_not_hold_an_unbounded_pile(self):
        """Past a handful the person is typing at a wall, not steering — and delivering nine
        at once buries the round they were meant to change."""
        for i in range(steering.MAX_WAITING + 4):
            steering.steer("c1", f"message {i}")
        assert steering.waiting("c1") == steering.MAX_WAITING

    def test_conversations_do_not_leak_into_each_other(self):
        steering.steer("c1", "for one")
        steering.steer("c2", "for two")
        assert steering.take("c1") == "for one"
        assert steering.take("c2") == "for two"

    def test_forgetting_leaves_nothing_for_the_next_turn(self):
        """Text nobody answered still belonged to a turn that is over, and its recent history
        no longer matches what the text was reacting to."""
        steering.steer("c1", "left over")
        steering.forget("c1")
        assert steering.take("c1") == ""
