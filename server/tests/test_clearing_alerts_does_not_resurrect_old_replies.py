"""Clearing the alerts panel must not put months-old replies back in front of him.

The panel can now empty itself, which is the obvious thing to want from a list of a hundred
notes. The non-obvious part is that `pending_user_messages` — the thing that tells him "they
wrote to you and haven't heard back", every turn — decides that by comparing ids against his
last word. Delete his last word and the high-water mark drops, and every reply he answered
weeks ago is suddenly above it again: he re-reads them as new, and answers them a second
time. A tidy-up in the UI would have quietly changed what he thinks he owes.

So these tests are about the clear leaving that question answered the same way it was
answered before. The one about the count is smaller but is the reason the button can name a
number at all.
"""

from __future__ import annotations

import pytest

from kith.infra import notify
from kith.infra.db import repositories as repo


@pytest.fixture(autouse=True)
def no_doorbell(monkeypatch):
    """Never post a real desktop notification from the suite."""
    monkeypatch.setattr(notify, "announce", lambda *a, **k: False)


def _pending(db) -> list[str]:
    return [m["body"] for m in repo.messages.pending_user_messages(db)]


class TestClearingKeepsWhatHeStillOwes:
    def test_an_answered_reply_stays_answered(self, db):
        repo.messages.add_message(db, "old note", kind="note")
        repo.messages.add_message(db, "their old reply", sender="user")
        repo.messages.add_message(db, "his answer", kind="reachout")
        assert _pending(db) == [], "he had already replied — nothing should be pending"

        repo.messages.delete_messages(db)

        assert _pending(db) == [], (
            "clearing the panel made a reply he answered look unanswered again — he will "
            "read it next turn as if it had just arrived"
        )

    def test_a_reply_he_has_not_answered_survives_the_clear(self, db):
        repo.messages.add_message(db, "a note", kind="note")
        repo.messages.add_message(db, "have you seen this?", sender="user")
        assert _pending(db) == ["have you seen this?"]

        repo.messages.delete_messages(db)

        assert _pending(db) == ["have you seen this?"], (
            "emptying his outbox threw away a question of theirs he had not answered"
        )

    def test_clearing_one_kind_leaves_the_rest_alone(self, db):
        repo.messages.add_message(db, "note one", kind="note")
        repo.messages.add_message(db, "I need your input", kind="asked")
        repo.messages.add_message(db, "note two", kind="note")

        assert repo.messages.delete_messages(db, ["note"]) == 2

        left = [m["kind"] for m in repo.messages.list_messages(db)]
        assert left == ["asked"]

    def test_clearing_the_numerous_kind_still_holds_the_line(self, db):
        """The dangerous shape: the newest thing he said is a note, and a reply sits above it."""
        repo.messages.add_message(db, "his answer", kind="reachout")
        repo.messages.add_message(db, "their old reply", sender="user")
        repo.messages.add_message(db, "a note while working", kind="note")
        repo.messages.add_message(db, "and another thing", sender="user")
        assert _pending(db) == ["and another thing"]

        repo.messages.delete_messages(db, ["note"])

        assert _pending(db) == ["and another thing"]

    def test_clearing_nothing_deletes_nothing(self, db):
        repo.messages.add_message(db, "a note", kind="note")
        assert repo.messages.delete_messages(db, ["delivered"]) == 0
        assert len(repo.messages.list_messages(db)) == 1


class TestTheCountOnTheButton:
    def test_it_counts_the_whole_channel_not_a_page(self, db):
        for i in range(120):
            repo.messages.add_message(db, f"note {i}", kind="note")
        repo.messages.add_message(db, "a question", kind="asked")
        repo.messages.add_message(db, "their reply", sender="user")

        counts = repo.messages.count_messages_by_kind(db)

        assert counts["note"] == 120, "the page limit was doing the counting"
        assert counts["asked"] == 1
        assert "user" not in counts, "your own replies are not his alerts"
