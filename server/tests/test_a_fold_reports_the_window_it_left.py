"""`/fold` has to say how full the window is afterwards, because nothing else will.

A context reading has only ever existed as something a turn takes on its way past: the loop
measures the request it is about to send, records the last one, and the meter shows that. A fold
is not a turn — it rewrites the stored history over a POST and answers — so the figure on screen
went on describing a conversation that no longer existed. Asked to fold at 58% of a 1.05M window,
the meter stayed at 58%, which reads as the command having done nothing at all.

The route does not rebuild the request to find out. It adjusts the last real reading by what
actually moved, which is why these tests are mostly about arithmetic staying honest: the untouched
categories must survive untouched, the brief the fold *wrote* has to be charged for, and the
characters removed have to be converted at the ratio that reading was costed with rather than at
the seed guess.
"""

from __future__ import annotations

import pytest

from kith.api.routes.chat import _reading_after_fold
from kith.llm import ledger
from kith.services import conversations

#: Deliberately not the seed. A fold removes hundreds of thousands of characters, so the ratio
#: they are divided by is the difference between the meter moving by the right amount and the
#: meter merely moving.
RATIO = 5.0


def _recorded(window: int = 1_000_000, **lines: int) -> dict:
    """A reading as a turn would have left it: tokens by category, plus its own ratio."""
    return {
        "window": window,
        "used": sum(lines.values()),
        "free": window - sum(lines.values()),
        "share": round(sum(lines.values()) / window, 4),
        "charsPerToken": RATIO,
        "lines": [{"key": key, "label": key, "tokens": tokens} for key, tokens in lines.items()],
    }


def _convo(code_chars: int = 0, said_chars: int = 0) -> ledger.Ledger:
    """The stored conversation, measured in characters — what the route hands the adjuster."""
    messages = []
    if code_chars:
        messages.append({"role": "tool", "tool_name": "read_file", "content": "x" * code_chars})
    if said_chars:
        messages.append({"role": "user", "content": "s" * said_chars})
    return ledger.take(messages, chars_per_token=1.0)


class TestWhatTheFoldRemoved:
    def test_the_window_falls_by_what_left_it(self):
        conversation = "c1"
        conversations.record_event(conversation, "context", {"context": _recorded(code=100_000)})

        after = _reading_after_fold(conversation, _convo(code_chars=500_000), _convo(code_chars=0))

        assert after is not None
        # 500,000 characters at 5 chars per token is 100,000 tokens, which was all of it.
        assert after["used"] == 0
        assert after["lines"] == []

    def test_it_converts_at_the_ratio_the_reading_was_taken_with(self):
        """The seed would halve the removal here and leave the meter reading twice too high."""
        conversation = "c2"
        conversations.record_event(conversation, "context", {"context": _recorded(code=100_000)})

        after = _reading_after_fold(conversation, _convo(code_chars=250_000), _convo(code_chars=0))

        assert after is not None
        # 250,000 / 5 = 50,000 removed, so half the category survives. At the 4.0 seed it would
        # be 62,500 removed and 37,500 left — a number that never described anything.
        #
        # Approximate by a few tokens because a ledger measures the *message*, not the string
        # inside it: the role and the JSON around it are context too, and they are charged for.
        assert after["used"] == pytest.approx(50_000, abs=50)

    def test_the_categories_a_fold_cannot_touch_are_carried_across(self):
        """Persona, system prompt and tool schemas are not in the conversation and do not move.

        They are re-derived nowhere: carrying them is the reason this adjusts a reading instead
        of taking a new one, and a fold that quietly dropped them would report a window far
        emptier than the next turn is about to find.
        """
        conversation = "c3"
        conversations.record_event(
            conversation,
            "context",
            {"context": _recorded(persona=6_000, built_in_tools=14_000, code=100_000)},
        )

        after = _reading_after_fold(conversation, _convo(code_chars=500_000), _convo(code_chars=0))

        assert after is not None
        surviving = {line["key"]: line["tokens"] for line in after["lines"]}
        assert surviving == {"persona": 6_000, "built_in_tools": 14_000}
        assert after["used"] == 20_000
        assert after["free"] == 1_000_000 - 20_000

    def test_the_brief_the_fold_wrote_is_charged_for(self):
        """A fold does not only remove. It replaces what it removed with a summary, and that
        summary is context like any other — counted in whichever category it lands in, even one
        that held nothing before. Ignoring it reads the window low by the size of the brief, and
        the next turn then appears to grow out of nowhere."""
        conversation = "c4"
        conversations.record_event(conversation, "context", {"context": _recorded(code=100_000)})

        after = _reading_after_fold(
            conversation,
            _convo(code_chars=500_000),
            _convo(code_chars=0, said_chars=15_000),
        )

        assert after is not None
        surviving = {line["key"]: line["tokens"] for line in after["lines"]}
        # The code is gone; 15,000 characters of brief arrived at 5 chars per token.
        assert set(surviving) == {"messages"}
        assert surviving["messages"] == pytest.approx(3_000, abs=50)

    def test_a_line_cannot_go_negative(self):
        """The stored conversation and the last reading are measured at different moments — a
        turn may have run since — so the subtraction can overshoot. Better a category that reads
        zero than a meter reporting a window with less than nothing in it."""
        conversation = "c5"
        conversations.record_event(conversation, "context", {"context": _recorded(code=1_000)})

        after = _reading_after_fold(conversation, _convo(code_chars=500_000), _convo(code_chars=0))

        assert after is not None
        assert after["used"] == 0


class TestWhenThereIsNothingToAdjust:
    def test_a_conversation_that_has_never_had_a_turn_reports_nothing(self):
        """`None`, not a guess. There is no measurement to adjust, and the first reading of a
        conversation belongs to the turn that takes it."""
        assert _reading_after_fold("never-ran", _convo(code_chars=100), _convo()) is None

    def test_a_reading_recorded_before_the_ratio_was_sent_falls_back_to_the_seed(self):
        """Old transcripts carry no `charsPerToken`. The seed is a guess, and it is the honest
        one available — what must not happen is the adjustment being skipped altogether."""
        conversation = "c6"
        stale = _recorded(code=100_000)
        del stale["charsPerToken"]
        conversations.record_event(conversation, "context", {"context": stale})

        after = _reading_after_fold(conversation, _convo(code_chars=200_000), _convo(code_chars=0))

        assert after is not None
        assert after["used"] < 100_000

    def test_the_baseline_stands_in_when_a_turn_recorded_no_context(self):
        """`baseline` is what a turn carried in; a reading that predates `context` still has it,
        and it is a truer starting point than refusing to answer."""
        conversation = "c7"
        conversations.record_event(conversation, "context", {"baseline": _recorded(code=100_000)})

        after = _reading_after_fold(conversation, _convo(code_chars=500_000), _convo(code_chars=0))

        assert after is not None
        assert after["used"] == 0
