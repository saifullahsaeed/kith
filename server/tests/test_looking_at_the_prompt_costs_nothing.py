"""Showing someone their own prompt, without sending or spending anything.

"What is in the context" has only ever been answerable in aggregate — the ledger's eleven
categories. A category is not the thing. "Code he has read: 260k" cannot say *which* messages
those are, in what order, or what any of them actually says, and those are the questions someone
opens a context screen to ask.

The list a turn sends is built by `prompt._build_messages`, and it folds on the way: a real fold
is a summarisation call. A screen you open in order to look at something must never spend money
to draw itself, and must never quietly rewrite the conversation it is describing.

So the preview shares the assembly and differs in exactly one thing — it is not allowed to pay.
`compact` already treats an empty summary as "leave the history alone", its own fail-safe for a
summariser that is down, so handing it one that always returns "" is the existing path rather
than a new one.
"""

from __future__ import annotations

import itertools

import pytest

from kith.services import conversations, history
from kith.services.turn import prompt

_next = itertools.count(1)


class _Config:
    """Just the fields the prompt build reads."""

    system = "You are Kith."
    context_window = 1_050_000
    api_key = ""
    base_url = ""
    think = False
    effort = ""
    num_predict = 0


class _Small(_Config):
    """A model with a small window, so the fold threshold is reachable in a few short turns.
    The budget is a share of the window, so this is the honest way to exercise it — the
    alternative is generating megabytes of fixture to out-grow a million-token model."""

    context_window = 4_000


@pytest.fixture
def no_summariser(monkeypatch):
    """A model call during a *preview* is the failure this file exists to catch, so make one
    impossible rather than merely unlikely."""

    def boom(*args, **kwargs):
        raise AssertionError("a preview asked a model for a summary")

    monkeypatch.setattr(history, "_summarize", boom)


def _conversation(turns: int = 3, size: int = 200) -> str:
    conversation_id = f"looking-at-the-prompt-{next(_next)}"
    for n in range(turns):
        conversations.record_event(
            conversation_id, "message", {"role": "user", "content": f"q{n} " + "x" * size}
        )
        conversations.record_event(
            conversation_id, "message", {"role": "assistant", "content": f"a{n} " + "y" * size}
        )
    return conversation_id


class TestItIsTheListATurnWouldSend:
    def test_a_short_conversation_previews_exactly_what_a_turn_builds(self, no_summariser):
        """The two go through the same assembly, so on a conversation with no fold due they are
        the same list — and if they ever stop being, the screen is describing a request that is
        never made."""
        conversation_id = _conversation()
        messages = conversations.full_messages(conversation_id)

        built = prompt._build_messages(messages, _Config(), conversation_id)
        shown, pending = prompt.as_sent(messages, _Config(), conversation_id)

        assert shown == built
        assert pending is False

    def test_the_persona_leads_it(self, no_summariser):
        conversation_id = _conversation()
        shown, _ = prompt.as_sent(conversations.full_messages(conversation_id), _Config(), conversation_id)

        assert shown[0]["role"] == "system"
        assert shown[0]["content"].startswith("You are Kith.")

    def test_what_is_true_right_now_comes_last(self, no_summariser):
        """The one region rewritten every turn, and therefore the one that must never sit inside
        the cached prefix. A preview that showed it anywhere else would be describing a prompt
        with different caching behaviour from the real one."""
        conversation_id = _conversation()
        shown, _ = prompt.as_sent(conversations.full_messages(conversation_id), _Config(), conversation_id)

        assert shown[-1].get("_live") is True


class TestItNeverPaysForWhatItShows:
    def test_a_conversation_long_enough_to_fold_still_makes_no_model_call(self, no_summariser):
        """The case that would otherwise cost money every time someone opened the screen.

        Driven by a small window rather than a huge conversation: the budget is a share of the
        model's window, so a 4,000-token model reaches the threshold on a few turns and the test
        stays fast and readable."""
        conversation_id = _conversation(turns=12, size=2_000)

        shown, pending = prompt.as_sent(
            conversations.full_messages(conversation_id), _Small(), conversation_id
        )

        assert shown, "still answers"
        assert pending is True, "and says the next real turn will fold before it sends"

    def test_it_writes_no_brief_to_the_transcript(self, no_summariser):
        """A preview that persisted a summary would be a screen that changes what it looks at."""
        conversation_id = _conversation(turns=12, size=2_000)
        assert conversations.latest_summary(conversation_id) == {}

        prompt.as_sent(conversations.full_messages(conversation_id), _Small(), conversation_id)

        assert conversations.latest_summary(conversation_id) == {}

    def test_a_conversation_under_the_budget_reports_no_fold_pending(self, no_summariser):
        conversation_id = _conversation(turns=2, size=100)

        _, pending = prompt.as_sent(conversations.full_messages(conversation_id), _Config(), conversation_id)

        assert pending is False


class TestTheDryFoldItself:
    def test_it_reuses_a_stored_brief_rather_than_declining(self, no_summariser):
        """A brief that still covers the tail is the ordinary state of any long conversation, and
        it is the case where the preview is byte-exact without paying anything."""
        conversation_id = _conversation(turns=30, size=2_000)
        messages = conversations.full_messages(conversation_id)
        # A brief covering everything but the last turn, as a real fold would have left.
        cut = max(i for i, m in enumerate(messages) if m.get("role") == "user")
        conversations.record_summary(conversation_id, cut, "They talked at length.")

        folded, pending = history.fold_dry(messages, _Config(), conversation_id)

        assert folded[0]["role"] == "system"
        assert "They talked at length." in folded[0]["content"]
        assert len(folded) < len(messages)
        assert pending is False, "the stored brief covers it — nothing is owed"
