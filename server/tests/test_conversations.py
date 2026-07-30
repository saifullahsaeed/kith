"""Conversations, and the promise that nothing is lost.

The transcript is append-only plain text because that is what makes "I don't want to lose
any info" a property of the format rather than a hope. These tests are mostly about the
file: that it survives a mangled line, that the index can be wrong without the words
being wrong, and that removing a conversation from a list does not delete an afternoon.
"""

from __future__ import annotations

import pytest

from kith.services import conversations


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    """Transcripts under a temp folder, not the real ~/Kith."""
    monkeypatch.setattr("kith.settings.WORKSPACE_DIR", str(tmp_path))
    from kith.infra import workspace as module

    monkeypatch.setattr(module.settings, "WORKSPACE_DIR", str(tmp_path))
    yield tmp_path


class TestStarting:
    def test_a_conversation_gets_an_id_a_session_and_a_file(self, db):
        opened = conversations.start(db, "Hello there")
        assert opened["id"]
        assert opened["sessionId"].startswith("kith-")
        assert conversations.transcript_path(opened["id"]).exists()

    def test_the_id_sorts_chronologically_by_name(self, db):
        """So the folder reads in order in Finder, not just through the app."""
        first = conversations.start(db, "one")["id"]
        second = conversations.start(db, "two")["id"]
        assert sorted([second, first]) == [first, second]

    def test_the_session_id_is_per_conversation(self, db):
        """One id per install had unrelated chats fighting over the same warm cache."""
        a = conversations.start(db, "a")
        b = conversations.start(db, "b")
        assert a["sessionId"] != b["sessionId"]

    def test_the_title_comes_from_the_first_thing_said(self, db):
        assert conversations.start(db, "Fix the invoice script")["title"] == "Fix the invoice script"

    def test_a_long_first_message_is_cut_on_a_word(self, db):
        long = "Please look into the quarterly numbers and tell me what changed since June"
        title = conversations.title_from(long)
        assert len(title) <= conversations.TITLE_CHARS + 1
        assert not title.rstrip("…").endswith(" ")


class TestRecording:
    def test_messages_land_in_the_transcript_and_come_back(self, db):
        opened = conversations.start(db)
        conversations.record(db, opened["id"], "user", "what is 2+2")
        conversations.record(db, opened["id"], "assistant", "4")
        assert conversations.messages(opened["id"]) == [
            {"role": "user", "content": "what is 2+2"},
            {"role": "assistant", "content": "4"},
        ]

    def test_a_placeholder_title_is_replaced_by_the_first_real_message(self, db):
        opened = conversations.start(db)
        assert opened["title"] == "New conversation"
        conversations.record(db, opened["id"], "user", "Draft the update")
        assert conversations.get(db, opened["id"])["title"] == "Draft the update"

    def test_a_later_message_does_not_rename_it(self, db):
        opened = conversations.start(db, "First subject")
        conversations.record(db, opened["id"], "user", "something else entirely")
        assert conversations.get(db, opened["id"])["title"] == "First subject"

    def test_events_are_kept_but_not_counted_as_messages(self, db):
        """ "12 messages" should mean what a person would count."""
        opened = conversations.start(db)
        conversations.record(db, opened["id"], "user", "go")
        conversations.record_event(opened["id"], "tool_call", {"name": "shell"})
        assert conversations.get(db, opened["id"])["messages"] == 1
        assert any(entry["type"] == "tool_call" for entry in conversations.read(opened["id"]))

    def test_tool_results_are_not_replayed_as_conversation(self, db):
        """A tool result from an hour ago is not a fact about now."""
        opened = conversations.start(db)
        conversations.record(db, opened["id"], "user", "go")
        conversations.record_event(opened["id"], "tool_result", {"result": "stale"})
        assert [m["content"] for m in conversations.messages(opened["id"])] == ["go"]

    def test_recording_without_an_id_is_a_no_op_not_a_crash(self, db):
        conversations.record(db, "", "user", "nowhere")  # must not raise


class TestTheFileSurvivesThings:
    def test_a_mangled_line_costs_that_line_only(self, db):
        """What a crash mid-append looks like. The whole reason this is JSONL."""
        opened = conversations.start(db)
        conversations.record(db, opened["id"], "user", "before")
        with conversations.transcript_path(opened["id"]).open("a") as handle:
            handle.write('{"type": "message", "role": "user", "content": "trunca\n')
        conversations.record(db, opened["id"], "assistant", "after")
        contents = [m["content"] for m in conversations.messages(opened["id"])]
        assert contents == ["before", "after"]

    def test_reading_a_conversation_with_no_file_is_empty_not_an_error(self):
        assert conversations.read("nope-not-here") == []

    def test_removing_it_from_the_list_keeps_the_words(self, db):
        opened = conversations.start(db, "keep me")
        conversations.record(db, opened["id"], "user", "the work")
        conversations.delete(db, opened["id"])
        with pytest.raises(KeyError):
            conversations.get(db, opened["id"])
        assert conversations.messages(opened["id"]) == [{"role": "user", "content": "the work"}]

    def test_purging_is_a_separate_deliberate_act(self, db):
        opened = conversations.start(db, "goodbye")
        conversations.delete(db, opened["id"], keep_file=False)
        assert not conversations.transcript_path(opened["id"]).exists()


class TestListing:
    def test_newest_first(self, db):
        first = conversations.start(db, "older")["id"]
        second = conversations.start(db, "newer")["id"]
        conversations.record(db, second, "user", "touch")
        assert next(row["id"] for row in conversations.recent(db)) == second
        assert first in [row["id"] for row in conversations.recent(db)]

    def test_storage_reports_what_is_on_disk(self, db):
        conversations.start(db, "one")
        conversations.start(db, "two")
        report = conversations.storage()
        assert report["files"] == 2
        assert report["bytes"] > 0


class TestSearchingWhatWasSaid:
    """Finding a conversation by what happened in it, not by how it opened.

    Nothing could do this. The control panel's box filters whichever tab is on screen, and
    the tab that held messages is gone — so the record kept most carefully was the one
    thing with no way in. Titles are generated from a first message, which means without
    this a conversation is findable by its opening line and by nothing else in it.
    """

    def test_a_word_from_the_middle_of_a_conversation_finds_it(self, db):
        first = conversations.start(db, "planning the week")
        conversations.record(db, first["id"], "user", "can you look at the invoice from Acme")
        conversations.start(db, "something unrelated")

        hits = conversations.search(db, "acme")

        assert [hit["conversationId"] for hit in hits] == [first["id"]]
        assert "Acme" in hits[0]["snippet"]

    def test_it_says_who_said_it(self, db):
        one = conversations.start(db, "hello")
        conversations.record(db, one["id"], "assistant", "I found the missing receipt")

        hits = conversations.search(db, "receipt")

        # "Did I ask for that or did he offer it" is most of why anyone is looking.
        assert hits[0]["role"] == "assistant"

    def test_case_does_not_matter(self, db):
        one = conversations.start(db, "hello")
        conversations.record(db, one["id"], "user", "Check the QUARTERLY numbers")
        assert len(conversations.search(db, "quarterly")) == 1

    def test_one_hit_per_conversation(self, db):
        one = conversations.start(db, "hello")
        for _ in range(5):
            conversations.record(db, one["id"], "user", "budget budget budget")

        # Five matches from one conversation would bury every other conversation that also
        # has one, and the list exists to get you to the right conversation.
        assert len(conversations.search(db, "budget")) == 1

    def test_newest_conversation_first(self, db):
        older = conversations.start(db, "older")
        conversations.record(db, older["id"], "user", "the mango report")
        newer = conversations.start(db, "newer")
        conversations.record(db, newer["id"], "user", "the mango report again")

        hits = conversations.search(db, "mango")

        assert [hit["conversationId"] for hit in hits] == [newer["id"], older["id"]]

    def test_an_empty_query_finds_nothing_rather_than_everything(self, db):
        one = conversations.start(db, "hello")
        conversations.record(db, one["id"], "user", "anything")
        assert conversations.search(db, "   ") == []

    def test_the_limit_is_respected(self, db):
        for index in range(6):
            one = conversations.start(db, f"chat {index}")
            conversations.record(db, one["id"], "user", "shared word")
        assert len(conversations.search(db, "shared", limit=3)) == 3

    def test_reasoning_and_tool_calls_are_not_searched(self, db):
        one = conversations.start(db, "hello")
        conversations.record_event(one["id"], "tool_call", {"name": "shell", "arguments": {"command": "rg zebra"}})

        # Matching these would answer "where did we talk about X" with a stack trace, and
        # the tool call is not something either of you said.
        assert conversations.search(db, "zebra") == []

    def test_a_missing_transcript_is_skipped_rather_than_raising(self, db):
        one = conversations.start(db, "hello")
        conversations.record(db, one["id"], "user", "findable")
        conversations.transcript_path(one["id"]).unlink()

        # The index can outlive the file — that is the whole reason the files are the
        # record. A search must not die because one row points at nothing.
        assert conversations.search(db, "findable") == []

    def test_a_mangled_line_does_not_stop_the_search(self, db):
        one = conversations.start(db, "hello")
        conversations.record(db, one["id"], "user", "before the damage")
        with conversations.transcript_path(one["id"]).open("a") as handle:
            handle.write("{not json at all\n")
        conversations.record(db, one["id"], "user", "after the damage")

        assert len(conversations.search(db, "after the damage")) == 1


class TestSnippets:
    def test_a_long_line_is_cut_around_the_match(self, db):
        one = conversations.start(db, "hello")
        conversations.record(db, one["id"], "user", "x" * 400 + " needle " + "y" * 400)

        snippet = conversations.search(db, "needle")[0]["snippet"]

        assert "needle" in snippet
        assert len(snippet) < 250
        assert snippet.startswith("…") and snippet.endswith("…")

    def test_newlines_are_flattened(self, db):
        one = conversations.start(db, "hello")
        conversations.record(db, one["id"], "user", "first line\n\nthen the keyword here")

        snippet = conversations.search(db, "keyword")[0]["snippet"]

        # A result row is one line high. A snippet with newlines in it either breaks the
        # row or gets silently clipped, and the offset of the match moves when whitespace
        # collapses — which put the window in the wrong place until it was re-found.
        assert "\n" not in snippet
        assert "keyword" in snippet
