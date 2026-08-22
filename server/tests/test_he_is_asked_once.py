"""What the person said reaches the model once.

A message is written to the transcript the moment it arrives, before anything can fail — and the
turn then reads the conversation back off disk, because the client's copy is prose-only and the
real history has the tool calls in it. Both of those are right. Together they meant the newest
message was in the list twice: once because it had just been recorded, and once more because the
request appended it again to keep the attachment riding on it.

Nothing failed. The prompt simply had the person saying the same thing twice, back to back, on
every turn after the first — and paid for it on the one part of the prompt that is never cached.

The test that covers this path already existed and could not see it: it stubs `record` and
`full_messages` both, so the two halves that disagree were replaced by ones that agree. These
tests use the real transcript.
"""

from __future__ import annotations

import itertools

import pytest

from kith.api.routes import chat as route
from kith.services import conversations

#: One id per test. The data directory is session-scoped and a transcript is a file, so a shared
#: id means the second test to run reads the first one's turns — see the note in
#: `test_a_fold_you_asked_for_sticks.py` for the afternoon that cost.
_next = itertools.count(1)


@pytest.fixture
def conversation(db, monkeypatch):
    """A conversation with one finished turn in it, on a real transcript."""
    monkeypatch.setattr(route, "AGENT_DB_PATH", db)
    conversation_id = f"asked-once-{next(_next)}"
    conversations.record(db, conversation_id, "user", "what is the plan")
    conversations.record(db, conversation_id, "assistant", "we start with the spine")
    return conversation_id


def _users(messages: list[dict]) -> list[str]:
    return [str(m.get("content") or "") for m in messages if m.get("role") == "user"]


def _woken(messages: list[dict]) -> list[str]:
    """What the harness said to open a turn — a reminder firing, a background task coming back."""
    return [str(m.get("content") or "") for m in messages if m.get("role") == "system"]


class TestTheNewestMessageIsNotDoubled:
    def test_a_recorded_message_is_not_added_again(self, db, conversation):
        conversations.record(db, conversation, "user", "and then what")

        history = route._history_for_turn(conversation, {"role": "user", "content": "and then what"})

        assert _users(history) == ["what is the plan", "and then what"]

    def test_what_the_message_carries_survives_the_merge(self, db, conversation):
        """The reason the second copy was there at all.

        `record` writes text and nothing else, and `full_messages` rebuilds from role and content
        — so an attachment or a canvas reading exists only on the request's own copy. Merging is
        what keeps it without duplicating the words it came with.
        """
        conversations.record(db, conversation, "user", "look at this")

        history = route._history_for_turn(
            conversation,
            {
                "role": "user",
                "content": "look at this",
                "attachments": [{"name": "log.txt", "kind": "file"}],
                "canvas": [{"title": "tuner", "values": {"foldAt": 5}}],
            },
        )

        assert _users(history) == ["what is the plan", "look at this"]
        assert history[-1]["attachments"] == [{"name": "log.txt", "kind": "file"}]
        assert history[-1]["canvas"] == [{"title": "tuner", "values": {"foldAt": 5}}]

    def test_a_message_that_never_reached_disk_is_still_asked(self, conversation):
        """`record` swallows an OSError rather than take a turn down with it.

        A turn missing the thing it was asked is worse than a turn asked twice, so the tail not
        matching means append — the case this is deliberately not clever about.
        """
        history = route._history_for_turn(conversation, {"role": "user", "content": "never written"})

        assert _users(history) == ["what is the plan", "never written"]

    def test_an_unattended_turn_is_asked_once_too(self, db, monkeypatch):
        """The scheduler's path had the same shape: record the trigger, then append it again.

        Asked as `system` now, not `user`. The duplication this test is about is unchanged — it is
        the role that moved, because nobody types "one of your reminders just fired" and recording
        it as though they had put a scheduler's prose in the person's mouth: in their bubble on
        reload, and in the prompt as their words on every turn afterwards.
        """
        monkeypatch.setattr(route, "AGENT_DB_PATH", db)
        conversation_id = f"asked-once-{next(_next)}"
        started: dict = {}
        monkeypatch.setattr(
            route,
            "begin_turn",
            lambda cid, config, gather, opening="": started.setdefault("messages", gather()) and None,
        )
        monkeypatch.setattr(route.live_turns, "watch", lambda live: iter(()))

        route.continue_conversation(conversation_id, "a build finished")

        assert _woken(started["messages"]) == ["a build finished"]
        assert _users(started["messages"]) == [], "a wake is not something the person said"
