"""Turning command-line arguments into the replies `ask` is waiting for.

`questions.answer` sets every reply on the Question and releases the parked turn in one call,
so **the first answer closes the whole thing** — there is no second call that picks up the
questions you did not get to. An earlier version of this command answered question one,
skipped the rest, and justified it in a comment by saying you could run the command again.
You could not. The agent found it by reading the file, which is a better review than this had
before it.

So the mapping from arguments to replies has to cover every question in one go, and these are
the cases that decide whether it does.
"""

from __future__ import annotations

import pytest

from kith.cli.commands.questions import _by_number
from kith.cli.errors import Failure


def _question(*labels: str) -> dict:
    return {"question": "which?", "options": [{"label": label} for label in labels]}


def test_one_number_per_question_in_order():
    questions = [_question("a", "b"), _question("c", "d", "e")]
    replies = _by_number(["1", "3"], questions)
    assert [r["chosen"] for r in replies] == [["a"], ["e"]]
    assert not any(r["skipped"] for r in replies)


def test_commas_pick_several_within_one_question():
    """`multiSelect` is a real flag on the tool, and a question that takes several answers
    cannot be expressed by a bare number."""
    replies = _by_number(["1,3"], [_question("a", "b", "c")])
    assert replies[0]["chosen"] == ["a", "c"]


def test_space_separates_questions_and_comma_separates_choices():
    questions = [_question("a", "b", "c"), _question("x", "y")]
    replies = _by_number(["1,2", "2"], questions)
    assert [r["chosen"] for r in replies] == [["a", "b"], ["y"]]


def test_questions_you_did_not_answer_are_skipped_explicitly():
    """Not merely left off. `ask` reads a missing reply as no reply, so a short list would
    silently answer nothing for the rest — after the command had already said "answered"."""
    questions = [_question("a"), _question("x"), _question("p")]
    replies = _by_number(["1"], questions)
    assert len(replies) == 3
    assert replies[0]["chosen"] == ["a"]
    assert replies[1]["skipped"] and replies[2]["skipped"]


def test_more_answers_than_questions_is_refused():
    """The ambiguous case, and the one where guessing is worst: two numbers against one
    question could mean "options 1 and 3" or "you have miscounted", and picking the first
    silently turns a typo into a multi-select answer."""
    with pytest.raises(Failure) as refused:
        _by_number(["1", "2"], [_question("a", "b")])
    assert "1 question" in str(refused.value)


def test_an_option_that_does_not_exist_says_which_question():
    with pytest.raises(Failure) as refused:
        _by_number(["1", "9"], [_question("a"), _question("x", "y")])
    assert "question 2" in str(refused.value)


def test_a_single_question_still_works_the_obvious_way():
    replies = _by_number(["2"], [_question("a", "b")])
    assert replies == [{"chosen": ["b"], "text": "", "skipped": False}]
