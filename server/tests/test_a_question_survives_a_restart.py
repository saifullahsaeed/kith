"""A question the process died holding is not a question that never happened.

`ask` parks the turn on an in-memory event, and the turn is a daemon thread. A restart took
both and wrote nothing down: the transcript stopped mid-tool-call, with no result, no error,
and no turn-log row. Measured on 2026-08-13 — a question asked at 11:33:15 with a fifteen-minute
deadline, a server that came up at 11:41:56, and a conversation file that simply ends. Five of
those across the transcripts.

Two of them were `remember this`, retyped by hand minutes later, and that is the second half of
the damage. `conversations.full_messages` has to drop a `tool_call` it cannot pair to a result —
a provider refuses the message otherwise — so the next turn could not see that it had ever
asked. He asked what to remember, the question evaporated, and the turn after had no idea.

The transcript is the persistence here, which is why there is no table: the `tool_call` is
written before the tool runs and carries the whole question, so a call with no result *is* the
record of an interrupted ask.
"""

from __future__ import annotations

import itertools
import json

import pytest

from kith.services import conversations, questions


@pytest.fixture(autouse=True)
def _no_questions_left_open():
    questions._OPEN.clear()
    yield
    questions._OPEN.clear()


#: One conversation per call. The transcript is a file in a session-scoped directory, so a
#: shared id means the second test to run finds the first one's records already in place — which
#: is exactly the flake this suite hit in `test_a_fold_you_asked_for_sticks` and is doubly wrong
#: here, where every assertion is about how many records a file has.
_next = itertools.count(1)


def _parked(prefix: str = "parked") -> str:
    """A transcript that ends exactly where a killed turn leaves one."""
    conversation_id = f"{prefix}-{next(_next)}"
    conversations.record_event(conversation_id, "message", {"role": "user", "content": "remember this"})
    conversations.record_event(conversation_id, "stats", {"stats": {"promptTokens": 10}})
    conversations.record_event(
        conversation_id,
        "tool_call",
        {
            "id": "c0",
            "name": "ask",
            "arguments": {
                "questions": [
                    {
                        "question": "What exactly should I remember?",
                        "options": [{"label": "A fact", "description": "A sentence to keep"}],
                    }
                ]
            },
        },
    )
    return conversation_id


def _entries(conversation_id: str) -> list[dict]:
    return conversations.read(conversation_id)


class TestFindingThem:
    def test_a_transcript_that_ends_on_an_unanswered_ask_is_found(self):
        conversation_id = _parked()
        assert conversation_id in conversations.interrupted_asks()

    def test_an_ask_that_got_its_answer_is_not(self):
        conversation_id = _parked("answered-properly")
        conversations.record_event(
            conversation_id, "tool_result", {"id": "c0", "name": "ask", "result": {"answered": True}}
        )
        assert conversation_id not in conversations.interrupted_asks()

    def test_nor_one_the_person_talked_past(self):
        """They carried on, so whatever was open is not what the conversation waits on."""
        conversation_id = _parked("they-moved-on")
        conversations.record_event(
            conversation_id, "message", {"role": "user", "content": "never mind, do the other thing"}
        )
        assert conversation_id not in conversations.interrupted_asks()

    def test_an_ordinary_conversation_is_not(self):
        conversations.record_event("just-talking", "message", {"role": "user", "content": "hello"})
        assert "just-talking" not in conversations.interrupted_asks()


class TestClosingTheBooks:
    def test_the_missing_result_is_written(self):
        """Until it is, `full_messages` drops the call and the next turn cannot see it asked."""
        conversation_id = _parked()
        questions.recover_interrupted()

        results = [e for e in _entries(conversation_id) if e.get("type") == "tool_result"]
        assert len(results) == 1, results
        assert results[0]["id"] == "c0"
        assert "restarted" in json.dumps(results[0]["result"])

    def test_the_next_turn_can_now_see_that_it_asked(self):
        """The whole point of writing the result, and the reason 'remember this' was typed twice."""
        conversation_id = _parked()
        before = conversations.full_messages(conversation_id)
        assert not [m for m in before if m.get("role") == "tool"], "an orphan call should be dropped"

        questions.recover_interrupted()

        after = conversations.full_messages(conversation_id)
        assert [m for m in after if m.get("role") == "tool"], "the ask is still invisible to the next turn"

    def test_recovering_twice_does_not_write_it_twice(self):
        """It runs on every start, and most starts follow a clean shutdown."""
        conversation_id = _parked()
        questions.recover_interrupted()
        questions.recover_interrupted()

        results = [e for e in _entries(conversation_id) if e.get("type") == "tool_result"]
        assert len(results) == 1, results


class TestPuttingTheCardBack:
    def test_the_question_is_open_again(self):
        conversation_id = _parked()
        # Not `== 1`: recovery sweeps every transcript in the folder, and the folder is shared
        # for the session, so a sibling test's parked conversation counts too.
        assert questions.recover_interrupted() >= 1

        found = questions.open_question(conversation_id)
        assert found is not None, "the card did not come back"
        assert found["questions"][0]["question"] == "What exactly should I remember?"

    def test_it_says_nothing_is_waiting_on_it(self):
        """The flag is what sends the answer as a message instead of into a dead thread."""
        conversation_id = _parked()
        questions.recover_interrupted()

        assert questions.open_question(conversation_id)["interrupted"] is True

    def test_a_live_question_is_never_overwritten(self):
        """A recovered one must not displace a turn actually parked in this process."""
        conversation_id = _parked()
        live = questions.Question(id="live", conversation_id=conversation_id, asked=[{"question": "?"}])
        questions._OPEN[conversation_id] = live

        questions.recover_interrupted()

        assert questions._OPEN[conversation_id] is live
        # And nothing was written into its transcript either. That turn is still running, so a
        # "the server restarted" result would be a lie — and it would pair off the very call the
        # live turn is about to answer for itself.
        assert not [e for e in _entries(conversation_id) if e.get("type") == "tool_result"]
