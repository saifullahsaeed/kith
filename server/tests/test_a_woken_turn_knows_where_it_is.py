"""A turn woken by the scheduler lands in the right folder, and is told the truth about why.

Two defects on one path, found by asking which autonomous path actually gets used. The answer
is not the one the code was written for: 21 reminders, all `done`, none since 2026-08-12 — and
they read *"Check Task #99 full local pytest run"*, *"Collect Task #90 runtime full backend"*,
because reminders were being used to poll long-running work. Zero schedules, ever. Meanwhile
`start_process` appears 196 times across 221 transcripts. The reminder path was a workaround;
`wake_finished` is what replaced it, and it is the one that had neither fix.

**The folder.** `fire_due` wraps its continuation in `session_context.working_in(...)`;
`wake_finished` called `_continue` bare. `paths.base_dir` decides where a relative path lands
by asking `session_context.current()`, so with nothing set it falls through to `root()` — and
a turn woken because a build finished wrote its report into ~/Kith instead of the project it
belongs to. `git` inherits the same base, so `changes` and `commit` read whichever repo is at
the root. Nothing fails: the file is written, the commit succeeds, and neither is where anyone
would look. `paths.py` already carries a note about a previous incident of this exact fallback.

**The sentence.** `_continue` opened every wake with "One of your reminders just fired",
including the ones where a test suite had come back. That is the first line of an unattended
turn, which is the line it plans against — so he was sent looking for a reminder that did not
exist. One caller was added and the sentence written for the other one was left alone.
"""

from __future__ import annotations

import pytest

from kith.kernel import session_context
from kith.services import scheduler


@pytest.fixture
def watching(monkeypatch):
    """Capture what each wake saw, from inside the continuation."""
    seen: list[dict] = []

    def resume(conversation_id: str, trigger: str) -> None:
        seen.append(
            {
                "conversation": conversation_id,
                "trigger": trigger,
                # Read *during* the turn, which is the only moment it means anything.
                "working_in": session_context.current(),
                "unattended": session_context.unattended(),
            }
        )

    monkeypatch.setattr(
        scheduler.processes,
        "finished_since_last_look",
        lambda: {"c-1": ["pytest finished: 412 passed"]},
    )
    return seen, resume


class TestWhereTheWokenTurnThinksItIs:
    def test_a_finished_process_wakes_the_turn_inside_its_conversation(self, watching):
        """The bug, in one line. Without this the whole turn runs with `current() == ""`."""
        seen, resume = watching
        assert scheduler.wake_finished(resume) == ["c-1"]
        assert seen[0]["working_in"] == "c-1"

    def test_and_nobody_is_watching_it(self, watching):
        """The other half of `fire_due`'s wrapping. A turn nobody asked for should not be
        drawing permission cards on a screen nobody is in front of."""
        seen, resume = watching
        scheduler.wake_finished(resume)
        assert seen[0]["unattended"] is True

    def test_the_context_does_not_leak_out_of_the_wake(self, watching):
        _, resume = watching
        scheduler.wake_finished(resume)
        assert session_context.current() == ""

    def test_one_conversation_failing_does_not_silence_the_rest(self, monkeypatch):
        """`fire_due` has had this guard for a while; adding the `with` without it would have
        made a single raising continuation swallow every other finished task in the batch."""
        monkeypatch.setattr(
            scheduler.processes,
            "finished_since_last_look",
            lambda: {"c-1": ["one"], "c-2": ["two"]},
        )
        reached: list[str] = []

        def resume(conversation_id: str, _trigger: str) -> None:
            reached.append(conversation_id)
            if conversation_id == "c-1":
                raise RuntimeError("the turn blew up")

        assert scheduler.wake_finished(resume) == ["c-1", "c-2"]
        assert reached == ["c-1", "c-2"]


class TestWhatItIsToldItIsFor:
    def test_a_finished_process_is_not_announced_as_a_reminder(self, watching):
        seen, resume = watching
        scheduler.wake_finished(resume)
        opening = seen[0]["trigger"]
        assert opening.startswith(scheduler.FINISHED)
        assert "reminder" not in opening.lower(), opening

    def test_the_note_itself_still_comes_through(self, watching):
        seen, resume = watching
        scheduler.wake_finished(resume)
        assert "- pytest finished: 412 passed" in seen[0]["trigger"]

    def test_a_reminder_is_still_announced_as_a_reminder(self):
        """The path that was already right stays right — this is a second sentence, not a
        replacement for the first."""
        said: list[str] = []
        scheduler._continue("c-9", ["water the plants"], lambda _c, t: said.append(t))
        assert said[0].startswith(scheduler.DUE)
        assert "- water the plants" in said[0]

    def test_both_openings_ask_for_the_same_shape_of_answer(self):
        """They differ in why he was woken and in nothing else. Two triggers that also
        disagreed about what to do with it would be two behaviours wearing one function."""
        tail = "briefly, the way you would mid-conversation"
        assert tail in scheduler.DUE and tail in scheduler.FINISHED
