"""Two gates can hold a turn, and they disagreed about what an absent person means.

`permissions._wait_for` and `questions.ask` are the same situation twice: the turn stops, and it
cannot go on until somebody says something. On the attended path they behave alike — raise a
badge, wait up to fifteen minutes, proceed on the answer. Unattended they did not.

    permission   refused instantly, silently
    ask          waited the full fifteen minutes, then said "nobody answered, carry on"

**`ask`'s waiting was the live bug.** The scheduler runs its wakes one after another on a single
timer thread (`services/scheduler.run`), so a reminder firing at four in the morning that asked
a question held that thread for a quarter of an hour — no other reminder fired, no finished
build was noticed. Permission had a guard against exactly this from August; `ask` never got one.

**Permission's silence was the other half.** Its refusal named an Allow button on a message
drawn on nobody's screen and then told him not to work around it, so the one instruction he
could follow was to stop — with nothing said about what he had stopped for. And `_tell_them`
sits below the refuse on purpose, because a refusal nobody is waiting on is not an interruption
worth making. That is true of interrupting and false of recording, and both were skipped
together: you came back to a turn that had quietly not done something, with no trace of what.

The shape both use now is `ask`'s, which is what this codebase already chose for "someone else's
turn" — see `domain/enums`, where `waiting` and `review` were deleted in its favour, on the
argument that a column waits to be noticed and a question does not.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kith.infra import permissions
from kith.kernel import live_turns, session_context
from kith.services import questions


@pytest.fixture
def a_live_turn(monkeypatch):
    """A turn that is running and watchable — so what is under test is *who is there*, not
    whether the machinery exists. Since `begin_turn`, an unattended turn has one of these too."""
    monkeypatch.setattr(live_turns, "current", lambda _c: object())
    monkeypatch.setattr(permissions, "mode", lambda: permissions.Mode.ASK)
    told: list[dict] = []
    monkeypatch.setattr(
        permissions, "_tell_them", lambda request, unattended=False: told.append({"unattended": unattended})
    )
    return told


class TestAskDoesNotWaitForNobody:
    def test_it_answers_at_once_when_nobody_is_there(self, monkeypatch):
        """The bug. Fifteen minutes of the scheduler's only timer thread, for a click that
        cannot happen."""
        monkeypatch.setattr(questions, "_tell_them", lambda *a, **k: None)
        with session_context.nobody_watching():
            out = questions.ask("c-1", [{"question": "which one?", "options": [{"label": "a"}]}])
        assert out["ok"] is True
        assert out["answered"] is False

    def test_it_tells_him_to_carry_on_and_say_which_way_he_went(self, monkeypatch):
        """Not an error. He is expected to proceed and report, which is the same thing the
        fifteen-minute deadline already told him — just without the fifteen minutes."""
        monkeypatch.setattr(questions, "_tell_them", lambda *a, **k: None)
        with session_context.nobody_watching():
            out = questions.ask("c-1", [{"question": "which?", "options": [{"label": "a"}]}])
        assert "carry on" in out["note"].lower()
        assert "which way you went" in out["note"]

    def test_the_question_is_still_put_on_the_badge(self, monkeypatch):
        """Its turn is long over by the time anyone reads it, and it is still worth seeing that
        he asked."""
        told: list[str] = []
        monkeypatch.setattr(questions, "_tell_them", lambda cid, asked: told.append(cid))
        with session_context.nobody_watching():
            questions.ask("c-9", [{"question": "which?", "options": [{"label": "a"}]}])
        assert told == ["c-9"]

    def test_nothing_is_left_open_behind_it(self, monkeypatch):
        """An unanswered question that stays registered would block the next real one — `ask`
        allows one open question per conversation."""
        monkeypatch.setattr(questions, "_tell_them", lambda *a, **k: None)
        with session_context.nobody_watching():
            questions.ask("c-1", [{"question": "which?", "options": [{"label": "a"}]}])
        assert questions._OPEN.get("c-1") is None

    def test_an_attended_ask_is_untouched(self, monkeypatch):
        """It still waits — that is the whole design. Deadline dropped to nothing so the test
        does not, and the shape of the answer is what is being checked."""
        monkeypatch.setattr(questions, "_tell_them", lambda *a, **k: None)
        out = questions.ask("c-1", [{"question": "which?", "options": [{"label": "a"}]}], deadline=0.01)
        assert out["answered"] is False
        assert "within the time allowed" in out["note"]


class TestARefusalNobodyCanAnswerIsWrittenDown:
    def test_it_still_refuses_immediately(self, a_live_turn):
        """Unchanged, and deliberately so. The alternative is parking the timer thread on a
        card drawn on no screen, which is what the guard was added to prevent."""
        with session_context.nobody_watching(), pytest.raises(permissions.Denied):
            permissions.require_path("write", Path("/etc/nope"), Path.home() / "Kith")

    def test_but_it_is_recorded_now(self, a_live_turn):
        """The gap. Nothing was written at all, so a turn that quietly skipped something left
        no trace of what it skipped."""
        with session_context.nobody_watching(), pytest.raises(permissions.Denied):
            permissions.require_path("write", Path("/etc/nope"), Path.home() / "Kith")
        assert a_live_turn == [{"unattended": True}]

    def test_it_does_not_promise_a_button_that_is_not_on_any_screen(self, a_live_turn):
        """What he was told to do was impossible, and the only part he could act on was 'stop'."""
        with session_context.nobody_watching(), pytest.raises(permissions.Denied) as refused:
            permissions.require_path("write", Path("/etc/nope"), Path.home() / "Kith")
        said = str(refused.value)
        assert "Allow button" not in said
        assert "nobody was there to ask" in said

    def test_it_tells_him_to_carry_on_and_say_what_he_skipped(self, a_live_turn):
        """The same sentence `ask` gives, because it is the same situation."""
        with session_context.nobody_watching(), pytest.raises(permissions.Denied) as refused:
            permissions.require_path("write", Path("/etc/nope"), Path.home() / "Kith")
        said = str(refused.value)
        assert "carry on without it" in said
        assert "what you" in said and "skipped" in said

    def test_an_attended_refusal_still_says_there_is_a_button(self, a_live_turn):
        """Because there is one. Both halves of this have to keep telling the truth."""
        # Nothing will answer, so the wait is cut to nothing rather than fifteen minutes.
        original = permissions._DEADLINE_SECONDS
        permissions._DEADLINE_SECONDS = 0.01
        try:
            with pytest.raises(permissions.Denied) as refused:
                permissions.require_path("write", Path("/etc/nope"), Path.home() / "Kith")
        finally:
            permissions._DEADLINE_SECONDS = original
        assert "Allow button" in str(refused.value)
        assert a_live_turn == [{"unattended": False}], "and the badge says it is waiting on them"
