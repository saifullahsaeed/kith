"""A question that holds the turn until it is answered.

Everything else that needs the person is asynchronous, and that was a constraint rather than a
preference: `permissions.py` says so in as many words — holding a tool call open means waiting
for a click that may never come, on a turn you may have walked away from. Both halves of that
stopped being true. A turn runs on its own thread and survives the window closing, and since
`live_turns` it can be watched again from wherever you come back to.

So these are about the waiting, not the shape of the payload. A question that returns
immediately is the bug, and it is the one the person reported about the permission popup:
"i see the popup but it dont wait, just move forward".
"""

from __future__ import annotations

import threading
import time

from kith.services import questions


def _asked(text: str = "Which way?") -> list[dict]:
    return [{"question": text, "options": ["Left", {"label": "Right", "description": "the other one"}]}]


class TestItWaits:
    def test_the_call_does_not_return_until_it_is_answered(self):
        got: list[dict] = []

        def turn():
            got.append(questions.ask("c1", _asked(), deadline=5))

        thread = threading.Thread(target=turn, daemon=True)
        thread.start()

        # Long enough that a non-blocking implementation would have finished and returned.
        time.sleep(0.3)
        assert got == [], "it came back without an answer"

        open_now = questions.open_question("c1")
        assert open_now is not None, "and nothing was on offer to answer"
        questions.answer(open_now["id"], [{"chosen": ["Right"]}])

        thread.join(timeout=5)
        assert got and got[0]["answered"] is True
        assert got[0]["answers"][0]["chosen"] == ["Right"]

    def test_free_text_comes_back_as_well_as_a_choice(self):
        """Both at once is the commonest real answer — an option picked with a caveat typed
        beside it."""
        got: list[dict] = []
        thread = threading.Thread(
            target=lambda: got.append(questions.ask("c2", _asked(), deadline=5)), daemon=True
        )
        thread.start()
        time.sleep(0.2)

        questions.answer(questions.open_question("c2")["id"], [{"chosen": ["Left"], "text": "but cheaper"}])
        thread.join(timeout=5)

        assert got[0]["answers"][0] == {"chosen": ["Left"], "text": "but cheaper", "skipped": False}

    def test_skipping_says_so_rather_than_pretending_to_be_an_answer(self):
        """He is told to decide and say why, which is a different instruction from being told
        what to do."""
        got: list[dict] = []
        thread = threading.Thread(
            target=lambda: got.append(questions.ask("c3", _asked(), deadline=5)), daemon=True
        )
        thread.start()
        time.sleep(0.2)

        questions.answer(questions.open_question("c3")["id"], [{"skipped": True}])
        thread.join(timeout=5)

        assert got[0]["answered"] is True
        assert got[0]["answers"][0]["skipped"] is True


class TestItAlwaysLetsGo:
    """Three ways out, because a thread parked forever is a leak whatever the reason."""

    def test_stopping_the_turn_releases_it(self):
        """Stop has to stop a turn that is waiting exactly as it stops one that is working —
        otherwise the button does nothing for fifteen minutes."""
        got: list[dict] = []
        thread = threading.Thread(
            target=lambda: got.append(questions.ask("c4", _asked(), deadline=30)), daemon=True
        )
        thread.start()
        time.sleep(0.2)

        questions.release("c4")
        thread.join(timeout=5)

        assert got and got[0]["answered"] is False
        assert "stopped" in got[0]["note"].lower()

    def test_it_gives_up_eventually(self):
        answer = questions.ask("c5", _asked(), deadline=0.2)
        assert answer["answered"] is False
        assert "answered" in answer["note"].lower()

    def test_nothing_is_left_open_afterwards(self):
        questions.ask("c6", _asked(), deadline=0.1)
        assert questions.open_question("c6") is None, "a finished question must not still be askable"


class TestWhatHeAsks:
    def test_a_bare_string_option_is_accepted(self):
        """A model asked for a list of choices writes plain strings as often as objects, and a
        tool that fails on the obvious call is a tool he stops reaching for."""
        thread_result: list[dict] = []
        thread = threading.Thread(
            target=lambda: thread_result.append(questions.ask("c7", _asked(), deadline=5)), daemon=True
        )
        thread.start()
        time.sleep(0.2)

        asked = questions.open_question("c7")["questions"][0]
        assert asked["options"] == [
            {"label": "Left", "description": ""},
            {"label": "Right", "description": "the other one"},
        ]
        questions.answer(questions.open_question("c7")["id"], [{"skipped": True}])
        thread.join(timeout=5)

    def test_a_question_with_no_options_is_refused_rather_than_hanging(self):
        """It would otherwise park the turn on a card with nothing to click."""
        answer = questions.ask("c8", [{"question": "well?"}], deadline=5)
        assert answer["ok"] is False
        assert questions.open_question("c8") is None

    def test_answering_something_that_is_no_longer_open_says_so(self):
        assert questions.answer("nope", [{"skipped": True}]) is False
