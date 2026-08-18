"""What the next turn sends, against what the last one did — and what moved between them.

A list of the prompt answers "what is in there". It does not answer the question people actually
have about context, which is "why does this keep growing, and what is doing it". That one is only
answerable by comparison: the prompt the last turn sent, the prompt the next turn will send, and
the messages that are the difference.

The difference is almost always the last turn's own work. One question produces an assistant
message, six tool calls and six tool results, and every one of them is in the prompt from then
on. Seeing that once explains more about context management than any total does.

**Both sides are computed, neither is stored.** Nothing records the literal message list a turn
sent. It does not need to: the prompt is a function of the transcript, so the last turn's prompt
is that same function over the transcript as it stood when that turn began — everything up to and
including the user message that started it. No model call on either side.

Two things this deliberately does not pretend to:

* the live block is rewritten every turn, so the reconstruction carries *today's*, not the one
  that actually went. It is flagged rather than diffed, since "rewritten every turn" is the true
  answer and is worth teaching;
* a fold since the last turn moves the boundary under both sides. Reported as what it is — the
  messages it removed show up as dropped, which is exactly what a fold did.
"""

from __future__ import annotations

import itertools

import flask
import pytest

from kith.api.routes import chat
from kith.services import conversations

_next = itertools.count(1)


@pytest.fixture
def app_context():
    with flask.Flask(__name__).app_context():
        yield


def _ask(conversation_id: str, text: str) -> None:
    conversations.record_event(conversation_id, "message", {"role": "user", "content": text})


def _reply(conversation_id: str, text: str) -> None:
    conversations.record_event(conversation_id, "message", {"role": "assistant", "content": text})


def _tool(conversation_id: str, name: str, path: str, size: int = 1_500) -> None:
    call = f"c{next(_next)}"
    conversations.record_event(
        conversation_id, "tool_call", {"id": call, "name": name, "arguments": {"path": path}}
    )
    conversations.record_event(
        conversation_id, "tool_result", {"id": call, "name": name, "result": "x" * size}
    )


def _conversation() -> str:
    """One finished turn, then a second turn that did real work."""
    conversation_id = f"what-the-turn-added-{next(_next)}"
    _ask(conversation_id, "hello")
    _reply(conversation_id, "Hey.")
    _ask(conversation_id, "have a look at the settings page")
    _tool(conversation_id, "read_file", "src/settings.tsx")
    _tool(conversation_id, "read_file", "src/app.tsx")
    _reply(conversation_id, "Read both.")
    return conversation_id


def _sent(conversation_id: str) -> dict:
    return chat.context_detail(conversation_id).get_json()["sent"]


class TestItSaysWhatTheLastTurnAdded:
    def test_the_tool_results_that_turn_produced_are_marked_new(self, app_context):
        """The whole lesson in one row: you asked one question, and two file reads are now in
        the prompt for the rest of the conversation."""
        added = [m for m in _sent(_conversation())["messages"] if m["change"] == "added"]

        assert [m["tool"] for m in added if m["role"] == "tool"] == ["read_file", "read_file"]

    def test_what_was_already_there_is_marked_kept(self, app_context):
        kept = [m for m in _sent(_conversation())["messages"] if m["change"] == "kept"]

        assert any(m["role"] == "user" for m in kept), "the earlier question is still in the prompt"

    def test_it_totals_both_sides_so_the_growth_has_a_number(self, app_context):
        sent = _sent(_conversation())

        assert sent["previousTokens"] > 0
        assert sent["tokens"] > sent["previousTokens"]
        assert sent["addedTokens"] == sum(m["tokens"] for m in sent["messages"] if m["change"] == "added")

    def test_the_added_tokens_are_the_difference_the_person_can_see(self, app_context):
        """Growth, minus the part nothing can attribute — the live block, which is rewritten
        rather than added. Without that carve-out the arithmetic never quite closes and the
        screen looks wrong for a reason nobody can find."""
        sent = _sent(_conversation())

        live = sum(m["tokens"] for m in sent["messages"] if m["live"])
        previous_live = sent["previousLiveTokens"]
        assert sent["tokens"] - sent["previousTokens"] == sent["addedTokens"] + live - previous_live


class TestTheLiveBlockIsNotDiffed:
    def test_it_is_flagged_rather_than_called_new_or_kept(self, app_context):
        """It is neither. It is rewritten every turn — that is the honest answer and the one
        worth teaching, since it is why the tail of a prompt is never cached."""
        live = [m for m in _sent(_conversation())["messages"] if m["live"]]

        assert len(live) == 1
        assert live[0]["change"] == "rewritten"


class TestAConversationWithNothingToCompare:
    def test_a_first_turn_has_no_previous_prompt(self, app_context):
        """Nothing has been sent yet, so everything is new — and saying "+100%" against a
        previous turn that never happened would be inventing a comparison."""
        conversation_id = f"first-turn-{next(_next)}"
        _ask(conversation_id, "hello")

        sent = _sent(conversation_id)

        assert sent["hasPrevious"] is False
        assert sent["previousTokens"] == 0

    def test_a_turn_still_in_flight_compares_against_the_one_before_it(self, app_context):
        """The user message that started the turn in progress is itself part of what is new."""
        conversation_id = _conversation()
        _ask(conversation_id, "and now the header")

        sent = _sent(conversation_id)

        assert sent["hasPrevious"] is True
        newest = [m for m in sent["messages"] if m["change"] == "added"]
        assert any("and now the header" in m["preview"] for m in newest)


class TestItStillDescribesTheSameListItLists:
    def test_every_message_carries_a_change(self, app_context):
        """A row with no marker is a row the screen cannot explain."""
        messages = _sent(_conversation())["messages"]

        assert all(m["change"] in {"kept", "added", "rewritten"} for m in messages)

    def test_the_dropped_messages_are_reported_separately(self, app_context):
        """Nothing was folded here, so nothing is dropped — but the field is always present, so
        the screen never has to guess whether an empty list means "none" or "not measured"."""
        sent = _sent(_conversation())

        assert sent["dropped"] == []
